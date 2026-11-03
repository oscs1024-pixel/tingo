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


def test_srt_numbering_times_and_names():
    from tingo.transcript import srt_ts

    tr = Transcript(
        "a.wav",
        3_725_000,
        [
            Sentence(0, 0, 1500, "大家好。"),
            Sentence(1, 61_001, 62_340, "开始吧。"),
            Sentence(0, 3_600_000, 3_725_000, "散会。"),
        ],
        {0: "林舟"},
    )
    assert tr.to_srt() == (
        "1\n00:00:00,000 --> 00:00:01,500\n林舟：大家好。\n\n"
        "2\n00:01:01,001 --> 00:01:02,340\n说话人2：开始吧。\n\n"
        "3\n01:00:00,000 --> 01:02:05,000\n林舟：散会。\n"
    )
    assert tr.to_srt(en=True).split("\n\n")[1] == "2\n00:01:01,001 --> 00:01:02,340\nSpeaker 2: 开始吧。"
    assert srt_ts(-5) == "00:00:00,000" and srt_ts(100 * 3600 * 1000 + 7) == "100:00:00,007"
