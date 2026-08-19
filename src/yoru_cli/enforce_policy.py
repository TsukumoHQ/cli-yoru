"""Local, conservative danger policy for the OPT-IN enforcement gate (MIT).

This is the decision core of the `yoru` PreToolUse enforcement hook — a SEPARATE,
opt-in gate that is distinct from the always-on, never-blocking audit streamer.
It runs entirely locally (no network), matching a tool call against a small,
HIGH-CONFIDENCE set of irreversible/dangerous patterns. Philosophy:

  - Fail-OPEN: this returns a match ONLY on an explicit, high-confidence
    dangerous pattern. Anything it is unsure about returns None (allow). A
    benign op must never be blocked by this gate.
  - The caller (the hook) blocks + asks for human confirmation ONLY on a
    returned match, and allows on None OR on any error/timeout.

Deliberately narrow: a few destructive db / filesystem / secret-exfil shapes.
Better to miss a novel dangerous op (the passive audit trail still records it)
than to false-positive and erode trust in an opt-in safety feature.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ── high-confidence dangerous patterns ──────────────────────────────────────
# Destructive SQL: DROP TABLE/DATABASE/SCHEMA, TRUNCATE, or an unqualified
# DELETE/UPDATE (no WHERE) — the classic irreversible data-loss shapes.
_DB_DESTRUCTIVE = re.compile(
    r"""\b(
        drop\s+(table|database|schema)\b
      | truncate\s+(table\s+)?\w
      | delete\s+from\s+\w+\b(?!.*\bwhere\b)   # DELETE with no WHERE
      | update\s+\w+\s+set\b(?!.*\bwhere\b)   # UPDATE with no WHERE
    )""",
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# Recursive force-remove of a root-ish path: /, ~, $HOME, /*, or a bare -rf /.
_FS_RMRF_ROOT = re.compile(
    r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r|-r\s+-f|-f\s+-r)\b[^\n|&;]*"
    r"(\s|=)(/|~|\$HOME|/\*)(\s|/|$|\*)",
    re.IGNORECASE,
)

# Secret exfiltration: a command that BOTH reads a secret-bearing path AND pipes
# to a network egress tool. Requiring both keeps false positives near zero.
_SECRET_PATH = re.compile(
    r"(\.env\b|id_rsa\b|\.pem\b|\.aws/credentials|/etc/shadow|secrets?\.(json|ya?ml|txt))",
    re.IGNORECASE,
)
_NET_EGRESS = re.compile(
    r"\b(curl|wget|nc|ncat|scp|rsync\s+[^|]*::|http\.client|requests\.(post|put))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    rule: str        # stable rule id
    category: str     # db | shell | secret  (subset of the six-kind taxonomy)
    reason: str       # human-facing, shown at the confirmation prompt


def _command_text(tool_name: str, tool_input: dict) -> str:
    """The best-effort text to inspect for a given tool. Bash → its command;
    a file write → path + content; else the stringified input."""
    if not isinstance(tool_input, dict):
        return str(tool_input or "")
    for key in ("command", "cmd"):
        v = tool_input.get(key)
        if isinstance(v, str):
            return v
    parts = []
    for key in ("file_path", "path", "content", "new_string", "new_str"):
        v = tool_input.get(key)
        if isinstance(v, str):
            parts.append(v)
    return "\n".join(parts) if parts else str(tool_input)


def evaluate(tool_name: str, tool_input: dict) -> Optional[Violation]:
    """Return a Violation ONLY on a high-confidence dangerous match, else None
    (allow). Never raises — a policy that can't decide must fail open."""
    try:
        text = _command_text(tool_name or "", tool_input or {})
    except Exception:
        return None
    if not text:
        return None

    if _FS_RMRF_ROOT.search(text):
        return Violation(
            "fs_rm_rf_root", "shell",
            "recursive force-delete of a root/home path (rm -rf) — irreversible",
        )
    if _DB_DESTRUCTIVE.search(text):
        return Violation(
            "db_destructive", "db",
            "destructive SQL (DROP/TRUNCATE or an unqualified DELETE/UPDATE) — "
            "irreversible data loss",
        )
    if _SECRET_PATH.search(text) and _NET_EGRESS.search(text):
        return Violation(
            "secret_exfil", "secret",
            "reads a secret-bearing file AND pipes to a network tool — "
            "possible credential exfiltration",
        )
    return None
