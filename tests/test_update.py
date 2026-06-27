"""Tests for `yoru update` — the Fleet Auto-Updater contract.

Network + pip are mocked: _fetch_latest_tag, _pip_install and the side-asset
refresh are monkeypatched so nothing leaves the process.
"""
from __future__ import annotations

import argparse

import httpx
import pytest

from yoru_cli import init_cmd, update_cmd


def _args(force: bool = False, check: bool = False) -> argparse.Namespace:
    return argparse.Namespace(force=force, check=check)


@pytest.fixture()
def calls(monkeypatch):
    """Record pip + side-asset calls; default latest tag = v0.1.3."""
    rec: dict = {"pip": [], "refresh": 0}
    monkeypatch.setattr(update_cmd, "_pip_install", lambda v: rec["pip"].append(v) or 0)
    monkeypatch.setattr(
        init_cmd, "refresh_hook_assets",
        lambda: rec.__setitem__("refresh", rec["refresh"] + 1) or (None, None),
    )
    monkeypatch.setattr(update_cmd, "_fetch_latest_tag", lambda **_: "v0.1.3")
    return rec


# ---------- unit: semver + dev-build ----------

@pytest.mark.parametrize("tag,expected", [
    ("v0.1.3", (0, 1, 3)),
    ("0.1.3", (0, 1, 3)),
    ("0.1.3+dev", (0, 1, 3)),
    ("1.2", (1, 2, 0)),
    ("nope", None),
])
def test_parse_semver(tag, expected):
    assert update_cmd._parse_semver(tag) == expected


@pytest.mark.parametrize("v,is_dev", [
    ("0.1.2", False), ("0.0.0+dev", True), ("0.1.2+gdeadbee", True), ("0.1.2.dev1", True),
])
def test_is_dev_build(v, is_dev):
    assert update_cmd._is_dev_build(v) is is_dev


# ---------- behavior ----------

def test_update_happy_path(monkeypatch, calls, capsys):
    monkeypatch.setattr(update_cmd, "__version__", "0.1.2")
    rc = update_cmd.run(_args())
    assert rc == 0
    assert calls["pip"] == ["0.1.3"]          # pinned, 'v' stripped for pip
    assert calls["refresh"] == 1               # side-assets refreshed
    assert "updated to v0.1.3" in capsys.readouterr().out


def test_update_noop_when_current(monkeypatch, calls, capsys):
    monkeypatch.setattr(update_cmd, "__version__", "0.1.3")
    rc = update_cmd.run(_args())
    assert rc == 0
    assert calls["pip"] == []                  # no install
    assert "already up to date" in capsys.readouterr().out


def test_update_no_downgrade(monkeypatch, calls, capsys):
    monkeypatch.setattr(update_cmd, "__version__", "0.2.0")
    rc = update_cmd.run(_args())
    assert rc == 0
    assert calls["pip"] == []
    assert "ahead" in capsys.readouterr().out


def test_force_overrides_no_downgrade(monkeypatch, calls):
    monkeypatch.setattr(update_cmd, "__version__", "0.2.0")
    assert update_cmd.run(_args(force=True)) == 0
    assert calls["pip"] == ["0.1.3"]


def test_dev_build_guard_refuses(monkeypatch, calls, capsys):
    monkeypatch.setattr(update_cmd, "__version__", "0.0.0+dev")
    rc = update_cmd.run(_args())
    assert rc == 0
    assert calls["pip"] == []                  # refused
    assert "dev/source build" in capsys.readouterr().out


def test_force_overrides_dev_guard(monkeypatch, calls):
    monkeypatch.setattr(update_cmd, "__version__", "0.0.0+dev")
    assert update_cmd.run(_args(force=True)) == 0
    assert calls["pip"] == ["0.1.3"]


def test_check_reports_without_installing(monkeypatch, calls, capsys):
    monkeypatch.setattr(update_cmd, "__version__", "0.1.2")
    rc = update_cmd.run(_args(check=True))
    assert rc == 0
    assert calls["pip"] == []                  # --check never installs
    assert "update available: 0.1.2 → v0.1.3" in capsys.readouterr().out


def test_network_failure_is_failsafe_noop(monkeypatch, capsys):
    monkeypatch.setattr(update_cmd, "__version__", "0.1.2")

    def _boom(**_):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(update_cmd, "_fetch_latest_tag", _boom)
    monkeypatch.setattr(update_cmd, "_pip_install", lambda v: pytest.fail("must not install"))
    rc = update_cmd.run(_args())
    assert rc == 0                             # offline = clean no-op, not a crash
    assert "could not reach GitHub" in capsys.readouterr().out
