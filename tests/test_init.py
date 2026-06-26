from __future__ import annotations

import argparse
import json
import stat


def _args(
    server: str,
    token: str | None = None,
    force: bool = False,
    user: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(cmd="init", server=server, token=token, force=force, user=user)


def test_init_writes_config_and_hook(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, init_cmd

    assert config.exists() is False

    rc = init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    assert rc == 0

    cfg_path = tmp_path / ".config" / "yoru" / "config.json"
    assert cfg_path.is_file()
    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600
    data = json.loads(cfg_path.read_text())
    assert data["server"] == "http://fake"
    assert data["token"] == "rcpt_ABC"
    assert "created_at" in data

    cfg_dir = tmp_path / ".config" / "yoru"
    assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700

    hook_path = tmp_path / ".claude" / "hooks" / "yoru.sh"
    assert hook_path.is_file()
    assert stat.S_IMODE(hook_path.stat().st_mode) == 0o755
    body = hook_path.read_text()
    assert "api/v1/sessions/events" in body
    assert "rcpt_ABC" in cfg_path.read_text()


def test_init_already_installed_without_force(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    assert init_cmd.run(_args(server="http://fake", token="rcpt_ABC")) == 0
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_XYZ"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "Already installed" in err


def test_init_with_force_overwrites(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_XYZ", force=True))
    assert rc == 0
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_XYZ"


def _fake_pairing_client(poll_result: dict):
    """A ReceiptClient stand-in for the device-code pairing flow. start returns a
    fixed pairing payload; poll returns `poll_result` (interval=0 so no sleep)."""
    class FakeClient:
        def __init__(self, server: str) -> None:
            self.server = server

        def start_device_code(self, label: str | None = None) -> dict:
            return {
                "user_code": "ABCD-EFGH",
                "verification_uri": "http://fake/pair",
                "verification_uri_complete": "http://fake/pair?code=ABCD-EFGH",
                "device_code": "dev-123",
                "expires_in": 600,
                "interval": 0,
            }

        def poll_device_code(self, device_code: str) -> dict:
            return poll_result

    return FakeClient


def test_init_device_pairs_when_no_token(monkeypatch, tmp_path):
    # No --token → init runs the device-code pairing handshake (no direct mint).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("YORU_TOKEN", raising=False)
    from yoru_cli import init_cmd

    monkeypatch.setattr(
        init_cmd, "ReceiptClient",
        _fake_pairing_client({"status": "approved", "token": "rcpt_PAIRED"}),
    )
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: True)

    rc = init_cmd.run(_args(server="http://fake", token=None))
    assert rc == 0
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_PAIRED"
    assert data["server"] == "http://fake"


def test_init_device_pairing_denied_returns_nonzero(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("YORU_TOKEN", raising=False)
    from yoru_cli import init_cmd

    monkeypatch.setattr(
        init_cmd, "ReceiptClient", _fake_pairing_client({"status": "denied"})
    )
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: True)

    rc = init_cmd.run(_args(server="http://fake", token=None))
    assert rc != 0
    assert not (tmp_path / ".config" / "yoru" / "config.json").exists()


def test_init_device_pairing_approved_without_token_fails(monkeypatch, tmp_path):
    # Approved but no token in the poll response → hard failure, no config written.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("YORU_TOKEN", raising=False)
    from yoru_cli import init_cmd

    monkeypatch.setattr(
        init_cmd, "ReceiptClient", _fake_pairing_client({"status": "approved"})
    )
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: True)

    rc = init_cmd.run(_args(server="http://fake", token=None))
    assert rc != 0
    assert not (tmp_path / ".config" / "yoru" / "config.json").exists()
