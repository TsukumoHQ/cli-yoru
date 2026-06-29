"""Bundled PUBLIC end-user Claude Code skill — written verbatim by `yoru init`
to ``~/.claude/skills/yoru/SKILL.md`` (and refreshed by `yoru update`).

This is the PUBLIC "how to self-host + use yoru" skill, NOT the private
yoru-dev maintainer persona. It teaches a fresh Claude Code session to drive
yoru setup + usage for the user ("tout copier à Claude Code, il fait tout").

Hard rails baked into the copy (keep them true if you edit):
  - yoru is SELF-HOSTED ONLY — no hosted/cloud/sign-up. `--server` is required.
  - No private paths / internal fleet references (public/AGPL split).
  - Public sessions are opt-in (`yoru share`) and PII-redacted — stay honest
    about the confidentiality model.
"""

SKILL_NAME = "yoru"

SKILL_MD: str = """---
name: yoru
description: >-
  Set up and use yoru — self-hosted, audit-grade session receipts for Claude
  Code and other autonomous coding agents. Use when the user wants to install
  or configure yoru, stand up / start their own yoru backend, verify the
  install, record a coding session, share a public session receipt (/s/:id),
  replay a session, or understand the redaction / privacy model. yoru is
  self-hosted only — there is no hosted service or sign-up.
---

# yoru — self-hosted session receipts

yoru turns every Claude Code session into an audit-grade receipt: every tool
call, file edit, prompt, and red-flag event streams to a dashboard the user
runs themselves, plus a per-session A–F grade (Throughput / Reliability /
Safety). It is **self-hosted only** — the user runs the backend on their own
box. There is no hosted yoru, no cloud, no sign-up. Never tell the user to
"sign up" or point them at a managed service.

Two pieces:
- **`yoru` CLI** (PyPI `yoru-cli`, MIT) — installs a Claude Code hook that
  forwards events to a backend.
- **the backend + dashboard** (AGPL, `github.com/TsukumoHQ/yoru`) — the user
  stands this up themselves; it ingests events, scores sessions, and serves the
  public session pages at `/s/:id`.

When this skill is active, drive the user through whichever part they need.
Run real commands, read their output, and fix problems — don't just narrate.

## 1. Is the CLI installed?

```bash
yoru --version
```

If `yoru` is not found, install it (needs Python ≥ 3.10):

```bash
pipx install yoru-cli        # or: uv tool install yoru-cli
```

Use `pipx`/`uv`, not a bare `pip install` — on Homebrew/Debian Python the
latter fails with `error: externally-managed-environment` (PEP 668). If the
user has neither, a venv works: `python3 -m venv .venv && .venv/bin/pip install
yoru-cli`.

## 2. Does the user have a backend to point at?

yoru needs a backend URL — there is **no default server**. Ask the user for
their yoru server URL (e.g. `https://yoru.example.com`, or
`http://localhost:8002` for a local one).

If they don't have one yet, help them stand it up from the AGPL server repo:

```bash
git clone https://github.com/TsukumoHQ/yoru && cd yoru
cp backend/.env.example backend/.env   # keyless self-host defaults work as-is
make dev                               # API on :8002, dashboard on :5173
```

First run shows a wizard (or `make setup` headless) to create an admin account
and pick a database. Defaults are local-first: SQLite + local auth, zero
external services. Postgres / SMTP / OAuth are all optional.

## 3. Pair this machine + install the hook

```bash
yoru init --server <THEIR_SERVER_URL>
```

`--server` is required. `yoru init`:
- writes `~/.config/yoru/config.json` (server + a paired device token),
- writes the hook to `~/.claude/hooks/yoru.sh`,
- registers it in `~/.claude/settings.json` (PostToolUse / SessionStart / Stop).

It opens a browser pairing page; the user approves the device there. For a
headless box, pass a token instead: `yoru init --server <url> --token <token>`
(or set `YORU_TOKEN`). Re-running is safe — add `--force` to repoint.

## 4. Verify

```bash
yoru doctor
```

It prints a ✓ per stage (config → backend reachable → token valid → hook
installed). If a stage fails, the line just before the error tells you what
already worked — fix that stage:
- *yoru init not run* → run step 3.
- *backend unreachable* → is the server URL right and the backend up?
- *token revoked or expired* → re-run `yoru init --force`.
- *hook file missing* → re-run `yoru init` (or `yoru update`).

## 5. Record a session

Nothing extra to do — just use Claude Code normally. The hook streams each
event to the backend, and the session appears in the dashboard automatically
with its A–F grade. To refresh the hook after a CLI upgrade: `yoru update`.

yoru flags six kinds of red-flag events at ingest, so the user can audit them:
`secret` (leaked keys), `env` (writes to `.env*`), `shell` (`rm`/`dd`/`mkfs`),
`db` (`DROP`/`TRUNCATE`/unscoped `DELETE`), `migration` (edits under
`migrations/`), `ci` (edits under `.github/workflows/`, Dockerfile, etc.).

## 6. Share a session (and the privacy model)

By default every session is **private** — `/s/:id` returns 404 on purpose.
Sharing is explicit and per-session:

```bash
yoru share <SESSION_ID> --server <THEIR_SERVER_URL>
```

This flips that one session public and gives a shareable URL (on the user's own
host, e.g. `https://yoru.example.com/s/<id>`). The public page is the session
trail + grade, with a replay view of the event timeline.

Be honest with the user about what sharing exposes: a public session is
**PII-redacted on the wire** — the viewer sees a session id, not the user's
email — but it DOES show the tool calls, file paths, and any flagged content
captured in that session. Only share sessions that are safe to make public, and
only ones the user explicitly chooses. Sharing is opt-in and reversible from the
dashboard.

## Honesty rails

- Self-hosted only. Never promise hosting, "sign up", "managed", "free
  forever", or cloud pricing.
- `--server` is always required; there is no default.
- Don't overstate redaction — emails are stripped, but shared trails still
  contain whatever the agent did. Say so.
"""
