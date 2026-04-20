from __future__ import annotations

import argparse


def _args() -> argparse.Namespace:
    return argparse.Namespace(cmd="doctor")


def test_doctor_exits_1_when_config_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from receipt_cli import config, doctor_cmd

    assert config.exists() is False
    rc = doctor_cmd.run(_args())
    assert rc == 1
    err = capsys.readouterr().err
    assert "receipt init not run" in err
