"""转写结果的数据结构、合并与渲染。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Sentence:
    speaker: int
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Turn:
    """同一说话人连续说的一段话。"""

    speaker: str
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Transcript:
    source: str
    duration_ms: int
    sentences: list[Sentence]
    speaker_names: dict[int, str] = field(default_factory=dict)

    def name_of(self, speaker: int) -> str:
        return self.speaker_names.get(speaker) or f"说话人{speaker + 1}"

    def speakers_in_order(self) -> list[int]:
        """按首次发言顺序排列的说话人编号。"""
        seen: list[int] = []
        for s in self.sentences:
            if s.speaker not in seen:
                seen.append(s.speaker)
        return seen

    def rename(self, names: list[str]) -> None:
        """按首次发言顺序给说话人命名，空字符串表示保留默认名。"""
        for spk, name in zip(self.speakers_in_order(), names):
            if name.strip():
                self.speaker_names[spk] = name.strip()

    def turns(self) -> list[Turn]:
        turns: list[Turn] = []
        for s in self.sentences:
            name = self.name_of(s.speaker)
            if turns and turns[-1].speaker == name:
                last = turns[-1]
                last.text += s.text
                last.end_ms = s.end_ms
            else:
                turns.append(Turn(name, s.start_ms, s.end_ms, s.text))
        return turns

    def to_markdown(self) -> str:
        lines = [f"# 会议转写：{self.title()}", ""]
        lines.append(f"时长 {fmt_ts(self.duration_ms)}，说话人 {len(self.speakers_in_order())} 位。")
        lines.append("")
        for t in self.turns():
            lines.append(f"**{t.speaker}** `{fmt_ts(t.start_ms)}`")
            lines.append("")
            lines.append(t.text)
            lines.append("")
        return "\n".join(lines)

    def title(self) -> str:
        """`tingo record` 的录音统一叫 录音.m4a，这时用会议文件夹名（如 2026-11-02_1430_周会）。"""
        src = Path(self.source)
        return src.parent.name if src.stem == "录音" and src.parent.name else src.stem

    def to_prompt_text(self) -> str:
        """喂给 LLM 的紧凑格式：[mm:ss] 说话人：内容。"""
        return "\n".join(f"[{fmt_ts(t.start_ms)}] {t.speaker}：{t.text}" for t in self.turns())

    def save(self, path: Path) -> None:
        data = {
            "source": self.source,
            "duration_ms": self.duration_ms,
            "speaker_names": {str(k): v for k, v in self.speaker_names.items()},
            "sentences": [asdict(s) for s in self.sentences],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Transcript:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            source=data["source"],
            duration_ms=data["duration_ms"],
            sentences=[Sentence(**s) for s in data["sentences"]],
            speaker_names={int(k): v for k, v in data.get("speaker_names", {}).items()},
        )


def fmt_ts(ms: int) -> str:
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
