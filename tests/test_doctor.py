from __future__ import annotations

import argparse


def _args() -> argparse.Namespace:
    return argparse.Namespace(cmd="doctor")


def test_doctor_exits_1_when_config_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, doctor_cmd

    assert config.exists() is False
    rc = doctor_cmd.run(_args())
    assert rc == 1
    err = capsys.readouterr().err
    assert "yoru init not run" in err


def test_doctor_prints_passed_stages_before_a_failure(monkeypatch, tmp_path, capsys):
    """A later-stage failure still shows the ✓ trail of what already worked."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    from yoru_cli import config, doctor_cmd

    # config stage passes; backend stage fails (nothing is listening).
    config.save({"server": "http://127.0.0.1:1", "token": "rcpt_test_abcd"})

    rc = doctor_cmd.run(_args())
    out = capsys.readouterr()
    assert rc == 2  # failed at the backend stage
    assert "✓ config" in out.out  # but the passed stage was reported first
    assert "backend unreachable" in out.err
