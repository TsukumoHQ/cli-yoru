from __future__ import annotations

from receipt_cli.hook_template import HOOK_SCRIPT


def test_hook_script_has_bash_shebang() -> None:
    assert HOOK_SCRIPT.startswith("#!/usr/bin/env bash")


def test_hook_script_posts_to_events_endpoint() -> None:
    assert "api/v1/sessions/events" in HOOK_SCRIPT


def test_hook_script_forwards_bearer_token() -> None:
    assert "Authorization: Bearer" in HOOK_SCRIPT


def test_hook_script_skips_agent_relay_children_before_cfg_read() -> None:
    # The AGENT_RELAY_CHILD env gate must short-circuit BEFORE the CFG= read,
    # otherwise agent-relay-spawned sessions pollute the dashboard's receipt.db.
    gate = '[ "${AGENT_RELAY_CHILD:-0}" = "1" ] && exit 0'
    assert gate in HOOK_SCRIPT, "env gate missing from hook template"
    gate_idx = HOOK_SCRIPT.index(gate)
    cfg_idx = HOOK_SCRIPT.index('CFG="${HOME}/.config/receipt/config.json"')
    assert gate_idx < cfg_idx, "env gate must come BEFORE the CFG read"
