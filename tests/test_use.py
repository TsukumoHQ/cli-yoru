from __future__ import annotations

import argparse


def _args(label: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(cmd="use", label=label)


def test_use_no_identities_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import use_cmd

    rc = use_cmd.run(_args())
    assert rc == 1
    assert "init" in capsys.readouterr().err


def test_use_no_arg_lists_identities_marks_active(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, use_cmd

    config.save({"identity_id": "id-1", "identity_label": "laptop", "server": "http://a", "token": "rcpt_A"})
    config.save({"identity_id": "id-2", "identity_label": "desktop", "server": "http://b", "token": "rcpt_B"})

    rc = use_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "laptop" in out and "desktop" in out
    # id-2 is active (most recently saved) — its line is marked.
    active_lines = [ln for ln in out.splitlines() if ln.startswith("*")]
    assert len(active_lines) == 1
    assert "desktop" in active_lines[0]


def test_use_switches_active_by_label(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, use_cmd

    config.save({"identity_id": "id-1", "identity_label": "laptop", "server": "http://a", "token": "rcpt_A"})
    config.save({"identity_id": "id-2", "identity_label": "desktop", "server": "http://b", "token": "rcpt_B"})

    rc = use_cmd.run(_args("laptop"))
    assert rc == 0
    assert config.active_identity_id() == "id-1"
    assert config.load()["server"] == "http://a"
    assert "laptop" in capsys.readouterr().out


def test_use_switches_active_by_identity_id(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, use_cmd

    config.save({"identity_id": "id-1", "identity_label": "laptop", "server": "http://a", "token": "rcpt_A"})
    config.save({"identity_id": "id-2", "identity_label": "desktop", "server": "http://b", "token": "rcpt_B"})

    rc = use_cmd.run(_args("id-1"))
    assert rc == 0
    assert config.active_identity_id() == "id-1"


def test_use_unknown_label_errors_without_switching(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, use_cmd

    config.save({"identity_id": "id-1", "identity_label": "laptop", "server": "http://a", "token": "rcpt_A"})

    rc = use_cmd.run(_args("nonexistent"))
    assert rc == 1
    assert "no paired identity" in capsys.readouterr().err.lower()
    assert config.active_identity_id() == "id-1"


def test_use_ambiguous_label_errors_and_lists_matches(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, use_cmd

    config.save({"identity_id": "id-1", "identity_label": "dev", "server": "http://a", "token": "rcpt_A"})
    config.save({"identity_id": "id-2", "identity_label": "dev", "server": "http://b", "token": "rcpt_B"})

    rc = use_cmd.run(_args("dev"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "id-1" in err and "id-2" in err
