from __future__ import annotations

import argparse
import json


def _args() -> argparse.Namespace:
    return argparse.Namespace(cmd="logout")


def _fake_client(*, ok: bool = True, raises: Exception | None = None):
    calls: list[dict] = []

    class FakeClient:
        def __init__(self, base_url: str, token: str | None = None) -> None:
            self.base_url = base_url
            self.token = token

        def logout(self) -> bool:
            calls.append({"base_url": self.base_url, "token": self.token})
            if raises is not None:
                raise raises
            return ok

    return FakeClient, calls


def test_logout_no_config_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import logout_cmd

    rc = logout_cmd.run(_args())
    assert rc == 1
    assert "not run" in capsys.readouterr().err


def test_logout_revokes_and_removes_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, logout_cmd

    config.save({"server": "http://fake", "token": "rcpt_ABC"})
    FakeClient, calls = _fake_client(ok=True)
    monkeypatch.setattr(logout_cmd, "ReceiptClient", FakeClient)

    rc = logout_cmd.run(_args())

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["token"] == "rcpt_ABC"
    assert config.exists() is False


def test_logout_already_revoked_still_removes_config(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, logout_cmd

    config.save({"server": "http://fake", "token": "rcpt_ABC"})
    FakeClient, _calls = _fake_client(ok=False)  # already revoked/invalid
    monkeypatch.setattr(logout_cmd, "ReceiptClient", FakeClient)

    rc = logout_cmd.run(_args())

    assert rc == 0
    assert config.exists() is False
    assert "already revoked" in capsys.readouterr().out


def test_logout_network_failure_keeps_local_config(monkeypatch, tmp_path, capsys):
    import httpx

    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, logout_cmd

    config.save({"server": "http://fake", "token": "rcpt_ABC"})
    FakeClient, _calls = _fake_client(raises=httpx.ConnectError("boom"))
    monkeypatch.setattr(logout_cmd, "ReceiptClient", FakeClient)

    rc = logout_cmd.run(_args())

    assert rc == 2
    # Local config stays — the token wasn't confirmed revoked, so the user
    # is still (as far as we know) logged in; don't discard state on a
    # network hiccup.
    assert config.exists() is True
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_ABC"
    assert "could not revoke" in capsys.readouterr().err.lower()
