from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from yoru_cli.hook_template import HOOK_SCRIPT


def test_hook_script_has_bash_shebang() -> None:
    assert HOOK_SCRIPT.startswith("#!/usr/bin/env bash")


def test_hook_script_posts_to_events_endpoint() -> None:
    assert "api/v1/sessions/events" in HOOK_SCRIPT


def test_hook_script_forwards_bearer_token() -> None:
    assert "Authorization: Bearer" in HOOK_SCRIPT


def test_hook_script_skips_agent_relay_children_before_cfg_read() -> None:
    # The AGENT_RELAY_CHILD env gate must short-circuit BEFORE identity/config
    # resolution, otherwise agent-relay-spawned sessions pollute receipt.db.
    gate = '[ "${AGENT_RELAY_CHILD:-0}" = "1" ] && exit 0'
    assert gate in HOOK_SCRIPT, "env gate missing from hook template"
    gate_idx = HOOK_SCRIPT.index(gate)
    root_idx = HOOK_SCRIPT.index('ROOT="${HOME}/.config/yoru"')
    assert gate_idx < root_idx, "env gate must come BEFORE identity/config resolution"


# ── functional: identity-scoped config resolution (A.3#3) ──────────────────

SESSION_START = json.dumps({"hook_event_name": "SessionStart", "session_id": "s1"})


def _bash() -> str:
    return shutil.which("bash") or "/bin/bash"


def _hook_env(tmp_path: Path) -> tuple[Path, Path, dict]:
    """The real hook script + a PATH with a capturing fake `curl` (records its
    argv to a file) alongside the real python3/cat, and HOME=tmp_path so the
    hook reads whatever config we seed under tmp_path/.config/yoru."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    capture = tmp_path / "curl.args"
    bash_abs = shutil.which("bash") or "/bin/bash"
    fake_curl = bindir / "curl"
    fake_curl.write_text(
        f"#!{bash_abs}\nprintf '%s\\n' \"$@\" > {capture}\ncat >/dev/null 2>&1 || true\n"
    )
    fake_curl.chmod(fake_curl.stat().st_mode | stat.S_IEXEC)
    for real in ("python3", "cat"):
        src = shutil.which(real)
        if src:
            os.symlink(src, bindir / real)

    hook = tmp_path / "yoru.sh"
    hook.write_text(HOOK_SCRIPT)
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

    env = {"PATH": str(bindir), "HOME": str(tmp_path)}
    return hook, capture, env


def _run_hook(hook: Path, env: dict, payload: str = SESSION_START):
    if not shutil.which("python3") or not shutil.which("curl"):
        pytest.skip("python3/curl not on PATH")
    return subprocess.run(
        [_bash(), str(hook)], input=payload.encode(),
        env=env, capture_output=True, timeout=15,
    )


def test_hook_uses_flat_config_when_not_migrated(tmp_path):
    """Pre-A.3#3 install (no `active` pointer yet) — the hook must still
    resolve the legacy flat config.json, same as before slots existed."""
    hook, capture, env = _hook_env(tmp_path)
    cfg_dir = tmp_path / ".config" / "yoru"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps({"server": "http://flat", "token": "rcpt_FLAT"}))

    out = _run_hook(hook, env)
    assert out.returncode == 0
    args = capture.read_text()
    assert "http://flat/api/v1/sessions/events" in args
    assert "Authorization: Bearer rcpt_FLAT" in args


def test_hook_uses_active_identity_slot_config(tmp_path):
    hook, capture, env = _hook_env(tmp_path)
    cfg_dir = tmp_path / ".config" / "yoru"
    slot = cfg_dir / "identities" / "id-1"
    slot.mkdir(parents=True)
    (slot / "config.json").write_text(json.dumps({"server": "http://slot", "token": "rcpt_SLOT"}))
    (cfg_dir / "active").write_text("id-1")

    out = _run_hook(hook, env)
    assert out.returncode == 0
    args = capture.read_text()
    assert "http://slot/api/v1/sessions/events" in args
    assert "Authorization: Bearer rcpt_SLOT" in args


def test_hook_noop_when_active_points_to_missing_slot(tmp_path):
    """Corrupted/partial state (active pointer with no matching slot dir) —
    the hook must fail SAFE (silent no-op), never error or block."""
    hook, capture, env = _hook_env(tmp_path)
    cfg_dir = tmp_path / ".config" / "yoru"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "active").write_text("ghost-id")

    out = _run_hook(hook, env)
    assert out.returncode == 0
    assert out.stdout == b""
    assert not capture.exists()
