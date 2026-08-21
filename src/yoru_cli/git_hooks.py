"""Install the independent git capture floor's hooks into a repo (B3 slice1,
trovex:4e85331d §D). Non-destructive: CHAINS onto whatever hook script
already exists (husky, pre-commit-framework, a dev's own script) rather than
overwriting `core.hooksPath`, since that's a single slot a repo may already
be using (review-cli-tool §3's settings-merge-safety principle, applied to
git hooks instead of settings.json).

Idempotent: re-running `yoru init` in an already-hooked repo is a no-op —
the managed block is marker-delimited and installed at most once per hook
file.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_MARKER = "# >>> yoru git capture (managed, do not edit below) >>>"
_END_MARKER = "# <<< yoru git capture <<<"

_HOOKS = ("post-commit", "pre-push")

# Invokes the `yoru` CONSOLE-SCRIPT, never `python3 -m yoru_cli...` (round-2
# review finding, critical): under the documented pipx install, the system
# `python3` on PATH is NOT the interpreter pipx installed yoru-cli into, so
# `python3 -m yoru_cli.git_hook_run` raised ModuleNotFoundError on every
# real pipx install — silently swallowed by `|| true`, so B3 captured
# NOTHING on the primary install path while `yoru init` printed "commits
# captured" regardless. `yoru`'s own console-script entrypoint is pinned to
# the correct interpreter by construction (that's what pipx/uv tool install
# generate), so this can't drift from whichever python yoru-cli actually
# lives in. Failures are appended to a recoverable log instead of vanishing
# into `|| true` — an audit tool's own capture hook must never fail silently
# and claim success.
_ERROR_LOG = '"${HOME}/.config/yoru/git-hook-errors.log"'


def _block_for(hook_name: str) -> str:
    if hook_name == "post-commit":
        args = 'post-commit "$(git rev-parse HEAD)"'
    else:
        args = "pre-push"
    call = f"yoru git-hook-run {args} 2>>{_ERROR_LOG} || true"
    return f"{_MARKER}\n{call}\n{_END_MARKER}\n"


def repo_root(cwd: str | None = None) -> Path | None:
    """The top-level working directory of the git repo containing `cwd`
    (default: the process cwd), or None if `cwd` isn't inside a git repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or os.getcwd(),
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return Path(out.decode("utf-8").strip())
    except Exception:
        return None


def _hooks_dir(root: Path) -> Path | None:
    """`git rev-parse --git-path hooks` — resolves `core.hooksPath` (husky
    etc.) and worktree-specific git dirs correctly, so this never guesses
    `.git/hooks` when a repo has customized it."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        rel = out.decode("utf-8").strip()
        return (root / rel).resolve()
    except Exception:
        return None


def install_git_hooks(root: Path) -> list[str]:
    """Install/chain the post-commit + pre-push hooks into `root`'s repo.
    Returns the list of hook names actually (newly) installed — empty if
    already present or the hooks dir couldn't be resolved. Never raises:
    a hook-install failure must not fail `yoru init`."""
    installed: list[str] = []
    try:
        hooks_dir = _hooks_dir(root)
        if hooks_dir is None:
            return installed
        hooks_dir.mkdir(parents=True, exist_ok=True)
        for hook_name in _HOOKS:
            hook_path = hooks_dir / hook_name
            block = _block_for(hook_name)
            if hook_path.is_file():
                existing = hook_path.read_text(encoding="utf-8")
                if _MARKER in existing:
                    continue  # already installed — idempotent no-op
                new_content = existing.rstrip("\n") + "\n\n" + block
                hook_path.write_text(new_content, encoding="utf-8")
            else:
                hook_path.write_text(f"#!/usr/bin/env bash\nset -e\n\n{block}", encoding="utf-8")
            mode = hook_path.stat().st_mode
            os.chmod(hook_path, mode | 0o111)  # ensure executable, keep existing perms otherwise
            installed.append(hook_name)
    except Exception:
        return installed
    return installed
