import json
from pathlib import Path

import pytest

from tingo import obsidian
from tingo.config import ObsidianConfig
from tingo.meetings import MINUTES, TRANSCRIPT_JSON

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
        'speakers: ["李经理", "小张"]\ntags: [会议纪要]\ntingo: "2026-11-02_1030_发布评审"\n---\n# 发布评审会' in note
    )
    assert "![[2026-11-02 发布评审 鱼骨图.excalidraw]]\n\n**为什么延期？**\n" in note
    assert "- **支付有问题**\n" in note
    assert "- **猜的**" in note
    assert "    - `00:09` 小张：支付模块" not in note
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


EN_MINUTES_TEXT = (
    "# Release review\n\n## Summary\nDelayed.\n\n## Root Cause Analysis\n```fishbone\n"
    '{"problem": "Why late?", "branches": [{"title": "Payments", "detail": "two bugs", "refs": ["00:09"]}, '
    '{"title": "Guess", "refs": []}]}\n```\n'
)


def test_english_minutes_note_labels(tmp_path):
    d = make_meeting(tmp_path)
    data = json.loads((d / TRANSCRIPT_JSON).read_text(encoding="utf-8"))
    data["speaker_names"] = {"0": "Lin"}  # 第二个人没命名
    (d / TRANSCRIPT_JSON).write_text(json.dumps(data), encoding="utf-8")
    (d / MINUTES).write_text(EN_MINUTES_TEXT, encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    out = obsidian.export(ObsidianConfig(str(vault)), d)
    assert out.note == vault / "会议纪要" / "2026-11-02 发布评审.md"  # 默认文件夹不变
    note = out.note.read_text(encoding="utf-8")
    assert 'speakers: ["Lin", "Speaker 2"]\ntags: [meeting-notes]\n' in note
    assert "![[2026-11-02 发布评审 fishbone.excalidraw]]\n\n**Why late?**\n" in note
    assert "- **Payments**: two bugs\n" in note
    assert "- **Guess**" in note
    assert "\n## Transcript\n\n> [!quote]- Full transcript (00:52, 2 speakers)\n>\n> **Lin** `00:00`" in note
    assert "> **Speaker 2** `00:09`" in note and "说话人" not in note
    assert out.drawings == [vault / "会议纪要" / "2026-11-02 发布评审 fishbone.excalidraw.md"]
    assert "No evidence ^jy" not in out.drawings[0].read_text(encoding="utf-8")


def test_export_errors_follow_ui_language(tmp_path):
    from tingo import i18n

    d = make_meeting(tmp_path)
    i18n.set_lang("en")
    with pytest.raises(obsidian.ExportError, match="No Obsidian vault is set"):
        obsidian.export(ObsidianConfig(str(tmp_path / "nope")), d)
