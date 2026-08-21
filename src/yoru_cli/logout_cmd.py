"""`yoru logout` — revoke the current token server-side and forget it locally.

Before this command existed, the only path off a stale/unwanted pairing was
`yoru init --force`, which re-pairs to a NEW identity but never revoked the
one it replaced (see the `init --force` fix — a related but distinct gap:
that one is about not orphaning the OLD token when getting a NEW one; this
one is about actually signing out with no new pairing at all). `doctor`
used to point users at `init --force` for a revoked-token failure, which is
the wrong tool for "I just want to sign out."
"""
from __future__ import annotations

import argparse
import sys

from . import config
from .api import ReceiptClient


def run(args: argparse.Namespace) -> int:  # noqa: ARG001 — no flags today
    cfg = config.load()
    if cfg is None:
        print("yoru init not run — nothing to log out of", file=sys.stderr)
        return 1

    server = (cfg.get("server") or "").rstrip("/")
    token = cfg.get("token") or ""
    if not server or not token:
        print("config incomplete — nothing to log out of", file=sys.stderr)
        return 1

    try:
        revoked = ReceiptClient(server, token=token).logout()
    except Exception as e:  # noqa: BLE001 — network/HTTP failure, not a bug
        print(
            f"could not revoke token server-side ({e}) — local config left "
            "in place so you can retry; revoke it manually from the "
            "dashboard if this keeps failing",
            file=sys.stderr,
        )
        return 2

    config.remove()
    if revoked:
        print("✓ token revoked, local config removed")
    else:
        print("· token was already revoked/invalid, local config removed")
    return 0
