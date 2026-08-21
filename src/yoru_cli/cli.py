from __future__ import annotations

import argparse

from . import __version__
from . import (
    config,
    doctor_cmd,
    enforce_cmd,
    init_cmd,
    logout_cmd,
    rotate_cmd,
    share_cmd,
    tail_cmd,
    update_cmd,
    use_cmd,
    verify_export_cmd,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yoru",
        description="Yoru — audit-grade session receipts for autonomous AI coding agents.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"yoru {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="cmd",
        required=True,
        metavar="{init,use,logout,rotate,tail,doctor,share,update,verify-export,enforce}",
    )

    p_init = subparsers.add_parser(
        "init",
        help="Install the Claude Code hook and pair a local identity under ~/.config/yoru/.",
    )
    p_init.add_argument(
        "--server",
        required=True,
        help="Backend URL of your Yoru server, e.g. https://yoru.acme.com. "
             "Yoru is self-hosted — there is no default server.",
    )
    p_init.add_argument(
        "--token",
        default=None,
        help="Pre-minted hook token (rcpt_...) — for headless/CI/server setups. "
             "Also read from $YORU_TOKEN. Without this, yoru init launches "
             "interactive device pairing.",
    )
    p_init.add_argument(
        "--label",
        default=None,
        help="Human-readable machine label shown in the dashboard "
             "(default: <hostname> · <os>).",
    )
    p_init.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't try to auto-open the pairing URL in a browser.",
    )
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing install.")

    p_use = subparsers.add_parser(
        "use",
        help="Switch the active paired identity (no arg: list paired identities).",
    )
    p_use.add_argument(
        "label",
        nargs="?",
        default=None,
        help="identity_label (or identity_id) to switch to. Omit to list.",
    )

    subparsers.add_parser(
        "logout",
        help="Revoke the current token server-side and forget it locally.",
    )

    p_rotate = subparsers.add_parser(
        "rotate",
        help="Replace the current token with a new one, revoking the old "
        "one server-side (same server as the existing pairing).",
    )
    p_rotate.add_argument(
        "--token",
        default=None,
        help="Pre-minted replacement token — for headless/CI/server setups. "
             "Also read from $YORU_TOKEN. Without this, yoru rotate launches "
             "interactive device pairing.",
    )
    p_rotate.add_argument(
        "--label",
        default=None,
        help="Human-readable machine label for the new pairing "
             "(default: <hostname> · <os>).",
    )
    p_rotate.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't try to auto-open the pairing URL in a browser.",
    )

    p_tail = subparsers.add_parser(
        "tail",
        help="Read JSON events from stdin and POST them as a batch (dev/debug).",
    )
    p_tail.add_argument(
        "--server",
        default=None,
        help="Backend URL (default: the server saved in config by `yoru init`).",
    )
    p_tail.add_argument("--session-id", default=None, help="Session id to stamp on events missing one.")

    subparsers.add_parser(
        "doctor",
        help="Diagnose the install: config, backend, token, hook.",
    )

    p_share = subparsers.add_parser(
        "share",
        help="Flip a session public and copy its yoru.sh/s/<id> URL to the clipboard.",
    )
    p_share.add_argument(
        "session_id",
        help="Session id to share (or revoke). Find it in the dashboard URL or the session header.",
    )
    p_share.add_argument(
        "--revoke",
        action="store_true",
        help="Flip the session back to private instead of sharing it.",
    )

    p_update = subparsers.add_parser(
        "update",
        help="Self-update the CLI to the latest release (pip), and refresh the hook.",
    )
    p_update.add_argument(
        "--force",
        action="store_true",
        help="Bypass the dev-build and no-downgrade guards (explicit reinstall/downgrade).",
    )
    p_update.add_argument(
        "--check",
        action="store_true",
        help="Report whether an update is available and exit — install nothing.",
    )
    p_update.add_argument(
        "--server",
        nargs="?",
        const="",
        default=None,
        metavar="URL",
        help="Check the running SERVER's version (notify-only) instead of self-updating "
        "the CLI. Bare --server uses your configured server; --server URL targets that "
        "instance. Never pulls or restarts the server.",
    )

    p_verify = subparsers.add_parser(
        "verify-export",
        help="Offline-verify a signed audit-export bundle (Ed25519/DSSE + chain).",
    )
    p_verify.add_argument(
        "bundle",
        help="Path to the signed bundle JSON (or '-' to read from stdin).",
    )
    p_verify.add_argument(
        "--pubkey-fingerprint",
        default=None,
        metavar="SHA256_HEX",
        help="Pin the deployer's public-key fingerprint (published out-of-band). "
        "Without it the check is tamper-evident only — the embedded key is "
        "trusted on faith.",
    )

    p_enforce = subparsers.add_parser(
        "enforce",
        help="Manage the OPT-IN enforcement gate that halts dangerous ops "
        "(default OFF; the passive audit trail is unaffected).",
    )
    _eg = p_enforce.add_mutually_exclusive_group()
    _eg.add_argument("--enable", action="store_true",
                     help="Install + enable the PreToolUse enforcement gate.")
    _eg.add_argument("--disable", action="store_true",
                     help="Disable the gate (no-arg `enforce` shows status).")

    # Internal: the enforcement hook invokes this with the PreToolUse event on
    # stdin; it emits a deny decision only on a match, else nothing.
    subparsers.add_parser(
        "enforce-check",
        help="(internal) decision endpoint the enforcement hook calls.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "tail" and args.server is None:
        cfg = config.load() or {}
        args.server = cfg.get("server")
        if not args.server:
            parser.error(
                "no server configured — run `yoru init --server <url>` first, "
                "or pass --server explicitly."
            )

    if args.cmd == "init":
        return init_cmd.run(args)
    if args.cmd == "use":
        return use_cmd.run(args)
    if args.cmd == "logout":
        return logout_cmd.run(args)
    if args.cmd == "rotate":
        return rotate_cmd.run(args)
    if args.cmd == "tail":
        return tail_cmd.run(args)
    if args.cmd == "doctor":
        return doctor_cmd.run(args)
    if args.cmd == "share":
        return share_cmd.run(args)
    if args.cmd == "update":
        return update_cmd.run(args)
    if args.cmd == "verify-export":
        return verify_export_cmd.run(args)
    if args.cmd == "enforce":
        return enforce_cmd.run(args)
    if args.cmd == "enforce-check":
        return enforce_cmd.check()

    parser.error(f"unknown command: {args.cmd!r}")
    return 2
