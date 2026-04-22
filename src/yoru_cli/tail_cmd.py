from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from . import config
from .api import ReceiptClient


def run(args: argparse.Namespace) -> int:
    cfg = config.load()
    server = getattr(args, "server", None) or (cfg.get("server") if cfg else None)
    if not server:
        print("error: no server configured (run `yoru init` or pass --server)", file=sys.stderr)
        return 2
    token = cfg.get("token") if cfg else None

    raw = sys.stdin.read().strip()
    if not raw:
        print("error: no input on stdin", file=sys.stderr)
        return 2

    events: list[dict[str, Any]]
    if raw.lstrip().startswith("["):
        events = json.loads(raw)
    else:
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]

    session_id = getattr(args, "session_id", None)
    if session_id:
        for e in events:
            e["session_id"] = session_id

    client = ReceiptClient(server, token)
    try:
        resp = client.post_events(events)
    except httpx.HTTPError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4

    print(f"HTTP {resp.status_code}")
    print(resp.text[:500])
    if 200 <= resp.status_code < 300:
        return 0
    if 400 <= resp.status_code < 500:
        return 3
    return 4
