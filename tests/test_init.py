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


def test_init_lands_public_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    rc = init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    assert rc == 0

    skill = tmp_path / ".claude" / "skills" / "yoru" / "SKILL.md"
    assert skill.is_file()
    body = skill.read_text()
    # Valid Claude Code skill frontmatter + the public name it resolves by.
    assert body.startswith("---\n")
    assert "name: yoru" in body
    assert "description:" in body
    # Honesty rails baked in (self-hosted only; --server has no default).
    assert "self-hosted" in body.lower()
    assert "--server" in body
    # Public/AGPL split — the shipped skill must not leak private fleet paths
    # or the private maintainer persona.
    assert ".worktrees" not in body
    assert "yoru-dev" not in body
    assert "agent-relay" not in body


def test_init_already_installed_without_force(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    assert init_cmd.run(_args(server="http://fake", token="rcpt_ABC")) == 0
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_XYZ"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "Already installed" in err


def _fake_revoke_client(*, ok: bool = True, raises: Exception | None = None):
    """A ReceiptClient stand-in for the --force revoke path. Records the
    server/token it was constructed with and whether .logout() was called."""
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


def test_init_with_force_overwrites(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    FakeClient, calls = _fake_revoke_client()
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)

    init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_XYZ", force=True))
    assert rc == 0
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_XYZ"


def test_init_force_revokes_previous_token_server_side(monkeypatch, tmp_path):
    # DEC-yoru-design-ruling-1 A.3#4 — the accountability hole this ticket
    # closes: --force must not orphan the credential it's replacing.
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    FakeClient, calls = _fake_revoke_client(ok=True)
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)

    init_cmd.run(_args(server="http://fake", token="rcpt_OLD"))
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_NEW", force=True))

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["base_url"] == "http://fake"
    assert calls[0]["token"] == "rcpt_OLD"  # the SUPERSEDED token, never the new one


def test_init_force_same_token_skips_revoke_does_not_brick_itself(monkeypatch, tmp_path, capsys):
    # Round-2 review finding: --force re-run with the SAME --token/$YORU_TOKEN
    # was revoking the token it was about to save — the config ends up
    # pointing at a token the server just killed, hook stream 401s forever.
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, init_cmd

    FakeClient, calls = _fake_revoke_client(ok=True)
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)

    init_cmd.run(_args(server="http://fake", token="rcpt_SAME"))
    capsys.readouterr()
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_SAME", force=True))

    assert rc == 0
    assert calls == []  # never revoked — it's the credential we're about to keep
    assert config.load()["token"] == "rcpt_SAME"
    assert "nothing to revoke" in capsys.readouterr().err.lower()


def test_init_without_force_never_calls_logout(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    FakeClient, calls = _fake_revoke_client()
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)

    init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    assert calls == []


def test_init_force_warns_explicitly_before_revoking(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    FakeClient, _calls = _fake_revoke_client(ok=True)
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)

    init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    capsys.readouterr()  # drain first run's output
    init_cmd.run(_args(server="http://fake", token="rcpt_XYZ", force=True))

    err = capsys.readouterr().err
    assert "force" in err.lower()
    assert "revok" in err.lower()


def test_init_force_revoke_failure_does_not_block_init(monkeypatch, tmp_path, capsys):
    # A network failure revoking the old token must not fail the whole
    # re-pair — surface it loudly, don't leave the user stuck unpaired.
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd
    import httpx

    FakeClient, calls = _fake_revoke_client(raises=httpx.ConnectError("boom"))
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)

    init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    capsys.readouterr()
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_XYZ", force=True))

    assert rc == 0
    assert len(calls) == 1
    data = json.loads((tmp_path / ".config" / "yoru" / "config.json").read_text())
    assert data["token"] == "rcpt_XYZ"
    err = capsys.readouterr().err
    assert "could not revoke" in err.lower()


def test_init_force_already_revoked_token_does_not_warn_as_failure(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    FakeClient, _calls = _fake_revoke_client(ok=False)  # already revoked/invalid
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)

    init_cmd.run(_args(server="http://fake", token="rcpt_ABC"))
    capsys.readouterr()
    rc = init_cmd.run(_args(server="http://fake", token="rcpt_XYZ", force=True))

    assert rc == 0
    err = capsys.readouterr().err
    assert "could not revoke" not in err.lower()


def test_init_force_recovers_from_corrupt_config(monkeypatch, tmp_path) -> None:
    # Regression: --force must stay a working recovery path even when the
    # existing config.json is corrupt (unparseable JSON) — that's the whole
    # point of --force existing. Revoking a corrupt-config's "old token" is
    # simply skipped, not a crash.
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import init_cmd

    FakeClient, calls = _fake_revoke_client(ok=True)
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)

    cfg_path = tmp_path / ".config" / "yoru" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("{not valid json", encoding="utf-8")

    rc = init_cmd.run(_args(server="http://fake", token="rcpt_NEW", force=True))

    assert rc == 0
    assert calls == []  # nothing to revoke — no crash, no bogus revoke attempt
    data = json.loads(cfg_path.read_text())
    assert data["token"] == "rcpt_NEW"


def _fake_pairing_client(poll_result: dict):
    """A ReceiptClient stand-in for the device-code pairing flow. start returns a
    fixed pairing payload; poll returns `poll_result` (interval=0 so no sleep)."""
    class FakeClient:
        def __init__(self, server: str) -> None:
            self.server = server

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


def test_init_device_pairing_sends_raw_hostname(monkeypatch, tmp_path):
    # DEC-yoru-design-ruling-1 A.3#1 — the CLI must send the raw hostname
    # (distinct from the user-overridable --label) so the server can
    # populate CliToken.machine_hostname.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("YORU_TOKEN", raising=False)
    from yoru_cli import init_cmd

    captured: dict = {}
    FakeClient = _fake_pairing_client({"status": "approved", "token": "rcpt_PAIRED"})
    orig_start = FakeClient.start_device_code

    def _spy_start(self, label=None, hostname=None):
        captured["label"] = label
        captured["hostname"] = hostname
        return orig_start(self, label=label, hostname=hostname)

    FakeClient.start_device_code = _spy_start
    monkeypatch.setattr(init_cmd, "ReceiptClient", FakeClient)
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: True)

    rc = init_cmd.run(_args(server="http://fake", token=None))
    assert rc == 0
    assert captured["hostname"] == init_cmd._hostname()
    assert captured["hostname"]  # non-empty
    assert captured["hostname"] not in (None, "")
    # hostname is raw (no " · os" suffix), unlike the label
    assert " · " not in captured["hostname"]


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
