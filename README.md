# yoru-cli

The Claude Code hook installer for Yoru. One command installs a `PostToolUse` hook that forwards every tool call, file edit, and red-flag event to a Yoru backend. Your next session shows up in the dashboard automatically.

[![PyPI](https://img.shields.io/pypi/v/yoru-cli.svg)](https://pypi.org/project/yoru-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

## Install

```bash
pip install yoru-cli
yoru init --server https://yoru.acme.com
```

Yoru is **self-hosted** — point the CLI at your own backend with `--server`. `yoru init` opens a pairing URL in your browser, writes `~/.claude/hooks/yoru.sh`, and registers the hook in `~/.claude/settings.json`. Don't have a server yet? [Stand one up](https://github.com/yoru-sh/yoru) — `docker-compose up`.

Requires Python 3.10+. One runtime dep: `httpx`.

## Usage

```bash
yoru init --server https://yoru.acme.com       # pair against your server (--server required)
yoru init --server https://yoru.acme.com --token yoru_xxx --force   # non-interactive (CI / scripted rotation)

yoru tail < events.jsonl                # post a batch of events (debug)

yoru --version
yoru --help
```

Exit codes: `0` ok · `1` already installed (use `--force`) · `2` auth failed · `3` 4xx · `4` 5xx/network.

## How the hook works

The installed script is ~20 lines of Bash that `POST`s one JSON event per Claude Code tool call:

```json
{"session_id": "…", "user": "…", "kind": "tool_use", "tool": "Bash", "content": "…"}
```

It uses `curl --max-time 2 || true` so a Yoru outage never stalls your terminal — Claude Code doesn't notice the hook is even there.

## Self-host the server

The CLI is MIT. The server (backend + dashboard) is AGPL-3.0 and lives at **[github.com/yoru-sh/yoru](https://github.com/yoru-sh/yoru)**. `docker-compose up`, then `yoru init --server https://your-host` — done. There is no hosted Yoru; you run it.

## License

[MIT](./LICENSE) · Copyright (c) 2026 Yoru authors. Issues and PRs welcome at [github.com/yoru-sh/cli](https://github.com/yoru-sh/cli).
