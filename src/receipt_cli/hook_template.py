"""Bundled Claude Code hook script — written verbatim by `receipt init`.

Shape is frozen in vault/CLI-V0-DESIGN.md §4. Bash (not Python) for fast startup;
`curl --max-time 2 || true` keeps the hook from ever blocking the agent.
v0 posts one event per tool call — batching is a v1 optimization.

Subscribed hook events (configured in ~/.claude/settings.json):
  - SessionStart        → kind=session_start
  - UserPromptSubmit    → kind=message (prompt text captured)
  - PostToolUse         → kind inferred (tool_use | file_change)
  - Notification        → kind=message (permission/input prompts)
  - Stop                → kind=session_end
  - SubagentStop        → kind=message (lightweight, doesn't close session)

PreToolUse is NOT subscribed — PostToolUse carries the same tool_input plus
tool_response, so subscribing to both doubles traffic for no gain.
"""

HOOK_SCRIPT: str = """#!/usr/bin/env bash
# Claude Code hook — Receipt ingest. Handles all subscribed hook events.
set -euo pipefail
# Skip events when AGENT_RELAY_CHILD=1 (agent-relay-spawned children — prevents dashboard noise)
[ "${AGENT_RELAY_CHILD:-0}" = "1" ] && exit 0
CFG="${HOME}/.config/receipt/config.json"
[ -r "$CFG" ] || exit 0          # silent no-op if uninstalled
SERVER=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/receipt/config.json")))["server"])')
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/receipt/config.json")))["token"])')
# Claude Code pipes the hook event as JSON on stdin. We parse the original
# payload, attach it verbatim to `raw` (so the backend sees tool_input /
# tool_response — Pydantic drops unknown top-level fields otherwise), then
# mutate the top-level envelope with `kind` + extracted `content` for the
# renderer-friendly shape.
#
# Routing context (Phase C): hook detects cwd + git remote/branch and ships
# them with every event so the server can route the session to the right
# workspace. `git` calls are cached per-session in $TMPDIR to keep the hook
# fast on hot PostToolUse paths.
BODY=$(python3 -c 'import sys,json,os,subprocess
original=json.loads(sys.stdin.read())
e=dict(original)
e["raw"]=original

# Routing context — cwd from Claude payload (reliable), git info cached.
cwd = original.get("cwd")
if isinstance(cwd, str) and cwd:
    e["cwd"] = cwd

sess_id = original.get("session_id") or original.get("sessionId") or ""
cache_dir = os.environ.get("TMPDIR", "/tmp")
cache = os.path.join(cache_dir, f".receipt-ctx-{sess_id}.env") if sess_id else None
git_remote = None
git_branch = None
if cache and os.path.exists(cache):
    try:
        for line in open(cache, encoding="utf-8"):
            k,_,v = line.strip().partition("=")
            if k == "git_remote": git_remote = v or None
            elif k == "git_branch": git_branch = v or None
    except Exception:
        pass
if (git_remote is None or git_branch is None) and isinstance(cwd, str) and cwd:
    def _run(args):
        try:
            return subprocess.check_output(args, cwd=cwd, stderr=subprocess.DEVNULL, timeout=1).decode().strip() or None
        except Exception:
            return None
    if git_remote is None:
        git_remote = _run(["git","remote","get-url","origin"])
    if git_branch is None:
        git_branch = _run(["git","rev-parse","--abbrev-ref","HEAD"])
    if cache:
        try:
            with open(cache, "w", encoding="utf-8") as f:
                if git_remote: f.write(f"git_remote={git_remote}\\n")
                if git_branch: f.write(f"git_branch={git_branch}\\n")
        except Exception:
            pass
if git_remote: e["git_remote"] = git_remote
if git_branch: e["git_branch"] = git_branch

hen=e.get("hook_event_name")
if hen=="SessionStart":
    e["kind"]="session_start"
elif hen=="UserPromptSubmit":
    e["kind"]="message"
    e["tool"]="user"
    p=e.get("prompt")
    if isinstance(p,str) and p: e["content"]=p[:2000]
elif hen=="Notification":
    e["kind"]="message"
    e["tool"]="notification"
    m=e.get("message")
    if isinstance(m,str) and m: e["content"]=m[:2000]
elif hen=="SubagentStop":
    e["kind"]="message"
    e["tool"]="subagent"
    e["content"]="subagent stopped"
elif hen=="Stop":
    e["kind"]="session_end"
# PostToolUse / PreToolUse: leave kind unset → backend _infer_kind() from tool
print(json.dumps({"events":[e]}))')
curl -sS --max-time 2 -X POST "${SERVER}/api/v1/sessions/events" \\
  -H "Authorization: Bearer ${TOKEN}" \\
  -H "Content-Type: application/json" \\
  -d "${BODY}" >/dev/null 2>&1 || true   # never block the agent
"""


# Hook subscriptions to write into ~/.claude/settings.json.
# Each entry is a (hook_event_name, description) pair; the installer writes a
# single `matcher:"*"` entry per event pointing at ~/.claude/hooks/receipt.sh.
HOOK_SUBSCRIPTIONS: list[tuple[str, str]] = [
    ("SessionStart",     "capture session boundary"),
    ("UserPromptSubmit", "capture user prompts (message events)"),
    ("PostToolUse",      "capture tool_use + file_change"),
    ("Notification",     "capture permission/input prompts"),
    ("Stop",             "capture session close"),
    ("SubagentStop",     "capture subagent lifecycle"),
]
