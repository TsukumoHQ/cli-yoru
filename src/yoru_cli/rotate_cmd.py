"""`yoru rotate` — replace the current token with a new one, revoking the old
one server-side. Same identity/server as the existing pairing (read from
config, no --server needed) — unlike `init --force`, which re-pairs from
scratch and requires re-specifying --server.

There is no hook-token rotation endpoint server-side (only
POST /auth/api-key/{id}/rotate, for long-lived API keys) — the only way to
get a genuinely new hook-token is a fresh device-code pairing, same as
`init`. `rotate` = that pairing, plus revoking the token it replaces, minus
re-touching the hook/settings/skill files (the hook reads config.json fresh
on every invocation — no embedded token to refresh).
"""
from __future__ import annotations

import argparse
import os
import sys

from . import config
from .api import ReceiptClient
from .init_cmd import _default_label, _pair_device


def run(args: argparse.Namespace) -> int:
    cfg = config.load()
    if cfg is None:
        print("yoru init not run — nothing to rotate", file=sys.stderr)
        return 1

    server = (cfg.get("server") or "").rstrip("/")
    old_token = cfg.get("token") or ""
    if not server:
        print("config incomplete — run `yoru init` instead", file=sys.stderr)
        return 1

    new_token: str | None = getattr(args, "token", None)
    if not new_token:
        new_token = os.environ.get("YORU_TOKEN", "").strip() or None

    if not new_token:
        label = (getattr(args, "label", None) or "").strip() or _default_label()
        no_browser = bool(getattr(args, "no_browser", False))
        new_token = _pair_device(server, label, no_browser=no_browser)
        if not new_token:
            return 2

    if old_token:
        try:
            revoked = ReceiptClient(server, token=old_token).logout()
        except Exception as e:  # noqa: BLE001 — network/HTTP failure, not a bug
            print(
                f"⚠ could not revoke the previous token ({e}) — revoke it "
                "manually from the dashboard",
                file=sys.stderr,
            )
        else:
            if revoked:
                print("✓ previous token revoked", file=sys.stderr)
            else:
                print("· previous token was already revoked/invalid", file=sys.stderr)

    config.save({
        "server": server,
        "token": new_token,
    })
    print("✓ rotated — new token saved to ~/.config/yoru/config.json")
    return 0
