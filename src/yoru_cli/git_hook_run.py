"""`yoru git-hook-run <post-commit|pre-push>` — the process a paired repo's
git hooks (see `git_hooks.py`) shell out to.

Thin by construction (review-cli-tool §1 / ruling D6.3): this module never
makes a network call. It resolves the active identity, builds one
CanonicalEvent-shaped JSON body from local `git` state, and writes it to the
identity's spool dir (`config.ensure_git_spool_dir()`). The shared tailer
(`transcript_tailer._drain_git_spool`) POSTs spooled files on its normal poll
loop and deletes them on success. A commit or push therefore never waits on
the network, matching the CC hook's "never block" guarantee by construction
rather than by a timeout.

Every failure mode here is a silent no-op (bare `except Exception: return 0`
at the top level) — a hook that can fail the user's `git commit`/`git push`
on any capture-path bug would be worse than capturing nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from . import config

# Must stay <= the backend's EventIn.diff_unified/diff_stat max_length
# (models.py) or a compliant client's own payload 422s at ingest — caught by
# review round 1: diff_stat was capped at the diff's 200_000 while the
# backend bounds it to 8_000 (a wide commit's --stat summary, one line per
# file, blew past that long before the diff text ever would).
_MAX_DIFF_CHARS = 200_000
_MAX_STAT_CHARS = 8_000


def _run_git(args: list[str], cwd: str) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, timeout=10
        )
        return out.decode("utf-8", errors="replace").strip() or None
    except Exception:
        return None


_TRUNCATION_SUFFIX = "\n...[truncated]"


def _cap(text: str | None, limit: int = _MAX_DIFF_CHARS) -> str | None:
    """Truncates so the RESULT (text + suffix) never exceeds `limit` — the
    backend's max_length is a hard cap, so appending a suffix after slicing
    to `limit` (the round-1 bug) can push the total past it."""
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX


def _spool(event: dict[str, Any]) -> None:
    spool_dir = config.ensure_git_spool_dir()
    name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}.json"
    path = spool_dir / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(event, f)


def _session_id_for_repo(repo_root: str) -> str:
    """Stable per-repo session id so commits from the same repo group into
    one ongoing independent-capture session, instead of one session per
    commit. Deliberately coarse for MVP — no rotation/expiry; a design
    follow-up if this proves too coarse in practice (not specified by
    trovex:4e85331d §D, a scoped implementation judgment call)."""
    import hashlib

    digest = hashlib.sha256(repo_root.encode("utf-8")).hexdigest()[:16]
    return f"git-{digest}"


def _routing_context(cwd: str) -> dict[str, str]:
    ctx: dict[str, str] = {"cwd": cwd}
    remote = _run_git(["remote", "get-url", "origin"], cwd)
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if remote:
        ctx["git_remote"] = remote
    if branch:
        ctx["git_branch"] = branch
    return ctx


def _base_event(repo_root: str) -> dict[str, Any] | None:
    cfg = config.load()
    if cfg is None:
        return None  # yoru init never run / no active identity — silent no-op
    event: dict[str, Any] = {
        "session_id": _session_id_for_repo(repo_root),
        "source": "independent:git",
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    event.update(_routing_context(repo_root))
    return event


def build_commit_event(sha: str, repo_root: str) -> dict[str, Any] | None:
    """Builds one commit's CanonicalEvent-shaped body from local `git` state.
    Shared by the post-commit hook (fast path) and `git_reconcile.py`'s
    git-log backfill (the audit-completeness filet for commits the hook
    never saw — `--no-verify`, or a repo where the hook was never
    installed) — both paths must produce byte-identical `entry_uuid`s for
    the same commit so the backend's existing (session_id, entry_uuid)
    dedup collapses a commit captured by BOTH paths into one event, not
    two (B3 slice2 AC #2)."""
    event = _base_event(repo_root)
    if event is None:
        return None
    message = _run_git(["log", "-1", "--format=%B", sha], repo_root) or ""
    stat = _run_git(["show", "--stat", "--format=", sha], repo_root)
    diff = _run_git(["show", "--format=", "--no-color", sha], repo_root)
    commit_ts = _run_git(["log", "-1", "--format=%cI", sha], repo_root)
    if commit_ts:
        event["ts"] = commit_ts
    event["kind"] = "commit"
    event["content"] = _cap(message.strip()) if message else None
    event["diff_unified"] = _cap(diff)
    event["diff_stat"] = _cap(stat, limit=_MAX_STAT_CHARS)
    event["entry_uuid"] = f"git-commit-{sha}"
    return event


def _handle_post_commit(sha: str, repo_root: str) -> int:
    if os.environ.get("AGENT_RELAY_CHILD") == "1":
        return 0
    event = build_commit_event(sha, repo_root)
    if event is None:
        return 0
    _spool(event)
    return 0


def _handle_pre_push(repo_root: str) -> int:
    # git always feeds pre-push refs on stdin — drain it unconditionally
    # (even on the AGENT_RELAY_CHILD no-op path) so git never sees a broken
    # pipe / hangs waiting for us to read.
    lines = sys.stdin.read().splitlines()
    if os.environ.get("AGENT_RELAY_CHILD") == "1":
        return 0
    base = _base_event(repo_root)
    if base is None:
        return 0
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if remote_sha == "0" * 40:
            continue  # new branch/ref — not a force-push
        is_ff = (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", remote_sha, local_sha],
                cwd=repo_root,
                stderr=subprocess.DEVNULL,
                timeout=10,
            ).returncode
            == 0
        )
        if is_ff:
            continue
        event = dict(base)
        event["force_push"] = True
        event["content"] = (
            f"force-push on {local_ref}: {remote_sha[:8]} -> {local_sha[:8]} "
            "(not a fast-forward)"
        )
        _spool(event)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if not argv:
            return 0
        kind = argv[0]
        repo_root = _run_git(["rev-parse", "--show-toplevel"], os.getcwd()) or os.getcwd()
        if kind == "post-commit":
            sha = argv[1] if len(argv) > 1 else (_run_git(["rev-parse", "HEAD"], repo_root) or "")
            if not sha:
                return 0
            return _handle_post_commit(sha, repo_root)
        if kind == "pre-push":
            return _handle_pre_push(repo_root)
        return 0
    except Exception:
        # Never let a capture-path bug fail the user's git operation.
        return 0


if __name__ == "__main__":
    sys.exit(main())
