from tingo.transcript import Sentence, Transcript, fmt_ts


def _tr():
    return Transcript(
        source="/x/周会.m4a",
        duration_ms=65_000,
        sentences=[
            Sentence(1, 0, 1000, "大家好，"),
            Sentence(1, 1000, 2000, "开始吧。"),
            Sentence(0, 2500, 4000, "好的。"),
            Sentence(1, 61_000, 62_000, "散会。"),
        ],
    )


def test_turns_merge_consecutive_same_speaker():
    turns = _tr().turns()
    assert [(t.speaker, t.text) for t in turns] == [
        ("说话人2", "大家好，开始吧。"),
        ("说话人1", "好的。"),
        ("说话人2", "散会。"),
    ]
    assert turns[0].end_ms == 2000


def test_rename_by_first_appearance_and_blank_keeps_default():
    tr = _tr()
    tr.rename(["张三", ""])
    assert tr.name_of(1) == "张三"
    assert tr.name_of(0) == "说话人1"


def test_prompt_text_and_markdown():
    tr = _tr()
    assert tr.to_prompt_text().splitlines()[-1] == "[01:01] 说话人2：散会。"
    md = tr.to_markdown()
    assert md.startswith("# 会议转写：周会")
    assert "说话人 2 位" in md


def test_save_load_roundtrip(tmp_path):
    tr = _tr()
    tr.rename(["张三", "李四"])
    path = tmp_path / "t.json"
    tr.save(path)
    loaded = Transcript.load(path)
    assert loaded.speaker_names == {1: "张三", 0: "李四"}
    assert loaded.sentences == tr.sentences


def test_fmt_ts():
    assert fmt_ts(0) == "00:00"
    assert fmt_ts(61_500) == "01:01"
    assert fmt_ts(3_725_000) == "1:02:05"


def test_title_uses_meeting_folder_for_recordings():
    tr = _tr()
    assert tr.title() == "周会"
    tr.source = "/Users/x/Documents/tingo/2026-11-02_1213_周会/录音.m4a"
    assert tr.title() == "2026-11-02_1213_周会"
