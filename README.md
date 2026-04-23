# yoru-cli

Audit trail for autonomous coding agents. `yoru-cli` installs a Claude Code hook that streams every tool call, file edit, and red-flag event to a Yoru backend — Cloud (yoru.sh) or your own self-hosted server.

[![PyPI](https://img.shields.io/pypi/v/yoru-cli.svg)](https://pypi.org/project/yoru-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

## Install

```bash
pip install yoru-cli
yoru init
```

`yoru init` pairs your machine with Yoru Cloud (or any server you point it at), writes `~/.claude/hooks/yoru.sh`, and registers the hook in `~/.claude/settings.json`. Your next Claude Code session streams to Yoru automatically.

Python 3.10+. Single runtime dep: `httpx`.

## Usage

```bash
yoru init                              # pair against https://api.yoru.sh (default)
yoru init --server https://yoru.acme   # pair against your self-hosted instance
yoru init --token YORU_xxx --force     # non-interactive (CI / scripted rotation)

yoru tail < events.jsonl               # post a batch of events (dev / debug)

yoru --version
yoru --help
```

Exit codes: `0` ok · `1` already installed (use `--force`) · `2` auth failed · `3` 4xx · `4` 5xx / network.

## Hook contract

The installed hook is a ~20-line Bash script that `POST`s one JSON event per Claude Code `PostToolUse` firing:

```json
{"session_id": "…", "user": "…", "kind": "tool_use", "tool": "Bash", "content": "…"}
```

`curl --max-time 2 || true` — the hook never blocks Claude Code, even if the backend is down.

## Self-hosting the server

The CLI is MIT; the Yoru backend + dashboard are AGPL-3.0 and live at **[github.com/yoru-sh/yoru](https://github.com/yoru-sh/yoru)**. `docker-compose up`, point `yoru init --server` at it, done.

## License

[MIT](./LICENSE). Copyright (c) 2026 Yoru authors.
