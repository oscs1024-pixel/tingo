import struct
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from jiyao import live as lv
from jiyao.live import GrowingWav, LiveTranscriber, find_cut

SR = 16000
FIXTURE = Path(__file__).parent / "fixtures" / "meeting_zh.wav"


def _streaming_header(f, rate=48000, tag=3, bits=32):
    # 录音组件写文件时长度字段还没定，用 0xFFFFFFFF 占位
    block = bits // 8
    f.write(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE")
    f.write(b"fmt " + struct.pack("<IHHIIHH", 16, tag, 1, rate, rate * block, block, bits))
    f.write(b"data" + struct.pack("<I", 0xFFFFFFFF))


def test_growing_wav_reads_by_file_size_and_resamples(tmp_path):
    path = tmp_path / "system.wav"
    with path.open("wb") as f:
        _streaming_header(f)
        f.write(np.full(48000, 0.5, dtype="<f4").tobytes())  # 1 秒
    w = GrowingWav(path)
    assert w.seconds() == pytest.approx(1.0)
    x = w.read(0.25, 0.75)
    assert len(x) == pytest.approx(0.5 * SR, abs=2)
    assert np.allclose(x[100:-100], 0.5, atol=1e-3)
    with path.open("ab") as f:  # 继续写入，长度跟着变
        f.write(np.zeros(24000, dtype="<f4").tobytes())
    assert w.seconds() == pytest.approx(1.5)


def test_growing_wav_int16_and_missing_file(tmp_path):
    path = tmp_path / "mic.wav"
    assert GrowingWav(path).seconds() == 0.0
    with path.open("wb") as f:
        _streaming_header(f, rate=16000, tag=1, bits=16)
        f.write(np.full(16000, 16384, dtype="<i2").tobytes())
    x = GrowingWav(path).read(0, 1)
    assert np.allclose(x, 0.5, atol=1e-4)


def _speech_with_gap(gap_at_s, total_s=30.0):
    t = np.arange(int(total_s * SR)) / SR
    x = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    x[int(gap_at_s * SR) : int((gap_at_s + 0.6) * SR)] = 0
    return x


def test_find_cut_picks_the_pause():
    cut = find_cut(_speech_with_gap(24.0), force=False)
    assert 24.0 * SR < cut < 24.6 * SR


def test_find_cut_waits_when_no_pause_unless_forced():
    x = _speech_with_gap(2.0)  # 停顿不在搜索范围里
    assert find_cut(x, force=False) is None
    assert find_cut(x, force=True) is not None


@pytest.mark.slow
def test_live_matches_offline_speakers_across_chunks(tmp_path, monkeypatch):
    """模拟录音组件边写文件边转写：切成多段后说话人编号仍然全场一致。"""
    from jiyao.record import _read16k

    monkeypatch.setattr(lv, "MIN_CHUNK_S", 8.0)  # 52 秒的素材切成好几段
    monkeypatch.setattr(lv, "MAX_CHUNK_S", 16.0)
    monkeypatch.setattr(lv, "SEARCH_S", 6.0)
    audio = _read16k(FIXTURE)
    files = {s: (tmp_path / f"{s}.wav").open("wb") for s in ("system", "mic")}
    for f in files.values():
        _streaming_header(f)

    def writer():
        step = SR // 4
        for i in range(0, len(audio), step):
            files["system"].write(np.repeat(audio[i : i + step], 3).astype("<f4").tobytes())
            files["mic"].write(np.zeros(len(audio[i : i + step]) * 3, dtype="<f4").tobytes())
            for f in files.values():
                f.flush()
            time.sleep(0.02)

    heard = []
    t = LiveTranscriber(tmp_path, ["system", "mic"], on_sentence=lambda text, ms: heard.append(text))
    t.start()
    w = threading.Thread(target=writer)
    w.start()
    w.join()
    tr = t.finish(tmp_path / "录音.m4a")
    assert t.error is None
    assert len(t.chunks) >= 3
    assert heard, "录音过程中就应该有句子出来"
    assert len(tr.speakers_in_order()) == 2
    turns = tr.turns()
    # 素材是两人轮流说的 7 段；换段处的说话人一旦在不同片段里编号不一致，段数就会变
    assert len(turns) == 7, [t.text[:10] for t in turns]
    assert turns[0].text.startswith("大家好")
    assert "二十五号上线" in tr.to_prompt_text()
    assert tr.duration_ms == pytest.approx(len(audio) / SR * 1000, abs=300)
