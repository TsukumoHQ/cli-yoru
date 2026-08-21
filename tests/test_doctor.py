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


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_doctor_revoked_token_points_to_rotate_not_init_force(monkeypatch, tmp_path, capsys):
    # DEC-yoru-design-ruling-1 A.3#5 — doctor must guide toward `yoru rotate`
    # (same identity, no --server needed), never `init --force` (re-pairs
    # from scratch), on a revoked/expired token.
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config, doctor_cmd

    config.save({"server": "http://fake", "token": "rcpt_test_abcd"})

    def fake_get(url: str, **kwargs):
        if url.endswith("/health/ready"):
            return _FakeResponse(200)
        if url.endswith("/auth/hook-tokens"):
            return _FakeResponse(401)
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(doctor_cmd.httpx, "get", fake_get)

    rc = doctor_cmd.run(_args())
    err = capsys.readouterr().err

    assert rc == 3
    assert "yoru rotate" in err
    assert "init --force" not in err


def _run_to_success(monkeypatch, tmp_path) -> None:
    """Fresh install through hook-installed — every stage before tailer
    status passes, so doctor reaches stage 5."""
    from yoru_cli import config, doctor_cmd

    monkeypatch.setenv("HOME", str(tmp_path))
    config.save({"server": "http://fake", "token": "rcpt_test_abcd"})

    def fake_get(url: str, **kwargs):
        if url.endswith("/health/ready"):
            return _FakeResponse(200)
        if url.endswith("/auth/hook-tokens"):
            return _FakeResponse(200)
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(doctor_cmd.httpx, "get", fake_get)

    hook = doctor_cmd._hook_path()
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n")
    hook.chmod(0o755)


def test_doctor_reports_tailer_never_run(monkeypatch, tmp_path, capsys):
    _run_to_success(monkeypatch, tmp_path)
    from yoru_cli import doctor_cmd, transcript_tailer

    monkeypatch.setattr(transcript_tailer, "_RUN_LOCK_PATH", tmp_path / "tailer.run.lock")
    monkeypatch.setattr(transcript_tailer, "_METRICS_PATH", tmp_path / "tail-metrics.json")
    monkeypatch.setattr(
        transcript_tailer, "_METRICS_LOCK_PATH", tmp_path / "tail-metrics.json.lock"
    )

    rc = doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert rc == 0  # tailer absence never fails doctor
    assert "tailer not running" in out


def test_doctor_reports_tailer_running_with_recent_activity(monkeypatch, tmp_path, capsys):
    _run_to_success(monkeypatch, tmp_path)
    from yoru_cli import doctor_cmd, transcript_tailer

    monkeypatch.setattr(transcript_tailer, "_RUN_LOCK_PATH", tmp_path / "tailer.run.lock")
    monkeypatch.setattr(transcript_tailer, "_METRICS_PATH", tmp_path / "tail-metrics.json")
    monkeypatch.setattr(
        transcript_tailer, "_METRICS_LOCK_PATH", tmp_path / "tail-metrics.json.lock"
    )
    held = transcript_tailer._acquire_run_lock()
    transcript_tailer._record_post_success()
    try:
        rc = doctor_cmd.run(_args())
    finally:
        held.close()

    out = capsys.readouterr().out
    assert rc == 0
    assert "✓ tailer running" in out
    assert "last event" in out


# ---------- Active identity visibility (bdda7f06) ----------

def test_doctor_shows_active_identity_label_and_id(monkeypatch, tmp_path, capsys):
    from yoru_cli import config, doctor_cmd

    monkeypatch.setenv("HOME", str(tmp_path))
    config.save({
        "identity_id": "id-alice",
        "identity_label": "alice-laptop",
        "server": "http://127.0.0.1:1",
        "token": "rcpt_test_abcd",
    })

    doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert "✓ identity: alice-laptop (id-alice)" in out
    assert "shared tailer attributes activity to this identity" in out


def test_doctor_no_multi_identity_warning_with_single_identity(monkeypatch, tmp_path, capsys):
    from yoru_cli import config, doctor_cmd

    monkeypatch.setenv("HOME", str(tmp_path))
    config.save({
        "identity_id": "id-alice", "identity_label": "alice-laptop",
        "server": "http://127.0.0.1:1", "token": "rcpt_test_abcd",
    })

    doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert "paired on this machine" not in out


def test_doctor_warns_on_multiple_paired_identities(monkeypatch, tmp_path, capsys):
    from yoru_cli import config, doctor_cmd

    monkeypatch.setenv("HOME", str(tmp_path))
    config.save({
        "identity_id": "id-alice", "identity_label": "alice-laptop",
        "server": "http://127.0.0.1:1", "token": "rcpt_test_abcd",
    })
    # A second, dormant identity — save_to_slot does NOT flip active.
    config.save_to_slot("id-bob", {
        "identity_id": "id-bob", "identity_label": "bob-desktop",
        "server": "http://127.0.0.1:1", "token": "rcpt_test_other",
    })
    assert config.active_identity_id() == "id-alice"  # unchanged by the above

    doctor_cmd.run(_args())
    out = capsys.readouterr().out
    assert "⚠ 2 identities paired on this machine, only \"alice-laptop\" is active" in out
    assert "bob-desktop (id-bob)" in out
    assert "yoru use <label>" in out
