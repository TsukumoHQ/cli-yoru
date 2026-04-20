"""Bundled Claude Code hook script — written verbatim by `receipt init`.

Shape is frozen in vault/CLI-V0-DESIGN.md §4. Bash (not Python) for fast startup;
`curl --max-time 2 || true` keeps the hook from ever blocking the agent.
v0 posts one event per tool call — batching is a v1 optimization.
"""

HOOK_SCRIPT: str = """#!/usr/bin/env bash
# Claude Code PostToolUse / PreToolUse hook — Receipt
set -euo pipefail
# Skip events when AGENT_RELAY_CHILD=1 (agent-relay-spawned children — prevents dashboard noise)
[ "${AGENT_RELAY_CHILD:-0}" = "1" ] && exit 0
CFG="${HOME}/.config/receipt/config.json"
[ -r "$CFG" ] || exit 0          # silent no-op if uninstalled
SERVER=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/receipt/config.json")))["server"])')
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/receipt/config.json")))["token"])')
# Claude Code pipes the tool event as JSON on stdin; forward as a 1-event batch.
# SessionStart/Stop events carry `hook_event_name` (no `tool_name`) → translate to `kind`.
BODY=$(python3 -c 'import sys,json
e=json.loads(sys.stdin.read())
hen=e.get("hook_event_name")
if hen=="SessionStart": e["kind"]="session_start"
elif hen=="Stop": e["kind"]="session_end"
print(json.dumps({"events":[e]}))')
curl -sS --max-time 2 -X POST "${SERVER}/api/v1/sessions/events" \\
  -H "Authorization: Bearer ${TOKEN}" \\
  -H "Content-Type: application/json" \\
  -d "${BODY}" >/dev/null 2>&1 || true   # never block the agent
"""
