from __future__ import annotations

import argparse

from . import __version__
from . import config, doctor_cmd, init_cmd, tail_cmd

DEFAULT_SERVER = "http://localhost:8002"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="receipt",
        description="Receipt — audit-grade session receipts for autonomous AI coding agents.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"receipt {__version__}",
    )

    subparsers = parser.add_subparsers(dest="cmd", required=True, metavar="{init,tail,doctor}")

    p_init = subparsers.add_parser(
        "init",
        help="Install the Claude Code hook and write ~/.config/receipt/config.json.",
    )
    p_init.add_argument("--server", default=DEFAULT_SERVER, help=f"Backend URL (default: {DEFAULT_SERVER})")
    p_init.add_argument("--token", default=None, help="Pre-minted hook token (rcpt_...); otherwise prompt via backend.")
    p_init.add_argument("--user", default=None, help="Email/username to mint a hook token for (skip interactive prompt).")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing install.")

    p_tail = subparsers.add_parser(
        "tail",
        help="Read JSON events from stdin and POST them as a batch (dev/debug).",
    )
    p_tail.add_argument(
        "--server",
        default=None,
        help=f"Backend URL (default: value from config, else {DEFAULT_SERVER}).",
    )
    p_tail.add_argument("--session-id", default=None, help="Session id to stamp on events missing one.")

    subparsers.add_parser(
        "doctor",
        help="Diagnose the install: config, backend, token, hook.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "tail" and args.server is None:
        cfg = config.load() or {}
        args.server = cfg.get("server", DEFAULT_SERVER)

    if args.cmd == "init":
        return init_cmd.run(args)
    if args.cmd == "tail":
        return tail_cmd.run(args)
    if args.cmd == "doctor":
        return doctor_cmd.run(args)

    parser.error(f"unknown command: {args.cmd!r}")
    return 2
