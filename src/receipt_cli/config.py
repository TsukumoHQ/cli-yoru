from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    return Path.home() / ".config" / "receipt"


def _config_file() -> Path:
    return _config_dir() / "config.json"


CONFIG_DIR: Path = _config_dir()
CONFIG_FILE: Path = _config_file()


def exists() -> bool:
    return _config_file().is_file()


def load() -> dict[str, Any] | None:
    path = _config_file()
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save(data: dict[str, Any]) -> None:
    dir_path = _config_dir()
    file_path = _config_file()
    os.makedirs(dir_path, mode=0o700, exist_ok=True)
    payload = dict(data)
    payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
