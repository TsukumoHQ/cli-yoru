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


# ---------- Streamed drain (#1) ----------

def _patch_metrics_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_METRICS_PATH", tmp_path / "tail-metrics.json")
    monkeypatch.setattr(tt, "_METRICS_LOCK_PATH", tmp_path / "tail-metrics.json.lock")


def _stub_one_event_per_line(monkeypatch, posted: list) -> None:
    """Replace `_iter_assistant_events` with a stub yielding one synthetic
    event per line, and `_post` with a fake that records (server, event) and
    always succeeds — decouples the drain-loop/chunking tests from real
    Claude Code JSONL shape."""
    monkeypatch.setattr(tt, "_iter_assistant_events", lambda line: [{"line": line}])
    def _fake_post(server, token, event):
        posted.append(event["line"])
        return True
    monkeypatch.setattr(tt, "_post", _fake_post)


def test_drain_file_streams_across_many_chunk_boundaries(tmp_path: Path, monkeypatch) -> None:
    """Force a tiny chunk size so a realistic-sized remainder spans dozens of
    reads — the streamed drain must still split on newlines correctly and
    land on the exact same final offset/event order as a single `.read()`
    would have, proving the chunking introduced no data loss/duplication."""
    monkeypatch.setattr(tt, "_DRAIN_CHUNK_SIZE", 16)  # tiny — forces many reads
    _patch_metrics_paths(tmp_path, monkeypatch)
    posted: list = []
    _stub_one_event_per_line(monkeypatch, posted)

    lines = [f"line-{i:03d} payload data here" for i in range(50)]
    p = tmp_path / "big.jsonl"
    p.write_text("\n".join(lines) + "\n")

    new_offset = tt._drain_file(p, 0, "http://x", "tok")
    assert new_offset == p.stat().st_size
    assert posted == lines

    # Re-draining from EOF is a no-op — nothing re-posted.
    posted.clear()
    assert tt._drain_file(p, new_offset, "http://x", "tok") == new_offset
    assert posted == []


def test_drain_file_leaves_incomplete_trailing_line_for_next_poll(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(tt, "_DRAIN_CHUNK_SIZE", 8)
    _patch_metrics_paths(tmp_path, monkeypatch)
    posted: list = []
    _stub_one_event_per_line(monkeypatch, posted)

    p = tmp_path / "partial.jsonl"
    p.write_text("complete-line\nno-newline-yet")  # trailing line has no \n

    offset = tt._drain_file(p, 0, "http://x", "tok")
    assert posted == ["complete-line"]
    assert offset == len("complete-line\n")  # stops before the partial line

    # The writer finishes the line later — next poll picks it up from offset.
    with open(p, "a") as f:
        f.write(" finished\n")
    offset2 = tt._drain_file(p, offset, "http://x", "tok")
    assert posted == ["complete-line", "no-newline-yet finished"]
    assert offset2 == p.stat().st_size


def test_drain_file_stops_at_failed_post_mid_stream(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_DRAIN_CHUNK_SIZE", 8)
    _patch_metrics_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(tt, "_iter_assistant_events", lambda line: [{"line": line}])

    posted: list = []
    def _flaky_post(server, token, event):
        if event["line"] == "bad":
            return False
        posted.append(event["line"])
        return True
    monkeypatch.setattr(tt, "_post", _flaky_post)

    p = tmp_path / "flaky.jsonl"
    p.write_text("good1\ngood2\nbad\ngood3\n")

    offset = tt._drain_file(p, 0, "http://x", "tok")
    assert posted == ["good1", "good2"]
    # Offset parked exactly before "bad" — never advances past an un-acked line.
    assert offset == len("good1\ngood2\n")
    assert tt._load_metrics()["error_count"] == 1


# ---------- Tailer observability (#4) ----------

def test_metrics_recorded_on_successful_drain(tmp_path: Path, monkeypatch) -> None:
    _patch_metrics_paths(tmp_path, monkeypatch)
    posted: list = []
    _stub_one_event_per_line(monkeypatch, posted)

    p = tmp_path / "s.jsonl"
    p.write_text("one\ntwo\n")
    before = tt._load_metrics()
    assert before == {}

    tt._drain_file(p, 0, "http://x", "tok")
    after = tt._load_metrics()
    assert "last_post_ts" in after
    assert after.get("error_count", 0) == 0


def test_metrics_untouched_when_nothing_new_to_post(tmp_path: Path, monkeypatch) -> None:
    """A poll tick with no new bytes shouldn't bump last_post_ts — that
    field means "tailer confirmed activity", not "tailer polled"."""
    _patch_metrics_paths(tmp_path, monkeypatch)
    posted: list = []
    _stub_one_event_per_line(monkeypatch, posted)

    p = tmp_path / "empty.jsonl"
    p.write_text("")
    tt._drain_file(p, 0, "http://x", "tok")
    assert tt._load_metrics() == {}


def test_read_status_reports_not_running_and_no_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_RUN_LOCK_PATH", tmp_path / "tailer.run.lock")
    _patch_metrics_paths(tmp_path, monkeypatch)

    status = tt.read_status()
    assert status == {
        "running": False, "last_post_ts": None, "last_error_ts": None, "error_count": 0,
    }


def test_read_status_reports_running_when_lock_held(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_RUN_LOCK_PATH", tmp_path / "tailer.run.lock")
    _patch_metrics_paths(tmp_path, monkeypatch)

    held = tt._acquire_run_lock()
    assert held is not None
    try:
        assert tt.read_status()["running"] is True
    finally:
        held.close()

    assert tt.read_status()["running"] is False


def test_read_status_surfaces_last_post_and_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tt, "_RUN_LOCK_PATH", tmp_path / "tailer.run.lock")
    _patch_metrics_paths(tmp_path, monkeypatch)

    tt._record_post_success()
    tt._record_post_error()
    tt._record_post_error()

    status = tt.read_status()
    assert status["last_post_ts"] is not None
    assert status["last_error_ts"] is not None
    assert status["error_count"] == 2
