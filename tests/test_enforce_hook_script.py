"""Regression: the enforcement hook must WORK when `timeout` is absent.

Default macOS ships no `timeout`/`gtimeout` (GNU coreutils). The first cut of
the hook ran `... | timeout 5 yoru enforce-check || exit 0`, which on macOS hit
'timeout: command not found' → the pipe failed → `|| exit 0` → the gate silently
NO-OPPED while `yoru enforce status` reported ON (false protection).

These tests run the ACTUAL bundled hook script under a minimal PATH that contains
NO `timeout` (only a fake `yoru` + a `cat`), deterministically exercising the
no-timeout fallback branch on any platform.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from yoru_cli.enforce_template import ENFORCE_HOOK_SCRIPT

DENY = json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse", "permissionDecision": "deny",
    "permissionDecisionReason": "blocked (test)",
}})


def _no_timeout_env(tmp_path: Path, yoru_stdout: str) -> tuple[Path, dict]:
    """A hook script + an env whose PATH has a fake `yoru` and `cat` but NO
    `timeout`/`gtimeout`, forcing the fallback branch."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # Fake `yoru`: drain stdin, emit the given stdout (simulates enforce-check).
    # Absolute bash shebang — the minimal PATH has no `env`.
    bash_abs = shutil.which("bash") or "/bin/bash"
    fake = bindir / "yoru"
    fake.write_text(
        f"#!{bash_abs}\ncat >/dev/null\n"
        f"printf '%s' {json.dumps(yoru_stdout)}\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    # `cat` is the only external the hook needs; symlink the real one in.
    real_cat = shutil.which("cat") or "/bin/cat"
    os.symlink(real_cat, bindir / "cat")

    hook = tmp_path / "yoru-enforce.sh"
    hook.write_text(ENFORCE_HOOK_SCRIPT)
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

    env = {"PATH": str(bindir), "YORU_ENFORCE": "1"}
    return hook, env


def _bash() -> str:
    return shutil.which("bash") or "/bin/bash"


def test_hook_denies_without_timeout(tmp_path):
    """No `timeout` on PATH → the hook still runs the checker and passes its
    deny decision through (the macOS regression)."""
    if not shutil.which("cat"):
        pytest.skip("no cat")
    hook, env = _no_timeout_env(tmp_path, DENY)
    out = subprocess.run(
        [_bash(), str(hook)], input=b'{"tool_name":"Bash","tool_input":{}}',
        env=env, capture_output=True, timeout=15,
    )
    assert out.returncode == 0
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_allows_without_timeout_when_checker_silent(tmp_path):
    """No `timeout`, checker emits nothing (benign/allow) → hook emits nothing."""
    if not shutil.which("cat"):
        pytest.skip("no cat")
    hook, env = _no_timeout_env(tmp_path, "")
    out = subprocess.run(
        [_bash(), str(hook)], input=b'{"tool_name":"Bash","tool_input":{}}',
        env=env, capture_output=True, timeout=15,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == b""


def test_template_has_timeout_fallback():
    """Guard the shape so the bare-`timeout` regression can't silently return."""
    assert "command -v timeout" in ENFORCE_HOOK_SCRIPT
    assert "command -v gtimeout" in ENFORCE_HOOK_SCRIPT
    # A no-timeout else-branch that still runs the checker.
    assert ENFORCE_HOOK_SCRIPT.count("yoru enforce-check") >= 3
