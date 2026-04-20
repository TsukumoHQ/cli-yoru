from __future__ import annotations

import json
from pathlib import Path

from receipt_cli.init_cmd import _merge_settings_json


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _receipt_entries(settings: dict) -> list[dict]:
    return [
        e
        for e in settings.get("hooks", {}).get("PostToolUse", [])
        if isinstance(e, dict)
        and e.get("hooks")
        and isinstance(e["hooks"][0], dict)
        and str(e["hooks"][0].get("command", "")).endswith("receipt.sh")
    ]


def test_creates_fresh_settings_when_absent(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    hook_path = tmp_path / ".claude" / "hooks" / "receipt.sh"

    _merge_settings_json(settings_path, hook_path)

    assert settings_path.is_file()
    data = _load(settings_path)
    entries = data["hooks"]["PostToolUse"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["matcher"] == "*"
    assert entry["hooks"][0]["type"] == "command"
    assert entry["hooks"][0]["command"] == str(hook_path)
    assert entry["hooks"][0]["command"].endswith("receipt.sh")


def test_preserves_user_hooks(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    hook_path = tmp_path / ".claude" / "hooks" / "receipt.sh"

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    seeded = {
        "theme": "dark",
        "allowedTools": ["Bash", "Edit"],
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "/usr/local/bin/my-hook.sh"}],
                }
            ]
        },
    }
    settings_path.write_text(json.dumps(seeded), encoding="utf-8")

    _merge_settings_json(settings_path, hook_path)

    data = _load(settings_path)
    assert data["theme"] == "dark"
    assert data["allowedTools"] == ["Bash", "Edit"]
    pre = data["hooks"]["PreToolUse"]
    assert len(pre) == 1
    assert pre[0]["hooks"][0]["command"] == "/usr/local/bin/my-hook.sh"
    receipt = _receipt_entries(data)
    assert len(receipt) == 1
    assert receipt[0]["hooks"][0]["command"] == str(hook_path)


def test_idempotent_on_double_run(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    hook_path = tmp_path / ".claude" / "hooks" / "receipt.sh"

    _merge_settings_json(settings_path, hook_path)
    first = settings_path.read_text(encoding="utf-8")
    _merge_settings_json(settings_path, hook_path)
    second = settings_path.read_text(encoding="utf-8")

    assert first == second
    data = _load(settings_path)
    assert len(_receipt_entries(data)) == 1


def test_registers_session_lifecycle_matchers(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    hook_path = tmp_path / ".claude" / "hooks" / "receipt.sh"

    _merge_settings_json(settings_path, hook_path)

    data = _load(settings_path)
    hooks = data["hooks"]
    for event_name in ("PostToolUse", "SessionStart", "Stop"):
        entries = hooks[event_name]
        receipt_entries = [
            e
            for e in entries
            if isinstance(e, dict)
            and e.get("hooks")
            and isinstance(e["hooks"][0], dict)
            and str(e["hooks"][0].get("command", "")).endswith("receipt.sh")
        ]
        assert len(receipt_entries) == 1, f"{event_name} should have 1 receipt entry"
        assert receipt_entries[0]["hooks"][0]["command"] == str(hook_path)


def test_replaces_stale_receipt_entry_on_force(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    new_hook = tmp_path / ".claude" / "hooks" / "receipt.sh"
    stale_command = "/old/location/receipt.sh"

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    seeded = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": stale_command}],
                }
            ]
        }
    }
    settings_path.write_text(json.dumps(seeded), encoding="utf-8")

    _merge_settings_json(settings_path, new_hook)

    data = _load(settings_path)
    receipt = _receipt_entries(data)
    assert len(receipt) == 1
    assert receipt[0]["hooks"][0]["command"] == str(new_hook)
    all_commands = [
        e["hooks"][0]["command"]
        for e in data["hooks"]["PostToolUse"]
        if isinstance(e, dict) and e.get("hooks")
    ]
    assert stale_command not in all_commands
