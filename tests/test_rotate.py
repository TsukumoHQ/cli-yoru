from __future__ import annotations

import argparse
import json


def _args(token: str | None = None, label: str | None = None, no_browser: bool = True) -> argparse.Namespace:
    return argparse.Namespace(cmd="rotate", token=token, label=label, no_browser=no_browser)


def _fake_client(*, pair_result: dict | None = None, logout_ok: bool = True, logout_raises=None):
    """Supports both the pairing surface (start/poll) and .logout() — rotate
    uses ReceiptClient for both, via two different import sites."""
    calls: dict = {"logout": []}

    class FakeClient:
        def __init__(self, base_url: str, token: str | None = None) -> None:
            self.base_url = base_url
            self.token = token

        def start_device_code(self, label: str | None = None, hostname: str | None = None) -> dict:
            return {
                "user_code": "ABCD-EFGH",
                "verification_uri": "http://fake/pair",
                "verification_uri_complete": "http://fake/pair?code=ABCD-EFGH",
                "device_code": "dev-123",
                "expires_in": 600,
                "interval": 0,
            }

        def poll_device_code(self, device_code: str) -> dict:
            return pair_result or {"status": "approved", "token": "rcpt_NEW"}

        def logout(self) -> bool:
            calls["logout"].append({"base_url": self.base_url, "token": self.token})
            if logout_raises is not None:
                raise logout_raises
            return logout_ok

    return FakeClient, calls


def _patch(monkeypatch, FakeClient) -> None:
    from yoru_cli import init_cmd, rotate_cmd

    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)
    monkeypatch.setattr(rotate_cmd, "ReceiptClient", FakeClient)
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: True)


def test_rotate_no_config_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import rotate_cmd

    rc = rotate_cmd.run(_args())
    assert rc == 1
    assert "not run" in capsys.readouterr().err


def test_rotate_with_token_revokes_old_and_saves_new(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, rotate_cmd

    config.save({"server": "http://fake", "token": "rcpt_OLD"})
    FakeClient, calls = _fake_client()
    _patch(monkeypatch, FakeClient)

    rc = rotate_cmd.run(_args(token="rcpt_XYZ"))

    assert rc == 0
    assert len(calls["logout"]) == 1
    assert calls["logout"][0]["token"] == "rcpt_OLD"
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_XYZ"
    assert data["server"] == "http://fake"


def test_rotate_via_pairing_revokes_old_and_saves_new(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, rotate_cmd

    config.save({"server": "http://fake", "token": "rcpt_OLD"})
    FakeClient, calls = _fake_client(pair_result={"status": "approved", "token": "rcpt_PAIRED"})
    _patch(monkeypatch, FakeClient)

    rc = rotate_cmd.run(_args())

    assert rc == 0
    assert len(calls["logout"]) == 1
    assert calls["logout"][0]["token"] == "rcpt_OLD"
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_PAIRED"


def test_rotate_pairing_failure_leaves_old_config_and_skips_revoke(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, rotate_cmd

    config.save({"server": "http://fake", "token": "rcpt_OLD"})
    FakeClient, calls = _fake_client(pair_result={"status": "denied"})
    _patch(monkeypatch, FakeClient)

    rc = rotate_cmd.run(_args())

    assert rc == 2
    assert calls["logout"] == []  # never got a new token — old one stays live
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_OLD"


def test_rotate_revoke_failure_does_not_block_save(monkeypatch, tmp_path, capsys):
    import httpx

    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, rotate_cmd

    config.save({"server": "http://fake", "token": "rcpt_OLD"})
    FakeClient, calls = _fake_client(logout_raises=httpx.ConnectError("boom"))
    _patch(monkeypatch, FakeClient)

    rc = rotate_cmd.run(_args(token="rcpt_XYZ"))

    assert rc == 0
    assert len(calls["logout"]) == 1
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_XYZ"
    assert "could not revoke" in capsys.readouterr().err.lower()
