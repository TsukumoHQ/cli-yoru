from __future__ import annotations

import argparse
import io


def _args(server: str | None = None, session_id: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(cmd="tail", server=server, session_id=session_id)


class _FakeResp:
    def __init__(self, status: int = 202, text: str = '{"accepted":1}') -> None:
        self.status_code = status
        self.text = text


def _seed_config(monkeypatch, tmp_path, token: str = "rcpt_TOK") -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    config.save({"server": "http://fake", "token": token})


def test_tail_posts_single_line_event(monkeypatch, tmp_path, capsys):
    _seed_config(monkeypatch, tmp_path)
    from yoru_cli import tail_cmd

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResp(status=202)

    monkeypatch.setattr("yoru_cli.api.httpx.post", fake_post)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id":"s1","user":"dev","kind":"tool_use"}\n'))

    rc = tail_cmd.run(_args())
    assert rc == 0
    assert captured["url"] == "http://fake/api/v1/sessions/events"
    assert captured["json"] == {"events": [{"session_id": "s1", "user": "dev", "kind": "tool_use"}]}
    assert captured["headers"]["Authorization"] == "Bearer rcpt_TOK"
    out = capsys.readouterr().out
    assert "HTTP 202" in out


def test_tail_accepts_json_array(monkeypatch, tmp_path):
    _seed_config(monkeypatch, tmp_path)
    from yoru_cli import tail_cmd

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResp(status=202, text="")

    monkeypatch.setattr("yoru_cli.api.httpx.post", fake_post)
    payload = '[{"session_id":"s1","user":"dev","kind":"tool_use"},{"session_id":"s1","user":"dev","kind":"tool_use"}]'
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))

    rc = tail_cmd.run(_args())
    assert rc == 0
    assert len(captured["json"]["events"]) == 2


def test_tail_session_id_flag_overrides(monkeypatch, tmp_path):
    _seed_config(monkeypatch, tmp_path)
    from yoru_cli import tail_cmd

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResp(status=202, text="")

    monkeypatch.setattr("yoru_cli.api.httpx.post", fake_post)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"session_id":"s-orig","user":"dev","kind":"tool_use"}\n'),
    )

    rc = tail_cmd.run(_args(session_id="s-override"))
    assert rc == 0
    assert captured["json"]["events"][0]["session_id"] == "s-override"


def test_tail_4xx_returns_3(monkeypatch, tmp_path):
    _seed_config(monkeypatch, tmp_path)
    from yoru_cli import tail_cmd

    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResp(status=400, text="bad request")

    monkeypatch.setattr("yoru_cli.api.httpx.post", fake_post)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id":"s1","user":"dev","kind":"tool_use"}\n'))

    rc = tail_cmd.run(_args())
    assert rc == 3


def test_tail_no_config_no_server_returns_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import tail_cmd

    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id":"s1","user":"dev","kind":"tool_use"}\n'))
    rc = tail_cmd.run(_args())
    assert rc == 2
    err = capsys.readouterr().err
    assert "no server configured" in err
