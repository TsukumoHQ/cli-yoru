# receipt-cli

Receipt — audit-grade session receipts for autonomous AI coding agents.

One command installs a Claude Code hook that streams every tool call into the Receipt backend; the dashboard turns that feed into a signed session receipt.

## Install

```bash
pip install -e .          # from the monorepo
# or once published:
# pip install receipt-cli
```

Requires Python 3.10+. Only runtime dep is `httpx`.

## Usage

```bash
receipt init                         # writes ~/.claude/hooks/receipt.sh + ~/.config/receipt/config.json (0600)
receipt init --server http://localhost:8002 --user you@example.com    # non-interactive (CI/smoke)
receipt init --server http://localhost:8002 --token rcpt_xxx --force

receipt tail                         # reads JSON events on stdin, POSTs them as a batch (dev/debug)
echo '{"session_id":"s1","user":"dev","kind":"tool_use","tool":"Bash"}' | receipt tail

receipt --version                    # receipt 0.1.0
receipt --help
```

Exit codes: `0` ok, `1` already installed without `--force`, `2` auth failed, `3` 4xx, `4` 5xx/network.

## Spec

Frozen design doc: `vault/CLI-V0-DESIGN.md` in the monorepo (§1 layout, §2 pyproject, §3 subcommands, §4 hook shape, §5 auth, §7 event schema).
