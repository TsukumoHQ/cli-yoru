from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config
from .api import ReceiptClient
from .hook_template import HOOK_SCRIPT


def _default_label() -> str:
    """Best-effort human label for this machine — 'macbook-air · darwin'."""
    host = socket.gethostname().split(".")[0] or "unknown"
    return f"{host} · {platform.system().lower()}"


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
        return isinstance(cmd, str) and cmd.endswith("yoru.sh")

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


def _pair_device(server: str, label: str, *, no_browser: bool) -> str | None:
    """Run the device-code pairing handshake — returns the raw token or None."""
    client = ReceiptClient(server)
    try:
        start = client.start_device_code(label=label)
    except httpx.HTTPError as e:
        print(f"error: failed to contact {server}: {e}", file=sys.stderr)
        return None

    user_code = start["user_code"]
    verify_uri = start["verification_uri"]
    verify_complete = start["verification_uri_complete"]
    device_code = start["device_code"]
    expires_in = int(start.get("expires_in", 600))
    interval = int(start.get("interval", 2))

    print()
    print(f"  Pair this device with your Yoru account:")
    print(f"    1. Open  {verify_uri}")
    print(f"    2. Enter {user_code}")
    print()
    if not no_browser:
        try:
            webbrowser.open(verify_complete)
        except Exception:
            pass

    deadline = time.time() + expires_in
    while time.time() < deadline:
        try:
            resp = client.poll_device_code(device_code)
        except httpx.HTTPError as e:
            print(f"\nerror: poll failed: {e}", file=sys.stderr)
            return None
        s = resp.get("status")
        if s == "approved":
            token = resp.get("token")
            if not token:
                print("error: approved but no token returned", file=sys.stderr)
                return None
            print(f"  ✓ Paired as {label}")
            return token
        if s in ("expired", "denied"):
            print(f"\nerror: pairing {s} — re-run `yoru init`", file=sys.stderr)
            return None
        # pending — sleep and keep polling
        sys.stdout.write("  waiting for approval…\r")
        sys.stdout.flush()
        time.sleep(interval)

    print("\nerror: pairing timed out — re-run `yoru init`", file=sys.stderr)
    return None


def refresh_hook_assets() -> tuple[Path, Path]:
    """(Re)write the Claude Code hook script and (re)register it in
    settings.json. Idempotent — used by `init` (first install) and `update`
    (refresh the hook to the new version + repair the settings wiring). Returns
    (hook_path, settings_path). Does NOT touch config (server/token)."""
    hook_dir = Path.home() / ".claude" / "hooks"
    hook_path = hook_dir / "yoru.sh"
    os.makedirs(hook_dir, exist_ok=True)
    hook_path.write_text(HOOK_SCRIPT, encoding="utf-8")
    os.chmod(hook_path, 0o755)

    settings_path = Path.home() / ".claude" / "settings.json"
    _merge_settings_json(settings_path, hook_path)
    return hook_path, settings_path


def run(args: argparse.Namespace) -> int:
    if config.exists() and not getattr(args, "force", False):
        print("Already installed (use --force to overwrite)", file=sys.stderr)
        return 1

    server: str = args.server
    token: str | None = getattr(args, "token", None)
    # Also accept YORU_TOKEN from env for headless / CI / server deployments.
    if not token:
        token = os.environ.get("YORU_TOKEN", "").strip() or None

    if not token:
        label = (getattr(args, "label", None) or "").strip() or _default_label()
        no_browser = bool(getattr(args, "no_browser", False))
        token = _pair_device(server, label, no_browser=no_browser)
        if not token:
            return 2

    config.save({
        "server": server,
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    refresh_hook_assets()

    print("\u2713 config   \u2192 ~/.config/yoru/config.json")
    print("\u2713 hook     \u2192 ~/.claude/hooks/yoru.sh")
    print("\u2713 settings \u2192 ~/.claude/settings.json (hook registered)")
    print("Next: run Claude Code normally; first event streams to /sessions/events.")
    return 0
