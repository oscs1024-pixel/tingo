"""纪要里的鱼骨图（```fishbone 代码块，JSON）：解析、对照转写核实引用、画成 Excalidraw。

引用核实的规则与界面（app/src/fishbone.ts）、桌面版（app/engine/src/fishbone.rs）一致：
时间戳落在某段发言里才算引用了这段话。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

from .transcript import Turn, fmt_ts

FENCE = re.compile(r"```fishbone\s*\n(.*?)\n```", re.DOTALL)
WEAK_CHARS = 20  # 引用的原文不到这么多字，算"依据很少"
_NUM = re.compile(r"\+?[0-9]+")

fmt_ms = fmt_ts  # 与桌面版同名，格式相同（mm:ss / h:mm:ss）


@dataclass
class Branch:
    title: str = ""
    detail: str = ""
    refs: list[str] = field(default_factory=list)


@dataclass
class Spec:
    problem: str
    branches: list[Branch]


def parse(text: str) -> Spec | None:
    """字段类型不对就整块作废（与桌面版的严格解析一致），空标题的分支丢掉。"""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("problem"), str):
        return None
    raw = data.get("branches")
    if not isinstance(raw, list):
        return None
    branches = []
    for b in raw:
        if not isinstance(b, dict):
            return None
        title, detail, refs = b.get("title", ""), b.get("detail", ""), b.get("refs", [])
        if not isinstance(title, str) or not isinstance(detail, str):
            return None
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            return None
        if title:
            branches.append(Branch(title, detail, list(refs)))
    return Spec(data["problem"], branches)


def blocks(md: str) -> list[tuple[tuple[int, int], Spec | None]]:
    """纪要里所有鱼骨图代码块：（代码块在原文里的位置，解析结果）。"""
    return [(m.span(), parse(m.group(1))) for m in FENCE.finditer(md)]


@dataclass
class Quote:
    ms: int
    speaker: str
    text: str


OK, WEAK, NONE = "ok", "weak", "none"


@dataclass
class Grounding:
    quotes: list[Quote]
    chars: int
    level: str

    def label(self, en: bool = False) -> str:
        """引用统计。en 跟纪要的语言走（英文纪要用英文），不看界面语言。"""
        n = len(self.quotes)
        if en:
            if self.level == NONE:
                return "No evidence"
            stats = f"{n} quote(s) · {self.chars} chars"
            return f"Thin evidence · {stats}" if self.level == WEAK else stats
        if self.level == NONE:
            return "无原文依据"
        if self.level == WEAK:
            return f"依据很少 · 引用 {n} 段 · {self.chars} 字"
        return f"引用 {n} 段 · {self.chars} 字"


def _ts_ms(t: str) -> int | None:
    parts = t.split(":")
    if not all(_NUM.fullmatch(p) for p in parts):
        return None
    n = [int(p) for p in parts]
    if len(n) == 2:
        return (n[0] * 60 + n[1]) * 1000
    if len(n) == 3:
        return (n[0] * 3600 + n[1] * 60 + n[2]) * 1000
    return None


def resolve(turns: list[Turn], duration_ms: int, ref: str) -> Quote | None:
    ms = _ts_ms(ref.strip().strip("[]"))
    if ms is None or ms > duration_ms + 1000:
        return None
    hit = None
    for t in turns:
        if t.start_ms > ms + 999:
            break
        hit = t
    return Quote(hit.start_ms, hit.speaker, hit.text) if hit else None


def ground(b: Branch, turns: list[Turn], duration_ms: int) -> Grounding:
    quotes: list[Quote] = []
    for r in b.refs:
        q = resolve(turns, duration_ms, r)
        if q and not any(x.ms == q.ms for x in quotes):
            quotes.append(q)
    quotes.sort(key=lambda q: q.ms)
    chars = sum(len(q.text) for q in quotes)
    level = NONE if not quotes else WEAK if chars < WEAK_CHARS else OK
    return Grounding(quotes, chars, level)


# ---------- Excalidraw ----------

INK = "#1e1e1e"
MUTED = "#868e96"
_COLORS = {OK: INK, WEAK: "#f08c00", NONE: "#e03131"}


def _lines(s: str) -> list[str]:
    """与 Rust 的 str::lines 相同：按 \\n 或 \\r\\n 分，末尾换行后的空串不算一行。"""
    out = s.split("\n")
    last = out.pop()
    return [line.removesuffix("\r") for line in out] + ([last] if last else [])


def _cw(c: str) -> float:
    return 0.6 if ord(c) < 128 else 1.0


def _text_width(s: str, size: float) -> float:
    """估算文字宽度：汉字算一个字号宽，其余算半个多。Excalidraw 打开后会按真实字体重新排。"""
    return max((sum(_cw(c) for c in line) * size for line in _lines(s)), default=0.0)


def _wrap(s: str, size: float, width: float) -> str:
    """按宽度折行（中文没有空格，逐字折）。"""
    out, w = [], 0.0
    for c in s:
        cw = _cw(c) * size
        if w + cw > width and w > 0.0:
            out.append("\n")
            w = 0.0
        out.append(c)
        w += cw
    return "".join(out)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


class _Canvas:
    def __init__(self) -> None:
        self.elements: list[dict] = []
        self.seed = 0

    def _next(self) -> tuple[str, int]:
        self.seed += 1
        return f"jy{self.seed * 7919 % 0xFFFFFF:06x}", 100_000 + self.seed * 7919

    def _base(self, kind: str, x: float, y: float, w: float, h: float, stroke: str) -> dict:
        id_, seed = self._next()
        return {
            "id": id_, "type": kind, "x": x, "y": y, "width": w, "height": h, "angle": 0,
            "strokeColor": stroke, "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 2, "strokeStyle": "solid", "roughness": 1, "opacity": 100,
            "groupIds": [], "frameId": None, "roundness": None, "seed": seed, "version": 1,
            "versionNonce": seed + 1, "isDeleted": False, "boundElements": [], "updated": 1,
            "link": None, "locked": False,
        }  # fmt: skip

    def text(self, x: float, y: float, s: str, size: float, stroke: str) -> float:
        h = max(len(_lines(s)), 1) * size * 1.25
        e = self._base("text", x, y, _text_width(s, size), h, stroke)
        e.update(
            text=s, originalText=s, fontSize=size, fontFamily=5, textAlign="left", verticalAlign="top",
            containerId=None, lineHeight=1.25, autoResize=True,
        )  # fmt: skip
        self.elements.append(e)
        return h

    def line(self, start: tuple[float, float], end: tuple[float, float], stroke: str, width: float) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        e = self._base("line", start[0], start[1], abs(dx), abs(dy), stroke)
        e["strokeWidth"] = width
        e["points"] = [[0, 0], [dx, dy]]
        for k in ("lastCommittedPoint", "startBinding", "endBinding", "startArrowhead", "endArrowhead"):
            e[k] = None
        self.elements.append(e)

    def rect(self, x: float, y: float, w: float, h: float, stroke: str, fill: str) -> None:
        e = self._base("rectangle", x, y, w, h, stroke)
        e["backgroundColor"] = fill
        e["roundness"] = {"type": 3}
        self.elements.append(e)


def excalidraw(spec: Spec, grounds: list[Grounding], en: bool = False) -> dict:
    """画一张经典鱼骨图：主骨水平，问题在鱼头（右侧），分支上下交替斜插在主骨上，
    导出仅保留标题和说明；没有原文依据的分支画成红色。en：纪要是英文的。"""
    col_w, rise, slant, text_w = 300.0, 150.0, 110.0, 270.0  # 每对分支占的宽度、斜骨高度
    c = _Canvas()
    cols = float(max(math.ceil(len(spec.branches) / 2), 1))
    spine_end = cols * col_w + 40.0

    for i, (b, g) in enumerate(zip(spec.branches, grounds, strict=True)):
        up = i % 2 == 0
        color = _COLORS[g.level]
        attach = ((i // 2) * col_w + 230.0, 0.0)
        tip = (attach[0] - slant, -rise if up else rise)
        c.line(tip, attach, color, 2.0)

        # 导出仅保留标题和补充说明
        parts = [(_wrap(b.title, 20.0, text_w), 20.0, color)]
        if b.detail:
            parts.append((_wrap(b.detail, 14.0, text_w), 14.0, MUTED))
        gap = 6.0
        height = sum(len(_lines(s)) * size * 1.25 + gap for s, size, _ in parts)
        x = tip[0] - 40.0
        y = tip[1] - height - 8.0 if up else tip[1] + 10.0
        for s, size, col in parts:
            y += c.text(x, y, s, size, col) + gap

    c.line((0.0, 0.0), (spine_end, 0.0), INK, 4.0)
    # 鱼头：问题
    problem = _wrap(spec.problem, 22.0, 300.0)
    w, h = _text_width(problem, 22.0) + 40.0, len(_lines(problem)) * 27.5 + 32.0
    c.rect(spine_end, -h / 2.0, w, h, INK, "#e7f5ff")
    c.text(spine_end + 20.0, -h / 2.0 + 16.0, problem, 22.0, INK)

    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://github.com/tancheng33/tingo",
        "elements": c.elements,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {},
    }


def dumps(drawing: dict) -> str:
    """与桌面版的 serde_json::to_string_pretty 输出一致（键按字母序）。"""
    return json.dumps(drawing, ensure_ascii=False, indent=2, sort_keys=True)


def excalidraw_md(drawing: dict) -> str:
    """Obsidian Excalidraw 插件的 .excalidraw.md 格式：文字元素列在正文里便于搜索，画面数据放在注释里。"""
    texts = [f"{e.get('text', '')} ^{e.get('id', '')}\n" for e in drawing.get("elements", []) if e["type"] == "text"]
    return (
        "---\n\nexcalidraw-plugin: parsed\ntags: [excalidraw]\n\n---\n"
        "==⚠  Switch to EXCALIDRAW VIEW in the MORE OPTIONS menu of this document. ⚠==\n\n\n"
        "# Excalidraw Data\n\n## Text Elements\n"
        f"{chr(10).join(texts)}\n%%\n## Drawing\n```json\n{dumps(drawing)}\n```\n%%"
    )
