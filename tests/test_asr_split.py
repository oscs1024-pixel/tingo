from jiyao.asr import _speaker_at, split_by_speaker

# FunASR 的说话人分段：(起秒, 止秒, 说话人)
SEGMENTS = [(0.0, 10.3, 0), (10.4, 18.0, 1), (19.0, 23.1, 0)]


def _sentence(text, stamps, spk=0):
    return {"text": text, "start": stamps[0][0], "end": stamps[-1][1], "timestamp": stamps, "spk": spk}


def test_speaker_at_inside_and_gap():
    assert _speaker_at(5.0, SEGMENTS) == 0
    assert _speaker_at(12.0, SEGMENTS) == 1
    assert _speaker_at(18.3, SEGMENTS) == 1  # 间隙里取最近的分段
    assert _speaker_at(18.9, SEGMENTS) == 0


def test_split_at_speaker_change():
    # "迁移" 属于说话人 0，"发布" 是说话人 1 开口的前两个字
    s = _sentence("迁移发布，", [[9000, 9400], [9400, 9800], [10500, 10800], [10800, 11100]])
    out = split_by_speaker(s, SEGMENTS)
    assert [(p.speaker, p.text) for p in out] == [(0, "迁移。"), (1, "发布，")]
    assert out[1].start_ms == 10500


def test_single_speaker_relabels_whole_sentence():
    s = _sentence("有点紧。", [[11000, 11300], [11300, 11600], [11600, 11900]], spk=0)
    out = split_by_speaker(s, SEGMENTS)
    assert [(p.speaker, p.text) for p in out] == [(1, "有点紧。")]


def test_mixed_english_tokens_align():
    s = _sentence("用 Kafka 做，好的。", [[1000, 1200], [1200, 1800], [1800, 2000], [10500, 10700], [10700, 10900]])
    out = split_by_speaker(s, SEGMENTS)
    assert [(p.speaker, p.text) for p in out] == [(0, "用 Kafka 做，"), (1, "好的。")]


def test_token_count_mismatch_falls_back_to_whole_sentence():
    s = _sentence("好的。", [[1000, 2000]], spk=1)
    out = split_by_speaker(s, SEGMENTS)
    assert [(p.speaker, p.text) for p in out] == [(1, "好的。")]


def test_no_segments_keeps_funasr_label():
    s = _sentence("你好。", [[0, 300], [300, 600]], spk=2)
    assert split_by_speaker(s, [])[0].speaker == 2


def test_empty_text():
    assert split_by_speaker({"text": " ", "start": 0, "end": 0}, SEGMENTS) == []
