"""`yoru share` — public-share toggle for an agent session. Issue #80.

Two flows:

  yoru share <session-id>            # flip public, copy URL
  yoru share --revoke <session-id>   # flip back to private

Consent: the first-ever share on an account prompts for explicit
acknowledgment ("prompts and file paths will be visible"). The decision
is persisted server-side (users.share_consent_given_at) so it follows
the account across machines — a second machine running `yoru share`
won't re-ask. Previously stored in ~/.config/yoru/share-confirmed; the
local file is obsolete and is now ignored if it exists.

Clipboard: we try `pbcopy` on Darwin and `xclip -selection clipboard`
elsewhere; on failure we just print the URL loudly enough to copy from
terminal. No pyperclip dep on purpose — the std-lib `subprocess` path
works everywhere we ship.
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys

import httpx

from . import config
from .api import ReceiptClient


def _prompt_consent() -> bool:
    """Interactive one-time acknowledgment. Non-interactive callers (CI,
    piped stdin) can set YORU_SHARE_CONSENT=yes to skip."""
    if os.environ.get("YORU_SHARE_CONSENT", "").strip().lower() in ("1", "yes", "true"):
        return True
    if not sys.stdin.isatty():
        print(
            "error: `yoru share` needs an interactive TTY the first time so "
            "you can confirm the public-disclosure terms. Re-run from a "
            "terminal, or set YORU_SHARE_CONSENT=yes if you're scripting.",
            file=sys.stderr,
        )
        return False

    print()
    print("  Make this session publicly shareable?")
    print()
    print("  Visible publicly: prompts, tool calls, file paths, red flags, grade.")
    print("  Always redacted: content of any event flagged secret_* (AWS / Stripe /")
    print("                   JWT / SSH / Anthropic key / Postgres URL / etc.).")
    print()
    print("  You'll only be asked once per account. Revoke any specific share at any")
    print("  time with `yoru share --revoke <id>`.")
    print()
    try:
        ans = input("  Share? [y/N]: ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy. Returns True if a copy command succeeded."""
    if platform.system() == "Darwin":
        cmd = ["pbcopy"]
    elif platform.system() == "Linux":
        # xclip is the most common; wl-copy (Wayland) would be a worthy add.
        cmd = ["xclip", "-selection", "clipboard"]
    else:
        return False
    try:
        subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=2)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _format_tweet(url: str) -> str:
    """Pre-filled tweet text the user can paste after copying the URL.

    Intentionally short — the *link* carries most of the payload; the
    tweet needs just enough framing that a dev scrolling past knows what
    they're looking at.
    """
    return (
        f"look what my Claude Code session just did — {url}\n\n"
        "(yoru.sh — audit-grade trails for AI coding agents)"
    )


def run(args: argparse.Namespace) -> int:
    cfg = config.load()
    if cfg is None:
        print("error: not configured — run `yoru init` first.", file=sys.stderr)
        return 1
    server = (cfg.get("server") or "").rstrip("/")
    token = cfg.get("token") or ""
    if not server or not token:
        print("error: config missing server or token — re-run `yoru init`.", file=sys.stderr)
        return 1

    session_id = (getattr(args, "session_id", None) or "").strip()
    if not session_id:
        print(
            "error: missing <session-id>. Usage: yoru share <session-id> "
            "[--revoke]",
            file=sys.stderr,
        )
        return 2
    revoke = bool(getattr(args, "revoke", False))

    client = ReceiptClient(server, token=token)

    # Revoke path — no consent check, no clipboard. Fast and quiet.
    if revoke:
        try:
            resp = client.revoke_share(session_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                print(
                    f"error: session {session_id} not found (or not owned by this account).",
                    file=sys.stderr,
                )
                return 3
            print(f"error: revoke failed: {e}", file=sys.stderr)
            return 4
        except httpx.HTTPError as e:
            print(f"error: revoke failed: {e}", file=sys.stderr)
            return 4
        print(f"✓ session {session_id} is private again.")
        return 0

    # Share path — consent is server-side now (one stamp per account).
    try:
        consent = client.get_share_consent()
    except httpx.HTTPError as e:
        print(f"error: couldn't check share consent: {e}", file=sys.stderr)
        return 4

    if not consent.get("consented"):
        ok = _prompt_consent()
        if not ok:
            print("  aborted — session stays private.")
            return 0
        try:
            client.post_share_consent()
        except httpx.HTTPError as e:
            print(
                f"error: couldn't save consent (not stamping server-side): {e}",
                file=sys.stderr,
            )
            return 4

    try:
        resp = client.share_session(session_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            print(
                f"error: session {session_id} not found (or not owned by this account).",
                file=sys.stderr,
            )
            return 3
        print(f"error: share failed: {e}", file=sys.stderr)
        return 4
    except httpx.HTTPError as e:
        print(f"error: share failed: {e}", file=sys.stderr)
        return 4

    url = resp.get("public_url") or ""
    if not url:
        print("error: backend returned no public_url — is the session actually public?", file=sys.stderr)
        return 4

    copied = _copy_to_clipboard(url)

    print()
    print(f"  ✓ public  {url}")
    if copied:
        print("    (copied to clipboard)")
    print()
    print("    suggested tweet:")
    print(f"    {_format_tweet(url)}")
    print()
    print(f"    revoke: yoru share --revoke {session_id}")
    return 0
