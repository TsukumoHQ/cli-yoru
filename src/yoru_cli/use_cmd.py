from __future__ import annotations

import argparse
import sys

from . import config


def _describe(identity: dict) -> str:
    label = identity.get("identity_label") or "(no label)"
    server = identity.get("server") or "?"
    return f"{label}  [{identity['identity_id']}]  {server}"


def run(args: argparse.Namespace) -> int:
    identities = config.list_identities()
    if not identities:
        print("No paired identities — run `yoru init` first.", file=sys.stderr)
        return 1

    label = (getattr(args, "label", None) or "").strip()
    if not label:
        active = config.active_identity_id()
        for identity in identities:
            marker = "*" if identity["identity_id"] == active else " "
            print(f"{marker} {_describe(identity)}")
        return 0

    matches = [i for i in identities if i.get("identity_label") == label]
    if not matches:
        matches = [i for i in identities if i["identity_id"] == label]
    if not matches:
        print(f"error: no paired identity matches {label!r}", file=sys.stderr)
        print("Run `yoru use` with no argument to list paired identities.", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: {len(matches)} identities share label {label!r} — use the id instead:", file=sys.stderr)
        for identity in matches:
            print(f"  {_describe(identity)}", file=sys.stderr)
        return 1

    config.set_active(matches[0]["identity_id"])
    print(f"✓ active identity → {_describe(matches[0])}")
    return 0
