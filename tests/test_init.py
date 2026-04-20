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
    from receipt_cli import config, init_cmd

    assert config.exists() is False

    rc = init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    assert rc == 0

    cfg_path = tmp_path / ".config" / "receipt" / "config.json"
    assert cfg_path.is_file()
    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600
    data = json.loads(cfg_path.read_text())
    assert data["server"] == "http://fake"
    assert data["token"] == "rcpt_ABC"
    assert "created_at" in data

    cfg_dir = tmp_path / ".config" / "receipt"
    assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700

    hook_path = tmp_path / ".claude" / "hooks" / "receipt.sh"
    assert hook_path.is_file()
    assert stat.S_IMODE(hook_path.stat().st_mode) == 0o755
    body = hook_path.read_text()
    assert "api/v1/sessions/events" in body
    assert "rcpt_ABC" in cfg_path.read_text()


def test_init_already_installed_without_force(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from receipt_cli import init_cmd

    assert init_cmd.run(_args(server="http://fake", token="rcpt_ABC")) == 0
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_XYZ"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "Already installed" in err


def test_init_with_force_overwrites(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from receipt_cli import init_cmd

    init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_XYZ", force=True))
    assert rc == 0
    data = json.loads((tmp_path / ".config" / "receipt" / "config.json").read_text())
    assert data["token"] == "rcpt_XYZ"


def test_init_mints_token_when_not_provided(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from receipt_cli import init_cmd

    class FakeResp:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"token": "rcpt_MINTED", "user_id": "uuidhex", "user": "alice"}

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr("receipt_cli.api.httpx.post", fake_post)
    monkeypatch.setattr("builtins.input", lambda prompt="": "alice")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    rc = init_cmd.run(_args(server="http://fake", token=None))
    assert rc == 0
    assert captured["url"] == "http://fake/api/v1/auth/hook-token"
    assert captured["json"] == {"user": "alice"}

    data = json.loads((tmp_path / ".config" / "receipt" / "config.json").read_text())
    assert data["token"] == "rcpt_MINTED"


def test_init_mints_token_with_user_flag_non_interactive(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from receipt_cli import init_cmd

    class FakeResp:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"token": "rcpt_FROMFLAG", "user_id": "uuidhex", "user": "a@b.c"}

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    def boom(prompt: str = "") -> str:
        raise AssertionError("input() must not be called when --user is provided")

    monkeypatch.setattr("receipt_cli.api.httpx.post", fake_post)
    monkeypatch.setattr("builtins.input", boom)

    rc = init_cmd.run(_args(server="http://fake", token=None, user="a@b.c"))
    assert rc == 0
    assert captured["json"] == {"user": "a@b.c"}
    data = json.loads((tmp_path / ".config" / "receipt" / "config.json").read_text())
    assert data["token"] == "rcpt_FROMFLAG"


def test_init_mints_token_from_stdin_when_piped(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    import io
    from receipt_cli import init_cmd

    class FakeResp:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"token": "rcpt_FROMSTDIN", "user_id": "uuidhex", "user": "piped@x.y"}

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return FakeResp()

    def boom(prompt: str = "") -> str:
        raise AssertionError("input() must not be called when stdin is piped")

    monkeypatch.setattr("receipt_cli.api.httpx.post", fake_post)
    monkeypatch.setattr("builtins.input", boom)
    monkeypatch.setattr("sys.stdin", io.StringIO("piped@x.y\n"))

    rc = init_cmd.run(_args(server="http://fake", token=None))
    assert rc == 0
    assert captured["json"] == {"user": "piped@x.y"}
    data = json.loads((tmp_path / ".config" / "receipt" / "config.json").read_text())
    assert data["token"] == "rcpt_FROMSTDIN"
