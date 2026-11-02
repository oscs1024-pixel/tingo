import json
from pathlib import Path

import pytest

from jiyao import obsidian
from jiyao.config import ObsidianConfig
from jiyao.meetings import MINUTES, TRANSCRIPT_JSON

MINUTES_TEXT = (
    "# 发布评审会\n\n## 原因分析\n```fishbone\n"
    '{"problem": "为什么延期？", "branches": [{"title": "支付有问题", "refs": ["00:09"]}, {"title": "猜的", "refs": []}]}'
    "\n```\n"
)
TRANSCRIPT = {
    "source": "a.wav",
    "duration_ms": 52000,
    "speaker_names": {"0": "李经理", "1": "小张"},
    "sentences": [
        {"speaker": 0, "start_ms": 0, "end_ms": 8000, "text": "今天讨论发布。"},
        {"speaker": 1, "start_ms": 9000, "end_ms": 18000, "text": "支付模块还有两个问题没修，十五号才能提测。"},
    ],
}


def make_meeting(base: Path) -> Path:
    d = base / "meetings" / "2026-11-02_1030_发布评审"
    d.mkdir(parents=True)
    (d / TRANSCRIPT_JSON).write_text(json.dumps(TRANSCRIPT, ensure_ascii=False), encoding="utf-8")
    (d / MINUTES).write_text(MINUTES_TEXT, encoding="utf-8")
    return d


def test_exports_note_with_drawing_and_transcript(tmp_path):
    d = make_meeting(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    out = obsidian.export(ObsidianConfig(str(vault), "会议纪要", True, True), d)
    assert out.note == vault / "会议纪要" / "2026-11-02 发布评审.md"
    note = out.note.read_text(encoding="utf-8")
    assert note.startswith('---\ntitle: "发布评审会"\ndate: 2026-11-02\nduration: "00:52"\n')
    assert (
        'speakers: ["李经理", "小张"]\ntags: [会议纪要]\njiyao: "2026-11-02_1030_发布评审"\n---\n# 发布评审会' in note
    )
    assert "![[2026-11-02 发布评审 鱼骨图.excalidraw]]\n\n**为什么延期？**\n" in note
    assert "- **支付有问题** · 引用 1 段 · 21 字\n" in note
    assert "- **猜的** · 无原文依据 ⚠️" in note
    assert "    - `00:09` 小张：支付模块" in note
    assert "> [!quote]- 转写全文（00:52，2 人）\n>\n> **李经理** `00:00`\n> 今天讨论发布。\n" in note
    assert "```fishbone" not in note
    assert out.drawings == [vault / "会议纪要" / "2026-11-02 发布评审 鱼骨图.excalidraw.md"]
    drawing = out.drawings[0].read_text(encoding="utf-8")
    assert drawing.startswith("---\n\nexcalidraw-plugin: parsed") and "为什么延期？ ^jy" in drawing
    assert obsidian.uri(Path("/a b/会")) == "obsidian://open?path=/a%20b/%E4%BC%9A"


def test_export_without_transcript_section_or_vault(tmp_path):
    d = make_meeting(tmp_path)
    vault = tmp_path / "vault"
    with pytest.raises(obsidian.ExportError, match="还没有设置 Obsidian 库"):
        obsidian.export(ObsidianConfig(str(vault)), d)
    vault.mkdir()
    out = obsidian.export(ObsidianConfig(str(vault), "/笔记/会议/", True, False), d)
    assert out.note.parent == vault / "笔记" / "会议"
    assert "转写全文" not in out.note.read_text(encoding="utf-8")
    (d / MINUTES).unlink()
    with pytest.raises(obsidian.ExportError, match="还没有纪要"):
        obsidian.export(ObsidianConfig(str(vault)), d)


def test_safe_name():
    assert obsidian.safe_name("a/b:c#d [x]\t y") == "a b c d x y"


def test_vaults_read_from_obsidian_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert obsidian.vaults() == []
    conf = tmp_path / "Library" / "Application Support" / "obsidian"
    conf.mkdir(parents=True)
    (tmp_path / "旧库").mkdir()
    (tmp_path / "新库").mkdir()
    (conf / "obsidian.json").write_text(
        json.dumps(
            {
                "vaults": {
                    "a": {"path": str(tmp_path / "旧库"), "ts": 1},
                    "b": {"path": str(tmp_path / "新库"), "ts": 9, "open": True},
                    "c": {"path": str(tmp_path / "已删除"), "ts": 20},
                }
            }
        )
    )
    assert obsidian.vaults() == [("新库", str(tmp_path / "新库")), ("旧库", str(tmp_path / "旧库"))]
