from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config
from .api import ReceiptClient
from .hook_template import HOOK_SCRIPT


RECEIPT_MATCHERS: list[tuple[str, str]] = [
    ("PostToolUse", "*"),
    ("SessionStart", "*"),
    ("Stop", "*"),
]


def _merge_settings_json(settings_path: Path, hook_path: Path) -> None:
    """Register the receipt hook in ~/.claude/settings.json, preserving user keys.

    Registers PostToolUse + SessionStart + Stop so the timeline has bookends.
    """
    if settings_path.exists():
        try:
            obj = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            obj = {}
        if not isinstance(obj, dict):
            obj = {}
    else:
        obj = {}

    hooks = obj.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        obj["hooks"] = hooks

    def _is_receipt_entry(entry: object) -> bool:
        if not isinstance(entry, dict):
            return False
        inner = entry.get("hooks")
        if not isinstance(inner, list) or not inner:
            return False
        first = inner[0]
        if not isinstance(first, dict):
            return False
        cmd = first.get("command")
        return isinstance(cmd, str) and cmd.endswith("receipt.sh")

    for event_name, matcher_glob in RECEIPT_MATCHERS:
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            entries = []
            hooks[event_name] = entries

        receipt_entry = {
            "matcher": matcher_glob,
            "hooks": [{"type": "command", "command": str(hook_path)}],
        }

        replaced = False
        for idx, entry in enumerate(entries):
            if _is_receipt_entry(entry):
                entries[idx] = receipt_entry
                replaced = True
                break
        if not replaced:
            entries.append(receipt_entry)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, settings_path)


def run(args: argparse.Namespace) -> int:
    if config.exists() and not getattr(args, "force", False):
        print("Already installed (use --force to overwrite)", file=sys.stderr)
        return 1

    server: str = args.server
    token: str | None = getattr(args, "token", None)

    if not token:
        user = (getattr(args, "user", None) or "").strip()
        if not user:
            if sys.stdin.isatty():
                user = input("Username for this machine: ").strip()
            else:
                user = sys.stdin.readline().strip()
        if not user:
            print("error: username is required to mint a hook token", file=sys.stderr)
            return 2
        try:
            resp = ReceiptClient(server).mint_token(user)
        except httpx.HTTPError as e:
            print(f"error: failed to mint token from {server}: {e}", file=sys.stderr)
            return 2
        token = resp.get("token")
        if not token:
            print(f"error: mint response missing 'token' field: {resp!r}", file=sys.stderr)
            return 2

    config.save({
        "server": server,
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    hook_dir = Path.home() / ".claude" / "hooks"
    hook_path = hook_dir / "receipt.sh"
    os.makedirs(hook_dir, exist_ok=True)
    hook_path.write_text(HOOK_SCRIPT, encoding="utf-8")
    os.chmod(hook_path, 0o755)

    settings_path = Path.home() / ".claude" / "settings.json"
    _merge_settings_json(settings_path, hook_path)

    print("\u2713 config   \u2192 ~/.config/receipt/config.json")
    print("\u2713 hook     \u2192 ~/.claude/hooks/receipt.sh")
    print("\u2713 settings \u2192 ~/.claude/settings.json (hook registered)")
    print("Next: run Claude Code normally; first event streams to /sessions/events.")
    return 0
