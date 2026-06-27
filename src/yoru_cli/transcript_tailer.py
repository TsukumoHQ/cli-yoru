"""Transcript tailer — captures AssistantMessage events that hooks can't.

Claude Code hooks expose user prompts, tool calls, and session lifecycle, but
emit no event when Claude itself speaks. For a complete audit trail Receipt
must read the transcript file (`~/.claude/projects/<slug>/<session>.jsonl`),
which Claude Code appends to in real time with every message (user, assistant,
tool_use, tool_result).

Strategy:
  * Discover all `.jsonl` under `~/.claude/projects/` on startup + via 2-sec
    rescan for new sessions.
  * Per file, remember the last byte offset in a small state file so a restart
    never reprocesses.
  * On every new JSONL line, route:
      type=assistant + content[].type=text     → kind=message tool=assistant
      type=assistant + content[].type=thinking → kind=message tool=thinking
    (user prompts + tool calls + tool results are already captured by hooks.)
  * POST one event at a time via the same /api/v1/sessions/events endpoint.

Daemonize: run as LaunchAgent (`launchctl load …plist`) or in a tmux pane.

v0 keeps it dependency-free — stdlib-only, one thread per file.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

_PROJECTS_DIR = Path.home() / ".claude/projects"
_CONFIG_PATH = Path.home() / ".config/yoru/config.json"
_STATE_PATH = Path.home() / ".config/yoru/tail-state.json"
_POLL_INTERVAL_SEC = 1.0
_RESCAN_INTERVAL_SEC = 5.0

# Pricing is computed backend-side (see backend/apps/api/api/routers/receipt/
# pricing.py which auto-refreshes from LiteLLM's public JSON). The tailer
# ships raw usage + model and lets the backend resolve the rate so we never
# have stale hardcoded prices here — and new providers (Cursor, Aider,
# whatever emits OpenAI/Gemini) work without touching the tailer.


def _load_config() -> tuple[str, str]:
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)
    return cfg["server"].rstrip("/"), cfg["token"]


def _load_state() -> dict[str, int]:
    if not _STATE_PATH.exists():
        return {}
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, int]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f)
    tmp.replace(_STATE_PATH)


def _post(server: str, token: str, event: dict[str, Any]) -> bool:
    """One-event ingest. Returns True on success — the caller only advances the
    read offset past acked events, so nothing is lost on a backend outage."""
    return _post_batch(server, token, [event])


def _post_batch(server: str, token: str, events: list[dict[str, Any]]) -> bool:
    """Batch ingest — POST up to 1000 events in one request. Returns True on a
    2xx. Retries on rate-limit (429) AND on connection errors (backend down /
    restarting) with backoff, so a transient outage doesn't drop events."""
    if not events:
        return True
    body = json.dumps({"events": events}).encode("utf-8")
    for attempt in range(6):
        req = urllib.request.Request(
            f"{server}/api/v1/sessions/events",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = 1
                try:
                    retry_after = int(e.headers.get("Retry-After", "1"))
                except (TypeError, ValueError):
                    pass
                time.sleep(retry_after + 0.2)
                continue
            # 4xx/5xx that isn't rate-limit: don't spin, but report failure so
            # the offset doesn't advance past this event.
            print(f"[tailer] POST {e.code}: {e.reason}", file=sys.stderr)
            return False
        except (urllib.error.URLError, TimeoutError) as e:
            # Backend down / restarting — back off and retry (covers a daemon
            # restart window). Failing here keeps the read offset put.
            print(f"[tailer] POST retry {attempt + 1}/6: {e}", file=sys.stderr)
            time.sleep(min(2 ** attempt, 10))
            continue
    print("[tailer] gave up after 6 retries — will retry next poll", file=sys.stderr)
    return False


# In-memory set of Anthropic message IDs we've already ingested. Prevents
# Claude Code's occasional duplicate transcript writes (streaming deltas +
# final consolidation, or a session compaction replaying history) from
# double-counting tokens and cost. The set grows unboundedly in a long run
# but each entry is ~40 bytes so millions fit comfortably.
_SEEN_MSG_IDS: set[str] = set()


def _iter_assistant_events(line: str) -> Iterable[dict[str, Any]]:
    """Parse one JSONL line, yield Receipt events.

    Handles two line types:
      * type=user  → emits kind=message tool=user (the hook UserPromptSubmit
        only fires live, so backfill has to replay prompts from transcript)
      * type=assistant → emits:
          - kind=message tool=assistant per text block
          - kind=message tool=thinking per thinking block
          - kind=token per assistant message (usage rollup → cost)

    Returns nothing for other types (attachment/snapshot/etc) or for message
    IDs we've already processed this session.
    """
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return
    t = d.get("type")
    if t == "user":
        # User prompt replay for backfill. Live tailer also emits these (dup
        # with the hook) but the hook-sourced events have no `message.id`
        # from the transcript — they're deduped by session_id+ts pair on
        # the backend if anyone cares, which for now we don't (duplicates
        # under kind=message tool=user are cosmetic in the timeline).
        msg = d.get("message") or {}
        content = msg.get("content")
        # Claude Code writes either a string content ("hi") or an array of
        # blocks ([{type:text,text:"hi"}, {type:tool_result,...}]). We only
        # keep the text blocks, join them.
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = [b.get("text") for b in content
                     if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
            text = "\n".join(p for p in parts if p)
        text = (text or "").strip()
        if not text:
            return
        session_id = d.get("sessionId") or ""
        ts = d.get("timestamp") or ""
        if not session_id:
            return
        # Dedup: a message.id-style key would be ideal but user lines don't
        # always carry one; use (uuid) from the line which is transcript-
        # unique.
        key = d.get("uuid")
        if isinstance(key, str) and key:
            if key in _SEEN_MSG_IDS:
                return
            _SEEN_MSG_IDS.add(key)
        yield {
            "session_id": session_id,
            "ts": ts,
            "kind": "message",
            "tool": "user",
            "content": text[:2000],
            "raw": {"hook_event_name": "TranscriptTail", "uuid": key},
            "entry_uuid": str(key) if key else None,
        }
        return

    if t != "assistant":
        return
    message = d.get("message") or {}
    content = message.get("content") or []
    if not isinstance(content, list):
        return
    session_id = d.get("sessionId") or ""
    ts = d.get("timestamp") or ""
    if not session_id:
        return
    line_uuid = d.get("uuid") or message.get("id") or ""
    # Claude Code writes each assistant message MULTIPLE times (streaming
    # deltas + a final consolidation), and tool_use blocks often only appear in
    # the later writes. So we must NOT skip the whole message on a repeat
    # message.id — that drops tool calls. Instead:
    #   * tool_use → emitted on every write, deduped at the backend by the
    #     block's stable id (toolu_…), so each tool call lands exactly once;
    #   * text/thinking/token → emitted only on the first write (is_repeat),
    #     so cost/text aren't double-counted.
    msg_id = message.get("id")
    is_repeat = bool(msg_id) and msg_id in _SEEN_MSG_IDS
    if isinstance(msg_id, str) and msg_id:
        _SEEN_MSG_IDS.add(msg_id)
    _FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
    for idx, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        euid = f"{line_uuid}:{idx}" if line_uuid else None
        if block_type == "tool_use":
            tname = block.get("name")
            tinput = block.get("input")
            yield {
                "session_id": session_id,
                "ts": ts,
                "kind": "file_change" if tname in _FILE_TOOLS else "tool_use",
                "tool": tname,
                "raw": {
                    "hook_event_name": "TranscriptTail",
                    "tool_input": tinput if isinstance(tinput, dict) else {},
                },
                # Stable across streaming re-writes → backend dedups to one row.
                "entry_uuid": block.get("id") or euid,
            }
            continue
        if is_repeat:
            continue  # text/thinking/token already captured on the first write
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                yield {
                    "session_id": session_id,
                    "ts": ts,
                    "kind": "message",
                    "tool": "assistant",
                    "content": text[:4000],
                    "raw": {"hook_event_name": "TranscriptTail", "block": block},
                    "entry_uuid": euid,
                }
        elif block_type == "thinking":
            think = block.get("thinking")
            if isinstance(think, str) and think.strip():
                yield {
                    "session_id": session_id,
                    "ts": ts,
                    "kind": "message",
                    "tool": "thinking",
                    "content": think[:4000],
                    "raw": {"hook_event_name": "TranscriptTail", "block": block},
                    "entry_uuid": euid,
                }

    # One usage event per message (first write only — see is_repeat above).
    # The backend's events_router aggregates
    # tokens_input/output/cost_usd onto the session row, so this is where the
    # Hero "cost" sparkline gets its numbers. Attaching model to `raw` keeps
    # the assumption auditable.
    usage = message.get("usage") or {}
    if not is_repeat and isinstance(usage, dict) and usage:
        model = str(message.get("model") or "")
        input_tokens = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
        )
        output_tokens = int(usage.get("output_tokens") or 0)
        if input_tokens > 0 or output_tokens > 0:
            # Put the full usage breakdown under `raw.tool_input` so the
            # backend's existing _enrich_events path (`raw.tool_input` →
            # `EventOut.tool_input`) surfaces it to the frontend TokenPanel
            # without a schema change. The backend's pricing compute reads
            # `raw.usage` for cost calculation — we duplicate to keep both
            # paths working.
            yield {
                "session_id": session_id,
                "ts": ts,
                "kind": "token",
                "tool": model or "usage",
                "tokens_input": input_tokens,
                "tokens_output": output_tokens,
                "content": f"{model} · {input_tokens}→{output_tokens} tok",
                "raw": {
                    "hook_event_name": "TranscriptTail",
                    "model": model,
                    "usage": usage,
                    "tool_input": {"model": model, **usage},
                },
                "entry_uuid": f"{line_uuid}:usage" if line_uuid else None,
            }


def _drain_file(
    path: Path,
    offset: int,
    server: str,
    token: str,
) -> int:
    """Read from `offset` to EOF, POSTing each derived event. Returns new offset."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            remainder = f.read()
    except OSError as e:
        print(f"[tailer] read {path}: {e}", file=sys.stderr)
        return offset
    if not remainder:
        return offset
    # Advance the offset one complete line at a time, and ONLY past lines whose
    # events all POST successfully. On a failure (backend down) we stop and
    # return the offset at the start of the failed line, so the next poll
    # re-reads from there once the backend recovers — no event is ever lost.
    pos = offset
    buf = remainder
    while True:
        nl = buf.find(b"\n")
        if nl == -1:
            break  # no complete line yet — wait for more
        line_bytes = buf[: nl + 1]
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if line:
            ok = True
            for ev in _iter_assistant_events(line):
                if not _post(server, token, ev):
                    ok = False
                    break
            if not ok:
                return pos  # leave offset before the un-acked line
        pos += len(line_bytes)
        buf = buf[nl + 1 :]
    return pos


def run() -> None:
    server, token = _load_config()
    state = _load_state()
    last_rescan = 0.0
    tracked: dict[str, Path] = {}  # abs-path-str → Path
    print(f"[tailer] server={server} watching {_PROJECTS_DIR}", file=sys.stderr)
    while True:
        now = time.time()
        if now - last_rescan > _RESCAN_INTERVAL_SEC:
            if _PROJECTS_DIR.is_dir():
                for p in _PROJECTS_DIR.glob("**/*.jsonl"):
                    key = str(p)
                    if key not in tracked:
                        tracked[key] = p
                        # First-ever discovery: seek to END so we only pick up
                        # NEW assistant messages, never backfill (audit-safe —
                        # backfill would duplicate old messages already viewed
                        # from older CLI hooks).
                        if key not in state:
                            try:
                                state[key] = p.stat().st_size
                            except OSError:
                                state[key] = 0
            last_rescan = now

        any_change = False
        for key, path in list(tracked.items()):
            offset = state.get(key, 0)
            new_offset = _drain_file(path, offset, server, token)
            if new_offset != offset:
                state[key] = new_offset
                any_change = True
        if any_change:
            _save_state(state)
        time.sleep(_POLL_INTERVAL_SEC)


def backfill(session_id: str, wipe: bool = True) -> None:
    """One-shot: re-ingest every assistant event for a given session.

    Finds `~/.claude/projects/**/<session_id>.jsonl`, optionally DELETEs
    existing tailer-origin events on the backend (avoids double-counting
    tokens + cost), then replays the file from offset 0 through
    `_iter_assistant_events` → POST /sessions/events.

    Use cases:
      * Import a session that started before the tailer was installed.
      * Re-import after changing pricing rates (backend recomputes cost_usd
        at ingest time from the current rate table).

    Running it twice WITHOUT `wipe=True` will emit every event twice →
    doubled aggregates. Default `wipe=True` is safe.
    """
    server, token = _load_config()
    # 1. locate the transcript
    candidates = list(_PROJECTS_DIR.glob(f"**/{session_id}.jsonl"))
    if not candidates:
        print(f"[backfill] no transcript found for session {session_id}", file=sys.stderr)
        return
    path = candidates[0]
    print(f"[backfill] using {path}", file=sys.stderr)

    # 2. wipe existing tailer events server-side so aggregates stay honest
    if wipe:
        req = urllib.request.Request(
            f"{server}/api/v1/sessions/{session_id}/tailer-events",
            headers={"Authorization": f"Bearer {token}"},
            method="DELETE",
        )
        try:
            urllib.request.urlopen(req, timeout=10).read()
            print(f"[backfill] wiped existing tailer events for {session_id}", file=sys.stderr)
        except urllib.error.HTTPError as e:
            print(f"[backfill] wipe returned {e.code} — continuing", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[backfill] wipe failed: {e} — aborting to avoid dupes", file=sys.stderr)
            return

    # 3. replay the file — batch size kept small (50) because each event can
    # carry a multi-kB `raw` payload (full tool_input + tool_response) and
    # the backend has a body-size limit that rejects big batches with 413.
    BATCH = 50
    batch: list[dict[str, Any]] = []
    count = 0
    def _flush() -> None:
        nonlocal batch, count
        if not batch: return
        _post_batch(server, token, batch)
        count += len(batch)
        batch = []
    with open(path) as f:
        for line in f:
            for ev in _iter_assistant_events(line):
                batch.append(ev)
                if len(batch) >= BATCH:
                    _flush()
                    time.sleep(0.1)  # breathing room for slowapi
    _flush()
    print(f"[backfill] emitted {count} events for session {session_id}", file=sys.stderr)

    # 4. bump the saved offset to EOF so the live tailer doesn't re-emit
    state = _load_state()
    try:
        state[str(path)] = path.stat().st_size
        _save_state(state)
    except OSError:
        pass


def backfill_all(wipe: bool = True) -> None:
    """Backfill every transcript under ~/.claude/projects/. Useful once."""
    for path in _PROJECTS_DIR.glob("**/*.jsonl"):
        sid = path.stem
        print(f"[backfill-all] ▶ {sid}", file=sys.stderr)
        backfill(sid, wipe=wipe)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        prog="yoru-tailer",
        description="Stream Claude Code assistant messages into Yoru.",
    )
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="follow every transcript live (default)")
    bf = sub.add_parser("backfill", help="re-ingest one session's transcript")
    bf.add_argument("session_id")
    bf.add_argument("--no-wipe", action="store_true",
                    help="skip the DELETE tailer-events pre-step (will duplicate)")
    bfa = sub.add_parser("backfill-all", help="re-ingest every transcript on disk")
    bfa.add_argument("--no-wipe", action="store_true")

    args = ap.parse_args()
    try:
        if args.cmd == "backfill":
            backfill(args.session_id, wipe=not args.no_wipe)
        elif args.cmd == "backfill-all":
            backfill_all(wipe=not args.no_wipe)
        else:
            run()
    except KeyboardInterrupt:
        pass
