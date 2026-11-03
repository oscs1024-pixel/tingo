"""会议文件夹的约定：一场会议一个文件夹，命令行和图形界面共用。

<根目录>/2026-11-02_1430_周会/
├── 录音.m4a
├── 转写.md
├── transcript.json
└── 纪要.md
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from . import terms
from .config import with_legacy
from .transcript import Transcript, fmt_ts

DEFAULT_ROOT = with_legacy(Path.home() / "Documents" / "Tingo", Path.home() / "Documents" / "jiyao")
AUDIO = "录音.m4a"
MINUTES = "纪要.md"
TRANSCRIPT_MD = "转写.md"
TRANSCRIPT_JSON = "transcript.json"

_FOLDER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})_(.+)$")
_RAW_TRACKS = {"system.wav", "mic.wav", "mixed.wav"}  # 录音进行中的原始轨道
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".mp4", ".aac", ".flac", ".ogg", ".webm", ".mov", ".mkv"}


def folder_title(title: str) -> str:
    """会议标题里不能进文件夹名的字符（路径分隔符、空白等）换成下划线。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", title.strip()) or "会议"


def new_folder(root: Path, title: str, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    safe = folder_title(title)
    path = root / f"{when:%Y-%m-%d_%H%M}_{safe}"
    n = 2
    while path.exists():
        path = root / f"{when:%Y-%m-%d_%H%M}_{safe}_{n}"
        n += 1
    return path


def save_transcript(tr: Transcript, out_dir: Path) -> None:
    tr.save(out_dir / TRANSCRIPT_JSON)
    (out_dir / TRANSCRIPT_MD).write_text(tr.to_markdown(), encoding="utf-8")


def meeting_date(folder: Path) -> date:
    """文件夹名里的日期优先（tingo 录的），否则用录音或转写文件的修改时间。"""
    m = _FOLDER_RE.match(folder.name)
    if m:
        return date.fromisoformat(m.group(1))
    for f in (find_audio(folder), folder / TRANSCRIPT_JSON):
        if f and f.exists():
            return datetime.fromtimestamp(f.stat().st_mtime).date()
    return date.today()


def find_audio(folder: Path) -> Path | None:
    if (folder / AUDIO).exists():
        return folder / AUDIO
    for p in sorted(folder.iterdir()) if folder.exists() else []:
        if p.suffix.lower() in AUDIO_EXTS and p.name not in _RAW_TRACKS:
            return p
    return None


def minutes_title(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def one_liner(text: str) -> str:
    """纪要里"一句话总结"（英文纪要是 Summary）下面的那句。术语表代码块不算。"""
    lines = terms.remove(text).splitlines()
    for i, line in enumerate(lines):
        if line.strip() in ("## 一句话总结", "## Summary"):
            for nxt in lines[i + 1 :]:
                if nxt.strip():
                    return nxt.strip()
    return ""


def summary(folder: Path) -> dict:
    """会议列表里一张卡片要的信息，只读小文件，不加载转写全文。"""
    minutes_path = folder / MINUTES
    minutes = minutes_path.read_text(encoding="utf-8") if minutes_path.exists() else ""
    m = _FOLDER_RE.match(folder.name)
    started = f"{m.group(2)}:{m.group(3)}" if m else ""
    fallback_title = m.group(4) if m else folder.name
    info: dict = {
        "id": folder.name,
        "title": minutes_title(minutes) or fallback_title,
        "date": meeting_date(folder).isoformat(),
        "time": started,
        "summary": one_liner(minutes),
        "has_minutes": bool(minutes),
        "has_transcript": (folder / TRANSCRIPT_JSON).exists(),
        "duration": "",
        "speakers": [],
    }
    if info["has_transcript"]:
        tr = Transcript.load(folder / TRANSCRIPT_JSON)
        info["duration"] = fmt_ts(tr.duration_ms)
        info["speakers"] = [tr.name_of(s) for s in tr.speakers_in_order()]
    return info


def list_meetings(root: Path) -> list[dict]:
    if not root.exists():
        return []
    folders = [
        p for p in root.iterdir() if p.is_dir() and ((p / TRANSCRIPT_JSON).exists() or find_audio(p) is not None)
    ]
    items = [summary(p) for p in folders]
    return sorted(items, key=lambda x: (x["date"], x["time"], x["id"]), reverse=True)
