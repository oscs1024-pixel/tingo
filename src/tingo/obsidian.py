"""把纪要存进 Obsidian 库：一场会议一篇笔记（带属性），鱼骨图另存为 Excalidraw 画板嵌进笔记，
转写全文放在折叠的引用块里。重复导出覆盖同名笔记。与桌面版（app/engine/src/obsidian.rs）一致。
"""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import fishbone, meetings
from .config import ObsidianConfig
from .fishbone import NONE, fmt_ms
from .meetings import MINUTES, TRANSCRIPT_JSON
from .transcript import Transcript

_UNSAFE = set('/\\:*?"<>|#^[]')
_URI_SAFE = set(b"-_.~/")


class ExportError(Exception):
    """导出失败，消息直接给用户看。"""


def vaults() -> list[tuple[str, str]]:
    """本机 Obsidian 打开过的库（读 Obsidian 自己的配置），设置页里直接选。最近打开的在前。"""
    home = os.environ.get("HOME")
    if not home:
        return []
    try:
        data = json.loads((Path(home) / "Library/Application Support/obsidian/obsidian.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    found = data.get("vaults") if isinstance(data, dict) else None
    if not isinstance(found, dict):
        return []
    out = []
    for _, v in sorted(found.items()):  # 与桌面版一样按 id 排，同一时间打开的保持这个次序
        path = v.get("path") if isinstance(v, dict) else None
        if isinstance(path, str) and Path(path).is_dir():
            ts = v.get("ts")
            ts = ts if isinstance(ts, int) and not isinstance(ts, bool) and ts >= 0 else 0
            out.append((ts, Path(path).name, path))
    out.sort(key=lambda x: -x[0])
    return [(name, path) for _, name, path in out]


def safe_name(s: str) -> str:
    """文件名里不能出现的字符（Obsidian 链接也不认 # ^ [ ] |）"""
    s = "".join(" " if c in _UNSAFE or unicodedata.category(c) == "Cc" else c for c in s)
    return " ".join(s.split())


def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON 字符串也是合法的 YAML 字符串


def uri(path: Path) -> str:
    """打开某篇笔记的 obsidian:// 链接"""
    out = ["obsidian://open?path="]
    for b in str(path).encode("utf-8"):
        out.append(chr(b) if (chr(b).isascii() and chr(b).isalnum()) or b in _URI_SAFE else f"%{b:02X}")
    return "".join(out)


@dataclass
class Exported:
    note: Path
    drawings: list[Path] = field(default_factory=list)


def export(cfg: ObsidianConfig, meeting_dir: Path) -> Exported:
    vault = Path(cfg.vault.strip())
    if not cfg.vault.strip() or not vault.is_dir():
        raise ExportError("还没有设置 Obsidian 库，或者库的文件夹不存在，去设置里选一下")
    try:
        minutes = (meeting_dir / MINUTES).read_text(encoding="utf-8")
    except OSError:
        raise ExportError("这场会议还没有纪要") from None
    try:
        tr: Transcript | None = Transcript.load(meeting_dir / TRANSCRIPT_JSON)
    except Exception:  # noqa: BLE001 - 没有转写也能导出纪要
        tr = None
    meeting_id = meeting_dir.name
    date = meetings.meeting_date(meeting_dir).isoformat()
    # 用会议文件夹的标题命名（重新生成纪要标题会变，文件名不跟着变，避免同一场会出现多篇）
    parts = meeting_id.split("_", 2)
    topic = parts[2] if len(parts) == 3 else meeting_id
    name = safe_name(f"{date} {topic}")
    folder = vault / cfg.folder.strip().strip("/")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ExportError(f"建不了文件夹 {folder}：{e}") from e

    turns = tr.turns() if tr is not None else []
    duration = tr.duration_ms if tr is not None else 2**64 - 1
    body: list[str] = []
    last = 0
    drawings: list[Path] = []
    for k, ((start, end), spec) in enumerate(fishbone.blocks(minutes)):
        body.append(minutes[last:start])
        last = end
        if spec is None:
            continue
        grounds = [fishbone.ground(b, turns, duration) for b in spec.branches]
        draw_name = f"{name} 鱼骨图{'' if k == 0 else k + 1}"
        path = folder / f"{draw_name}.excalidraw.md"
        try:
            path.write_text(fishbone.excalidraw_md(fishbone.excalidraw(spec, grounds)), encoding="utf-8")
        except OSError as e:
            raise ExportError(str(e)) from e
        drawings.append(path)
        body.append(f"![[{draw_name}.excalidraw]]\n\n**{spec.problem}**\n")
        for b, g in zip(spec.branches, grounds, strict=True):
            detail = f"：{b.detail}" if b.detail else ""
            mark = " ⚠️" if g.level == NONE else ""
            body.append(f"- **{b.title}**{detail} · {g.label()}{mark}\n")
            for q in g.quotes:
                body.append(f"    - `{fmt_ms(q.ms)}` {q.speaker}：{q.text}\n")
    body.append(minutes[last:])

    title = meetings.minutes_title(minutes) or topic
    fm = ["---", f"title: {_yaml_str(title)}", f"date: {date}"]
    if tr is not None:
        fm.append(f"duration: {_yaml_str(fmt_ms(tr.duration_ms))}")
        fm.append(f"speakers: [{', '.join(_yaml_str(tr.name_of(s)) for s in tr.speakers_in_order())}]")
    fm.append("tags: [会议纪要]")
    fm.append(f"tingo: {_yaml_str(meeting_id)}")
    fm.append("---\n")
    note = "\n".join(fm) + "".join(body).rstrip() + "\n"

    if cfg.transcript and tr is not None:
        note += f"\n## 转写\n\n> [!quote]- 转写全文（{fmt_ms(tr.duration_ms)}，{len(tr.speakers_in_order())} 人）\n"
        for t in turns:
            note += f">\n> **{t.speaker}** `{fmt_ms(t.start_ms)}`\n> {t.text}\n"
    path = folder / f"{name}.md"
    try:
        path.write_text(note, encoding="utf-8")
    except OSError as e:
        raise ExportError(f"写不进 {path}：{e}") from e
    return Exported(path, drawings)
