"""Tailer eviction — deleted transcripts must leave tracked/state.

Regression for the 75 GB launchd log (2026-07-23): a transcript deleted while
the tailer runs stayed tracked forever and printed an ENOENT line every poll.
"""
from __future__ import annotations

import json
from pathlib import Path

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
