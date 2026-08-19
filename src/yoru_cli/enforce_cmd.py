"""`yoru enforce` — the OPT-IN PreToolUse enforcement gate.

yoru's default install is a PASSIVE audit trail: the always-on hook streams
events AFTER they happen and NEVER blocks the terminal. This is a SEPARATE,
opt-in layer that can HALT the most dangerous, irreversible operations
(destructive db / rm -rf root / secret exfiltration) BEFORE they run, and ask a
human to confirm. It is off by default and does not touch the passive streamer.

Design guarantees (must hold):
  - Separate hook + separate lifecycle: never modifies the `yoru.sh` streamer.
  - Default OFF: the gate no-ops unless enforcement is enabled (YORU_ENFORCE
    env, or the on-disk policy marker written by `yoru enforce --enable`).
  - Bounded + FAIL-OPEN: any error/timeout/uncertainty ALLOWS the op (a yoru
    failure must never block a benign command). A block happens ONLY on an
    explicit high-confidence match, expressed via the deny JSON — never via a
    nonzero exit — so every failure path falls through to allow.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import enforce_policy
from .enforce_template import ENFORCE_HOOK_SCRIPT

_CONFIG_DIR = Path.home() / ".config" / "yoru"
_POLICY_MARKER = _CONFIG_DIR / "enforce.json"  # presence = opt-in enabled
_HOOK_PATH = Path.home() / ".claude" / "hooks" / "yoru-enforce.sh"
_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def enforcement_enabled() -> bool:
    """Opt-in gate. True iff YORU_ENFORCE is truthy OR the policy marker exists.
    Default (neither) = OFF."""
    if os.environ.get("YORU_ENFORCE", "").strip().lower() in ("1", "yes", "true", "on"):
        return True
    return _POLICY_MARKER.exists()


# ── the hidden decision the PreToolUse hook invokes ─────────────────────────

def check() -> int:
    """Read a PreToolUse event JSON on stdin; emit a deny decision ONLY on a
    high-confidence dangerous match WHILE enforcement is enabled. ALWAYS returns
    0 — a block is expressed through the deny JSON, never a nonzero exit, so any
    error path (including a disabled gate) simply allows."""
    try:
        if not enforcement_enabled():
            return 0
        payload = json.load(sys.stdin)
        violation = enforce_policy.evaluate(
            payload.get("tool_name", ""), payload.get("tool_input") or {}
        )
        if violation is None:
            return 0
        reason = (
            f"yoru enforcement gate blocked this: {violation.reason} "
            f"[{violation.rule}]. This irreversible operation was HALTED. If you "
            f"truly intend it, run it yourself or turn the gate off with "
            f"`yoru enforce --disable`."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        return 0
    except Exception:
        # Fail-open: a yoru failure must never block the user's tool call.
        return 0


# ── install / enable / disable ──────────────────────────────────────────────

def _is_enforce_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    inner = entry.get("hooks")
    if not isinstance(inner, list) or not inner:
        return False
    first = inner[0]
    return isinstance(first, dict) and isinstance(first.get("command"), str) \
        and first["command"].endswith("yoru-enforce.sh")


def _register_hook() -> None:
    """Add the PreToolUse enforce entry to settings.json, preserving all other
    keys and the streamer's own entries. Idempotent (replaces a stale entry)."""
    if _SETTINGS_PATH.exists():
        try:
            obj = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
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
    entries = hooks.setdefault("PreToolUse", [])
    if not isinstance(entries, list):
        entries = []
        hooks["PreToolUse"] = entries

    entry = {
        "matcher": "*",
        # A per-hook wall-clock cap: even if the inner `timeout` is bypassed, a
        # hung gate can never stall the terminal beyond this (fail-open).
        "hooks": [{"type": "command", "command": str(_HOOK_PATH), "timeout": 10}],
    }
    for idx, e in enumerate(entries):
        if _is_enforce_entry(e):
            entries[idx] = entry
            break
    else:
        entries.append(entry)

    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, _SETTINGS_PATH)


def _write_hook_script() -> None:
    _HOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HOOK_PATH.write_text(ENFORCE_HOOK_SCRIPT, encoding="utf-8")
    os.chmod(_HOOK_PATH, 0o755)


def _unregister_hook() -> None:
    """Remove ONLY the enforce PreToolUse entry from settings.json, leaving the
    streamer's entries and every other user key intact. Idempotent — a no-op if
    the entry (or the file) isn't there. Atomic write."""
    if not _SETTINGS_PATH.exists():
        return
    try:
        obj = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return  # don't clobber an unparseable user file on a teardown
    if not isinstance(obj, dict):
        return
    hooks = obj.get("hooks")
    if not isinstance(hooks, dict):
        return
    entries = hooks.get("PreToolUse")
    if not isinstance(entries, list):
        return
    kept = [e for e in entries if not _is_enforce_entry(e)]
    if len(kept) == len(entries):
        return  # nothing of ours to remove
    if kept:
        hooks["PreToolUse"] = kept
    else:
        hooks.pop("PreToolUse", None)  # don't leave an empty list behind
    tmp = _SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, _SETTINGS_PATH)


def enable() -> int:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _POLICY_MARKER.write_text(
        json.dumps({"enabled": True, "rules": "builtin"}, indent=2),
        encoding="utf-8",
    )
    _write_hook_script()
    _register_hook()
    print("✓ yoru enforcement gate ENABLED (opt-in).")
    print("  Halts high-confidence irreversible ops (destructive db, rm -rf")
    print("  root, secret exfil) and asks you to confirm. Fail-open on any error.")
    print("  The passive audit streamer is unchanged. Disable: yoru enforce --disable.")
    return 0


def disable() -> int:
    # Full teardown: drop the opt-in marker AND unregister the PreToolUse hook +
    # remove its script, so a disabled gate stops spawning bash/python on every
    # tool call (and `status` no longer reports a lingering hook). Symmetric with
    # enable(); the passive audit streamer is never touched.
    existed = _POLICY_MARKER.exists() or _HOOK_PATH.exists()
    if _POLICY_MARKER.exists():
        _POLICY_MARKER.unlink()
    _unregister_hook()
    if _HOOK_PATH.exists():
        _HOOK_PATH.unlink()
    print("✓ yoru enforcement gate DISABLED — hook unregistered + removed."
          if existed else "yoru enforcement gate was not enabled.")
    print("  (The passive audit trail is unaffected.)")
    return 0


def status() -> int:
    on = enforcement_enabled()
    src = (
        "YORU_ENFORCE env" if os.environ.get("YORU_ENFORCE", "").strip()
        else ("policy marker" if _POLICY_MARKER.exists() else "off")
    )
    print(f"enforcement: {'ON' if on else 'OFF'} ({src})")
    print(f"hook script: {'present' if _HOOK_PATH.exists() else 'not installed'}")
    return 0


def run(args: argparse.Namespace) -> int:
    if getattr(args, "enable", False):
        return enable()
    if getattr(args, "disable", False):
        return disable()
    return status()
