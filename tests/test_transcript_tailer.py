"""Tailer eviction — deleted transcripts must leave tracked/state.

Regression for the 75 GB launchd log (2026-07-23): a transcript deleted while
the tailer runs stayed tracked forever and printed an ENOENT line every poll.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from yoru_cli import transcript_tailer as tt


def test_drain_file_returns_none_when_deleted(tmp_path: Path) -> None:
    gone = tmp_path / "nope.jsonl"
    assert tt._drain_file(gone, 0, "http://localhost:1", "tok") is None


def test_drain_file_keeps_offset_on_other_oserror(tmp_path: Path) -> None:
    # A directory triggers IsADirectoryError (OSError, not FileNotFoundError):
    # keep the offset, don't evict — the path still exists.
    d = tmp_path / "adir.jsonl"
    d.mkdir()
    assert tt._drain_file(d, 7, "http://localhost:1", "tok") == 7


def test_state_purge_on_startup(tmp_path: Path, monkeypatch) -> None:
    alive = tmp_path / "alive.jsonl"
    alive.write_text("")
    state_path = tmp_path / "tail-state.json"
    state_path.write_text(json.dumps({str(alive): 3, str(tmp_path / "dead.jsonl"): 9}))
    monkeypatch.setattr(tt, "_STATE_PATH", state_path)

    state = tt._load_state()
    stale = [k for k in state if not Path(k).exists()]
    for k in stale:
        del state[k]
    tt._save_state(state)

    saved = json.loads(state_path.read_text())
    assert list(saved) == [str(alive)]


def test_run_lock_refuses_second_instance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_RUN_LOCK_PATH", tmp_path / "tailer.run.lock")
    first = tt._acquire_run_lock()
    assert first is not None
    try:
        assert tt._acquire_run_lock() is None
    finally:
        first.close()


def test_run_lock_releases_on_close(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_RUN_LOCK_PATH", tmp_path / "tailer.run.lock")
    first = tt._acquire_run_lock()
    assert first is not None
    first.close()

    second = tt._acquire_run_lock()
    assert second is not None
    second.close()


def test_run_exits_cleanly_when_lock_already_held(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_RUN_LOCK_PATH", tmp_path / "tailer.run.lock")
    held = tt._acquire_run_lock()
    assert held is not None
    try:
        with pytest.raises(SystemExit) as exc:
            tt.run()
        assert exc.value.code == 1
    finally:
        held.close()


def _patch_state_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_STATE_PATH", tmp_path / "tail-state.json")
    monkeypatch.setattr(tt, "_STATE_LOCK_PATH", tmp_path / "tail-state.json.lock")


def test_state_txn_preserves_key_written_by_another_transaction(
    tmp_path: Path, monkeypatch
) -> None:
    # Simulates backfill() bumping one transcript's offset while a live
    # tailer's run() loop saves a different transcript's offset in between —
    # the old bare load/mutate/save would silently drop whichever wrote last.
    _patch_state_paths(tmp_path, monkeypatch)

    with tt._state_txn() as state:
        state["a.jsonl"] = 10
    with tt._state_txn() as state:
        state["b.jsonl"] = 20

    assert tt._load_state() == {"a.jsonl": 10, "b.jsonl": 20}


def test_state_txn_no_lost_updates_under_thread_concurrency(
    tmp_path: Path, monkeypatch
) -> None:
    _patch_state_paths(tmp_path, monkeypatch)
    n_threads, n_writes = 8, 25

    def worker(idx: int) -> None:
        key = f"session-{idx}.jsonl"
        for i in range(n_writes):
            with tt._state_txn() as state:
                state[key] = i

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    final = tt._load_state()
    assert len(final) == n_threads
    for idx in range(n_threads):
        assert final[f"session-{idx}.jsonl"] == n_writes - 1


def test_load_config_resolves_from_active_identity_slot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    from yoru_cli import config

    config.save({"identity_id": "id-1", "server": "http://a", "token": "rcpt_A"})
    assert tt._load_config() == ("http://a", "rcpt_A")

    config.save({"identity_id": "id-2", "server": "http://b", "token": "rcpt_B"})
    assert tt._load_config() == ("http://b", "rcpt_B")


def test_load_config_raises_when_no_active_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        tt._load_config()
