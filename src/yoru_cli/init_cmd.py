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
from .agent_templates import CODEX_HOOK_SCRIPT, OPENCODE_PLUGIN_TS
from .hook_template import HOOK_SCRIPT
from .skill_template import SKILL_MD, SKILL_NAME


def _default_label() -> str:
    """Best-effort human label for this machine — 'macbook-air · darwin'."""
    host = socket.gethostname().split(".")[0] or "unknown"
    return f"{host} · {platform.system().lower()}"


RECEIPT_MATCHERS: list[tuple[str, str]] = [
    ("PostToolUse", "*"),
    ("SessionStart", "*"),
    ("Stop", "*"),
]

CODEX_MATCHERS: list[tuple[str, str | None]] = [
    ("SessionStart", None),
    ("UserPromptSubmit", None),
    ("Stop", None),
    ("PostToolUse", "Bash"),
    ("PostToolUse", "apply_patch"),
    ("PostToolUse", "Edit"),
    ("PostToolUse", "Write"),
    ("PreToolUse", "Bash"),
    ("PreToolUse", "apply_patch"),
    ("PreToolUse", "Edit"),
    ("PreToolUse", "Write"),
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


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json_object(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _merge_codex_hooks_json(hooks_path: Path, hook_path: Path) -> None:
    """Register the Codex hook in ~/.codex/hooks.json, preserving user hooks."""
    obj = _read_json_object(hooks_path)
    hooks = obj.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        obj["hooks"] = hooks

    command = f"{sys.executable} {hook_path}"

    def _is_yoru_entry(entry: object) -> bool:
        if not isinstance(entry, dict):
            return False
        inner = entry.get("hooks")
        if not isinstance(inner, list) or not inner:
            return False
        first = inner[0]
        if not isinstance(first, dict):
            return False
        cmd = first.get("command")
        return isinstance(cmd, str) and cmd.endswith("yoru.py")

    for event_name, matcher in CODEX_MATCHERS:
        entries = hooks.setdefault(event_name, [])
        if not isinstance(entries, list):
            entries = []
            hooks[event_name] = entries

        entry: dict[str, object] = {
            "hooks": [{"type": "command", "command": command}],
        }
        if matcher is not None:
            entry["matcher"] = matcher

        replaced = False
        for idx, existing in enumerate(entries):
            if not _is_yoru_entry(existing):
                continue
            if matcher is None or existing.get("matcher") == matcher:
                entries[idx] = entry
                replaced = True
                break
        if not replaced:
            entries.append(entry)

    _write_json_object(hooks_path, obj)


def _merge_opencode_package_json(package_path: Path) -> None:
    """Ensure OpenCode can resolve @opencode-ai/plugin for the local plugin."""
    obj = _read_json_object(package_path)
    deps = obj.setdefault("dependencies", {})
    if not isinstance(deps, dict):
        deps = {}
        obj["dependencies"] = deps
    deps.setdefault("@opencode-ai/plugin", "latest")
    _write_json_object(package_path, obj)


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
    print("  Pair this device with your Yoru account:")
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


def _install_skill() -> Path:
    """(Re)write the PUBLIC end-user yoru Claude Code skill so a fresh Claude
    session can drive setup + usage. Lands at ~/.claude/skills/yoru/SKILL.md,
    where Claude Code resolves user-level skills. Idempotent."""
    skill_dir = Path.home() / ".claude" / "skills" / SKILL_NAME
    os.makedirs(skill_dir, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(SKILL_MD, encoding="utf-8")
    return skill_path


def _install_codex_hook() -> tuple[Path, Path]:
    hook_dir = Path.home() / ".codex" / "hooks"
    hook_path = hook_dir / "yoru.py"
    os.makedirs(hook_dir, exist_ok=True)
    hook_path.write_text(CODEX_HOOK_SCRIPT, encoding="utf-8")
    os.chmod(hook_path, 0o755)

    hooks_path = Path.home() / ".codex" / "hooks.json"
    _merge_codex_hooks_json(hooks_path, hook_path)
    return hook_path, hooks_path


def _install_opencode_plugin(project_root: Path | None = None) -> tuple[Path, Path]:
    root = project_root or Path.cwd()
    opencode_dir = root / ".opencode"
    plugin_dir = opencode_dir / "plugins"
    plugin_path = plugin_dir / "yoru.ts"
    os.makedirs(plugin_dir, exist_ok=True)
    plugin_path.write_text(OPENCODE_PLUGIN_TS, encoding="utf-8")

    package_path = opencode_dir / "package.json"
    _merge_opencode_package_json(package_path)
    return plugin_path, package_path


def refresh_hook_assets() -> tuple[Path, Path, Path]:
    """(Re)write the Claude Code hook script + the public yoru skill, and
    (re)register the hook in settings.json. Idempotent — used by `init` (first
    install) and `update` (refresh to the new version + repair the wiring).
    Also installs Codex and OpenCode assets. Returns the historical
    (hook_path, settings_path, skill_path) tuple for update compatibility.
    Does NOT touch config."""
    hook_dir = Path.home() / ".claude" / "hooks"
    hook_path = hook_dir / "yoru.sh"
    os.makedirs(hook_dir, exist_ok=True)
    hook_path.write_text(HOOK_SCRIPT, encoding="utf-8")
    os.chmod(hook_path, 0o755)

    settings_path = Path.home() / ".claude" / "settings.json"
    _merge_settings_json(settings_path, hook_path)

    skill_path = _install_skill()
    _install_codex_hook()
    _install_opencode_plugin()
    return hook_path, settings_path, skill_path


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
    print("\u2713 skill    \u2192 ~/.claude/skills/yoru/SKILL.md (Claude can drive setup + usage)")
    print("\u2713 codex    \u2192 ~/.codex/hooks/yoru.py + ~/.codex/hooks.json")
    print("\u2713 opencode \u2192 .opencode/plugins/yoru.ts + .opencode/package.json")
    print("Next: run Claude Code, Codex, or OpenCode normally; events stream to /sessions/events.")
    return 0
