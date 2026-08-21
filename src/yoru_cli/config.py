"""Local identity-scoped state (DEC-yoru-design-ruling-1 A.3#3).

Each paired identity gets its own slot under
``~/.config/yoru/identities/<identity_id>/`` (config.json + enforce.json +
tail-state.json), so a second `yoru init --force` re-pair no longer clobbers
the first identity's state — the old slot stays on disk, just no longer
active. ``~/.config/yoru/active`` names the current slot; every other
module in this package keeps calling `load()`/`save()`/`remove()` exactly
as before — they now resolve against the active slot instead of a single
flat file, transparently.

`identity_id` is the server-issued `CliToken.id` when a slot comes from
device-code pairing (round-tripped via `DeviceCodePollOut.identity_id`).
The `--token`/`$YORU_TOKEN` headless path never round-trips one, so it
falls back to `synthesize_identity_id(token)` — hashing the TOKEN itself,
not `(server_url, user_email)` (that hash scheme was explicitly REJECTED,
DEC-yoru-design-ruling-1 Q1, because it collides across two different
identities re-paired at the same server+email; a token hash can't collide
that way — a different token always yields a different local slot).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _root_dir() -> Path:
    return Path.home() / ".config" / "yoru"


def _identities_dir() -> Path:
    return _root_dir() / "identities"


def _active_pointer_file() -> Path:
    return _root_dir() / "active"


def _legacy_config_file() -> Path:
    """The pre-A.3#3 flat config path. Auto-migrated into a slot on first
    access (`_migrate_legacy_if_needed`); never written to again."""
    return _root_dir() / "config.json"


def _slot_dir(identity_id: str) -> Path:
    return _identities_dir() / identity_id


def _slot_config_file(identity_id: str) -> Path:
    return _slot_dir(identity_id) / "config.json"


def synthesize_identity_id(token: str) -> str:
    return "local-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _ensure_private_dir(path: Path) -> None:
    """Create `path` (and any missing ancestors) then force 0700 on every
    directory from `path` up to and including `_root_dir()`.

    `Path.mkdir(parents=True, ...)`/`os.makedirs(..., mode=)` only apply
    `mode` to the leaf, not the intermediates they create along the way —
    so a plain `os.makedirs(identities/<id>, mode=0o700)` silently leaves
    `~/.config/yoru` itself (an auto-created intermediate) at the default,
    world-readable mode. The token lives under this tree (review-cli-tool
    §6), so every directory in it must stay private, not just the leaf.
    Never walks above `_root_dir()` — that's the user's shared ~/.config.
    """
    root = _root_dir()
    if path != root:
        path.relative_to(root)  # fail loud if ever misused on a path outside our tree
    path.mkdir(parents=True, exist_ok=True)
    node = path
    while True:
        os.chmod(node, 0o700)
        if node == root:
            break
        node = node.parent


def _migrate_legacy_if_needed() -> None:
    """One-time: a pre-A.3#3 flat config.json becomes a slot, so an existing
    pairing survives the upgrade instead of silently vanishing (`exists()`
    would otherwise start returning False for every current user). Runs at
    most once — a no-op the instant `active` exists."""
    if _active_pointer_file().is_file():
        return
    legacy = _legacy_config_file()
    if not legacy.is_file():
        return
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    token = data.get("token")
    if not token:
        return
    identity_id = data.get("identity_id") or synthesize_identity_id(token)
    save_to_slot(identity_id, data)
    set_active(identity_id)
    # MUST delete the legacy file, not just leave it — `remove()` (logout)
    # deletes ONLY the active pointer + the migrated slot, so a surviving
    # flat config.json would get re-migrated and re-activated on the very
    # next config access, silently resurrecting a token the user just
    # logged out. Migration has already copied everything of value into the
    # slot; the flat file is now dead weight, and live weight (a bearer
    # token sitting in a second place) besides.
    try:
        legacy.unlink()
    except OSError:
        pass


def active_identity_id() -> str | None:
    _migrate_legacy_if_needed()
    p = _active_pointer_file()
    if not p.is_file():
        return None
    value = p.read_text(encoding="utf-8").strip()
    return value or None


def set_active(identity_id: str) -> None:
    p = _active_pointer_file()
    _ensure_private_dir(p.parent)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(identity_id)


def list_identities() -> list[dict[str, Any]]:
    """Every paired identity slot on this $HOME. Each dict is that slot's
    config.json content plus `identity_id` — used by `yoru use` to list
    labels without contacting the server."""
    _migrate_legacy_if_needed()
    out: list[dict[str, Any]] = []
    d = _identities_dir()
    if not d.is_dir():
        return out
    for child in sorted(d.iterdir()):
        if not child.is_dir():
            continue
        cfg_path = child / "config.json"
        if not cfg_path.is_file():
            continue
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["identity_id"] = child.name
        out.append(data)
    return out


def exists() -> bool:
    return active_identity_id() is not None


def load() -> dict[str, Any] | None:
    identity_id = active_identity_id()
    if identity_id is None:
        return None
    path = _slot_config_file(identity_id)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_to_slot(identity_id: str, data: dict[str, Any]) -> None:
    dir_path = _slot_dir(identity_id)
    file_path = _slot_config_file(identity_id)
    _ensure_private_dir(dir_path)
    payload = dict(data)
    payload.setdefault("identity_id", identity_id)
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def save(data: dict[str, Any]) -> None:
    """Writes into a slot and makes it active. Prefers `data['identity_id']`
    — the caller (init_cmd) computes it: the server-issued CliToken.id when
    pairing, `synthesize_identity_id(token)` for the --token/headless path.
    Falls back to synthesizing it from `data['token']` when omitted, so a
    plain `{"server", "token"}` payload still works. A prior active
    identity's slot is left on disk, untouched — re-pairing never destroys
    an existing identity's state."""
    identity_id = data.get("identity_id")
    if not identity_id:
        token = data.get("token")
        if not token:
            raise ValueError("save() requires data['identity_id'] or data['token']")
        identity_id = synthesize_identity_id(token)
    save_to_slot(identity_id, data)
    set_active(identity_id)


def remove() -> None:
    """Deletes the ACTIVE identity's entire slot (config + enforce +
    tail-state) and clears the active pointer (used by `yoru logout`).
    Other identities' slots are untouched. No-op if none is active."""
    identity_id = active_identity_id()
    if identity_id is None:
        return
    shutil.rmtree(_slot_dir(identity_id), ignore_errors=True)
    _active_pointer_file().unlink(missing_ok=True)


def enforce_marker_path() -> Path:
    """~/.config/yoru/identities/<active>/enforce.json. Falls back to a
    root-level path when no identity is active yet (pre-`init`) — callers
    should gate on real config existing first regardless, so this path
    should never actually get touched in that state."""
    identity_id = active_identity_id()
    if identity_id is None:
        return _root_dir() / "enforce.json"
    return _slot_dir(identity_id) / "enforce.json"


def tail_state_path() -> Path:
    """~/.config/yoru/identities/<active>/tail-state.json — same fallback
    posture as `enforce_marker_path`."""
    identity_id = active_identity_id()
    if identity_id is None:
        return _root_dir() / "tail-state.json"
    return _slot_dir(identity_id) / "tail-state.json"


def tail_metrics_path() -> Path:
    """~/.config/yoru/identities/<active>/tail-metrics.json — tailer
    observability (last-successful-post timestamp + error counter), read by
    `yoru doctor` (029e87c3). Separate file from tail-state.json: that one's
    a hot-path `dict[str, int]` (path → byte offset) touched every poll tick
    per tracked transcript; metrics are a small fixed-shape dict touched only
    on success/error, and keeping them apart avoids widening tail-state's
    type or locking contention between the two concerns."""
    identity_id = active_identity_id()
    if identity_id is None:
        return _root_dir() / "tail-metrics.json"
    return _slot_dir(identity_id) / "tail-metrics.json"
