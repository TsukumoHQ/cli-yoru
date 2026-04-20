from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config
from .api import ReceiptClient
from .hook_template import HOOK_SCRIPT


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

    print("\u2713 config   \u2192 ~/.config/receipt/config.json")
    print("\u2713 hook     \u2192 ~/.claude/hooks/receipt.sh")
    print("Next: run Claude Code normally; first event streams to /sessions/events.")
    return 0
