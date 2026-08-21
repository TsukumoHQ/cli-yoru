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
from .skill_template import SKILL_MD, SKILL_NAME


def _hostname() -> str:
    """Raw machine hostname — not user-overridable (unlike --label). Sent
    separately so the server can populate CliToken.machine_hostname (the
    identity model's structured device field) even when the user picks a
    custom --label."""
    return socket.gethostname().split(".")[0] or "unknown"


def _default_label() -> str:
    """Best-effort human label for this machine — 'macbook-air · darwin'."""
    return f"{_hostname()} · {platform.system().lower()}"


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


def _pair_device(server: str, label: str, *, no_browser: bool) -> tuple[str, str | None] | None:
    """Run the device-code pairing handshake — returns (token, identity_id)
    or None. `identity_id` is the server-issued CliToken.id (identity slot
    key, A.3#3) — None only against an older server that doesn't send it
    yet, in which case the caller falls back to `synthesize_identity_id`."""
    client = ReceiptClient(server)
    try:
        start = client.start_device_code(label=label, hostname=_hostname())
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
            return token, resp.get("identity_id")
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


def refresh_hook_assets() -> tuple[Path, Path, Path]:
    """(Re)write the Claude Code hook script + the public yoru skill, and
    (re)register the hook in settings.json. Idempotent — used by `init` (first
    install) and `update` (refresh to the new version + repair the wiring).
    Returns (hook_path, settings_path, skill_path). Does NOT touch config."""
    hook_dir = Path.home() / ".claude" / "hooks"
    hook_path = hook_dir / "yoru.sh"
    os.makedirs(hook_dir, exist_ok=True)
    hook_path.write_text(HOOK_SCRIPT, encoding="utf-8")
    os.chmod(hook_path, 0o755)

    settings_path = Path.home() / ".claude" / "settings.json"
    _merge_settings_json(settings_path, hook_path)

    skill_path = _install_skill()
    return hook_path, settings_path, skill_path


def _revoke_previous_pairing(old_config: dict, new_token: str) -> None:
    """--force is replacing an existing pairing — revoke the token it's
    superseding server-side instead of leaving it live forever (the old
    behavior: local file overwritten, server-side credential orphaned
    indefinitely — a direct accountability hole for an audit product).

    Always warns explicitly before revoking, whether or not the new pairing
    turns out to be the same identity — the CLI has no reliable way to know
    that in advance (the old config may predate identity tracking, and the
    --token direct-mint path never learns the new identity at all), so an
    unconditional warning is the only version of "no silent clobber" that
    can't have a false negative.

    EXCEPT when `new_token` is byte-identical to the old one (re-running
    `--force` with the same `--token`/`$YORU_TOKEN`, or against an unchanged
    env) — revoking there would immediately brick the credential we're about
    to save (config points at a token the server just revoked, the hook
    streams start 401ing). Same token = nothing superseded, so skip.
    """
    old_server = (old_config.get("server") or "").rstrip("/")
    old_token = old_config.get("token") or ""
    if not old_server or not old_token:
        return
    if old_token == new_token:
        print(
            "  · --force re-paired with the same token — nothing to revoke",
            file=sys.stderr,
        )
        return
    print(
        "  ⚠ --force replaces the existing pairing — revoking the previous "
        "token server-side",
        file=sys.stderr,
    )
    try:
        revoked = ReceiptClient(old_server, token=old_token).logout()
    except httpx.HTTPError as e:
        print(
            f"  ⚠ could not revoke the previous token ({e}) — revoke it "
            "manually from the dashboard if this is a real identity change",
            file=sys.stderr,
        )
        return
    if revoked:
        print("  ✓ previous token revoked", file=sys.stderr)
    else:
        print("  · previous token was already revoked/invalid", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    force = getattr(args, "force", False)
    if config.exists() and not force:
        print("Already installed (use --force to overwrite)", file=sys.stderr)
        return 1

    old_config: dict | None = None
    if force:
        try:
            old_config = config.load()
        except (OSError, json.JSONDecodeError):
            # --force must stay a working recovery path even from a corrupt
            # config.json (that's the whole point of --force existing) — no
            # old token to revoke just means we skip that step, not abort.
            old_config = None

    server: str = args.server
    token: str | None = getattr(args, "token", None)
    # Also accept YORU_TOKEN from env for headless / CI / server deployments.
    if not token:
        token = os.environ.get("YORU_TOKEN", "").strip() or None

    label = (getattr(args, "label", None) or "").strip() or _default_label()
    identity_id: str | None = None

    if not token:
        no_browser = bool(getattr(args, "no_browser", False))
        paired = _pair_device(server, label, no_browser=no_browser)
        if not paired:
            return 2
        token, identity_id = paired

    # --token/$YORU_TOKEN never round-trips a server-issued id — key the
    # local slot off the token itself (DEC-yoru-design-ruling-1 Q1: never
    # hash(server_url, user_email), that collides across re-pairs).
    if not identity_id:
        identity_id = config.synthesize_identity_id(token)

    if old_config:
        _revoke_previous_pairing(old_config, token)

    config.save({
        "identity_id": identity_id,
        "identity_label": label,
        "server": server,
        "token": token,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    refresh_hook_assets()

    print(f"\u2713 config   \u2192 ~/.config/yoru/identities/{identity_id}/config.json")
    print("\u2713 hook     \u2192 ~/.claude/hooks/yoru.sh")
    print("\u2713 settings \u2192 ~/.claude/settings.json (hook registered)")
    print("\u2713 skill    \u2192 ~/.claude/skills/yoru/SKILL.md (Claude can drive setup + usage)")
    print("Next: run Claude Code normally; first event streams to /sessions/events.")
    return 0
