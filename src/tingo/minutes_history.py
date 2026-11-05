"""Minutes snapshots, shared on disk with the desktop backend."""

import json
import threading
import time
from pathlib import Path

from .meetings import MINUTES

_LOCK = threading.RLock()
_HISTORY = "minutes-history"


def _path(folder: Path, version: str) -> Path:
    if not version or any(c not in "0123456789-" for c in version):
        raise ValueError("Invalid version ID")
    return folder / _HISTORY / f"{version}.json"


def save(folder: Path, text: str) -> None:
    with _LOCK:
        current = folder / MINUTES
        old = current.read_text(encoding="utf-8") if current.exists() else None
        if old == text:
            return
        temp = folder / ".minutes-new.tmp"
        try:
            temp.write_text(text, encoding="utf-8")
            if old and old.strip():
                (folder / _HISTORY).mkdir(exist_ok=True)
                version = str(time.time_ns())
                data = {"id": version, "saved_at": current.stat().st_mtime_ns // 1_000_000, "text": old}
                with _path(folder, version).open("x", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            temp.replace(current)
        finally:
            temp.unlink(missing_ok=True)


def versions(folder: Path) -> list[dict]:
    with _LOCK:
        items = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in (folder / _HISTORY).glob("*.json")
            if p.is_file() and not p.is_symlink()
        ]
        return sorted(items, key=lambda item: (item["saved_at"], item["id"]), reverse=True)


def delete(folder: Path, version: str) -> None:
    with _LOCK:
        _path(folder, version).unlink()
