"""`yoru doctor` — diagnostic subcommand.

Read-only check of the install:
  1. config.json present                   → else exit 1
  2. backend /health/ready reachable       → else exit 2
  3. hook-token valid (GET /hook-tokens)   → else exit 3 on 401
  4. ~/.claude/hooks/yoru.sh is 0755    → else exit 4

Prints a ✓ line to stdout as each stage passes, so a failure leaves a visible
trail of what already worked (✓ config / ✓ backend / token revoked → the break
is obvious). Failures go to stderr with a short human-readable reason, then a
non-zero exit. No fixes attempted.
"""
from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path

import httpx

from . import config


def _token_suffix(token: str) -> str:
    tail = token[-4:] if len(token) >= 4 else token
    return f"rcpt_...{tail}"


def _hook_path() -> Path:
    return Path.home() / ".claude" / "hooks" / "yoru.sh"


def run(args: argparse.Namespace) -> int:  # noqa: ARG001 — argparse hands args, unused for now
    # 1. config
    cfg = config.load()
    if cfg is None:
        print("yoru init not run", file=sys.stderr)
        return 1
    server = (cfg.get("server") or "").rstrip("/")
    token = cfg.get("token") or ""
    if not server or not token:
        print("yoru init not run", file=sys.stderr)
        return 1
    # Each stage prints its check the moment it passes, so a later failure
    # leaves a visible trail of what DID work.
    print(f"✓ config at ~/.config/yoru/config.json (token {_token_suffix(token)})")

    # 2. backend /health/ready
    try:
        r = httpx.get(f"{server}/health/ready", timeout=5.0)
    except httpx.HTTPError:
        print(f"backend unreachable at {server}", file=sys.stderr)
        return 2
    if r.status_code != 200:
        print(f"backend unreachable at {server}", file=sys.stderr)
        return 2
    print(f"✓ backend {server} reachable")

    # 3. hook-token validity
    try:
        r = httpx.get(
            f"{server}/api/v1/auth/hook-tokens",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
    except httpx.HTTPError:
        print(f"backend unreachable at {server}", file=sys.stderr)
        return 2
    if r.status_code == 401:
        print("token revoked or expired", file=sys.stderr)
        return 3
    if r.status_code != 200:
        print(
            f"hook-token check failed: HTTP {r.status_code}",
            file=sys.stderr,
        )
        return 3
    user = cfg.get("user") or "authenticated"
    print(f"✓ hook-token valid (user: {user})")

    # 4. hook file + perms
    hook = _hook_path()
    if not hook.is_file():
        print("hook file missing or not 0755", file=sys.stderr)
        return 4
    mode = stat.S_IMODE(hook.stat().st_mode)
    if mode != 0o755:
        print("hook file missing or not 0755", file=sys.stderr)
        return 4
    print("\u2713 hook installed at ~/.claude/hooks/yoru.sh")
    return 0
