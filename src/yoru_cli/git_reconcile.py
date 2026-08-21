"""git-log reconciliation — the audit-completeness filet for B3's capture
floor (slice2, trovex:4e85331d §D, ruling point 3).

The post-commit hook (slice1) is the FAST path, but it's bypassable —
`git commit --no-verify` skips it outright, and a repo where `yoru init`
was never (re-)run after a fresh clone never had it installed. For an audit
product, a capture floor that a `--no-verify` silently defeats is not a
floor. This module periodically walks `git log` in every registered repo
from the last SHA it saw to `HEAD` and spools any commit the hook missed —
independent of whether the hook ever fired.

Idempotent against the hook path by construction: `git_hook_run.
build_commit_event` stamps a deterministic `entry_uuid` (`git-commit-<sha>`)
regardless of which path builds it, so a commit captured by BOTH the hook
AND a later reconciliation pass collapses to one event via the backend's
existing (session_id, entry_uuid) ingest dedup — no new backend work needed.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterator

from . import config
from .git_hook_run import _run_git, _spool, build_commit_event


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    tmp.replace(path)


@contextlib.contextmanager
def _locked_json_txn(path: Path, default: Any) -> Iterator[Any]:
    """Locked read-modify-write over a JSON file — same fcntl pattern as
    `transcript_tailer._state_txn` (opens a sibling `.lock` file, holds
    LOCK_EX for the duration, always writes back on exit). No unconditional
    truncate-on-open: the data is loaded, mutated by the caller, and only
    written back explicitly by `_save_json` after the lock is confirmed
    held — the f6f32c89 lesson (never truncate before ownership is
    confirmed) applies here too, even though this is flock not a PID
    lockfile."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            data = _load_json(path, default)
            yield data
            _save_json(path, data)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def register_repo(repo_path: str) -> None:
    """Adds `repo_path` to the reconciliation registry (idempotent) and
    seeds its last-seen SHA to current HEAD if it's newly registered — same
    "discover, don't backfill ancient history" posture as
    `transcript_tailer.run()` seeking a newly-found transcript to its
    current end-of-file: a commit made AFTER registration that skips the
    hook (`--no-verify`) is still caught by the next reconciliation pass;
    the repo's pre-existing history is not flooded in on first pairing."""
    repos_path = config.git_repos_path()
    with _locked_json_txn(repos_path, []) as repos:
        if repo_path not in repos:
            repos.append(repo_path)

    head = _run_git(["rev-parse", "HEAD"], repo_path)
    if head is None:
        return  # empty repo (no commits yet) or not resolvable — nothing to seed
    state_path = config.git_reconcile_state_path()
    with _locked_json_txn(state_path, {}) as state:
        if repo_path not in state:
            state[repo_path] = head


def reconcile_repo(repo_path: str) -> int:
    """Backfills any commit in `repo_path` between its last-seen SHA and
    current HEAD. Returns the number of commits spooled. Never raises —
    a repo that's been deleted/moved/rewritten-history since registration
    degrades to "reconcile nothing this pass", not a crash; the state
    simply advances to the new HEAD so a genuinely gone repo doesn't retry
    forever."""
    if not os.path.isdir(repo_path):
        return 0
    head = _run_git(["rev-parse", "HEAD"], repo_path)
    if head is None:
        return 0

    state_path = config.git_reconcile_state_path()
    spooled = 0
    with _locked_json_txn(state_path, {}) as state:
        last_sha = state.get(repo_path)
        if last_sha is None:
            state[repo_path] = head
            return 0
        if last_sha == head:
            return 0

        rev_list = _run_git(["rev-list", "--reverse", f"{last_sha}..{head}"], repo_path)
        shas = rev_list.splitlines() if rev_list else []
        for sha in shas:
            event = build_commit_event(sha, repo_path)
            if event is not None:
                _spool(event)
                spooled += 1
        state[repo_path] = head
    return spooled


def reconcile_all() -> int:
    """Reconciles every registered repo. Returns the total commits spooled
    across all of them. A single repo's failure (deleted, permissions,
    corrupt) never stops the others — same "one bad item never blocks the
    rest" posture as `transcript_tailer._drain_git_spool` (round-1 review
    finding on that function applies here by the same logic)."""
    repos_path = config.git_repos_path()
    repos = _load_json(repos_path, [])
    if not isinstance(repos, list):
        return 0
    total = 0
    for repo_path in repos:
        try:
            total += reconcile_repo(repo_path)
        except Exception:
            continue
    return total
