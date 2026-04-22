# yoru-cli

Yoru — audit-grade session receipts for autonomous AI coding agents.

One command installs a Claude Code hook that streams every tool call into the Yoru backend; the dashboard turns that feed into a signed session receipt.

## Install

```bash
pip install -e .          # from the monorepo
# or once published:
# pip install yoru-cli
```

Requires Python 3.10+. Only runtime dep is `httpx`.

## Usage

```bash
yoru init                         # writes ~/.claude/hooks/yoru.sh + ~/.config/yoru/config.json (0600)
yoru init --server http://localhost:8002 --user you@example.com    # non-interactive (CI/smoke)
yoru init --server http://localhost:8002 --token rcpt_xxx --force

yoru tail                         # reads JSON events on stdin, POSTs them as a batch (dev/debug)
echo '{"session_id":"s1","user":"dev","kind":"tool_use","tool":"Bash"}' | yoru tail

receipt --version                    # receipt 0.1.0
receipt --help
```

Exit codes: `0` ok, `1` already installed without `--force`, `2` auth failed, `3` 4xx, `4` 5xx/network.

## Spec

Frozen design doc: `vault/CLI-V0-DESIGN.md` in the monorepo (§1 layout, §2 pyproject, §3 subcommands, §4 hook shape, §5 auth, §7 event schema).
