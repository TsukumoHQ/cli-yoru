from __future__ import annotations

from receipt_cli.hook_template import HOOK_SCRIPT


def test_hook_script_has_bash_shebang() -> None:
    assert HOOK_SCRIPT.startswith("#!/usr/bin/env bash")


def test_hook_script_posts_to_events_endpoint() -> None:
    assert "api/v1/sessions/events" in HOOK_SCRIPT


def test_hook_script_forwards_bearer_token() -> None:
    assert "Authorization: Bearer" in HOOK_SCRIPT
