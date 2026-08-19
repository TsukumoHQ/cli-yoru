"""Bundled PreToolUse enforcement-gate hook — written by `yoru enforce --enable`.

SEPARATE from the passive audit streamer (`yoru.sh`); this script never touches
it. The gate is opt-in and fail-open: it blocks ONLY when `yoru enforce-check`
emits a deny decision on stdout, and any error/timeout/uncertainty falls through
to allow the tool call. Bash (not Python) for fast startup, mirroring the
streamer.
"""

ENFORCE_HOOK_SCRIPT: str = """#!/usr/bin/env bash
# yoru OPT-IN enforcement gate (PreToolUse). Default OFF — `yoru enforce-check`
# no-ops unless enforcement is enabled (YORU_ENFORCE env or the policy marker).
# FAIL-OPEN by construction: NO `set -e` (a failing stage must fall through to
# allow, never abort), and the checker expresses a block via a deny decision on
# stdout, never via a nonzero exit — so every error path here allows.
set -uo pipefail
# Skip agent-relay-spawned children, matching the streamer.
[ "${AGENT_RELAY_CHILD:-0}" = "1" ] && exit 0
INPUT=$(cat)
# Bounded: cap the check so a slow policy eval can never stall the terminal.
# (settings.json also sets a per-hook `timeout` as an outer bound.) On any
# timeout / missing binary / failure the `|| exit 0` allows the tool call.
printf '%s' "$INPUT" | timeout 5 yoru enforce-check 2>/dev/null || exit 0
"""
