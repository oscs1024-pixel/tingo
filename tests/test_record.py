import subprocess

import numpy as np
import pytest

from jiyao.record import _read16k, duck_mix, mixdown

SR = 16000


def _tone(seconds, amp=0.3, freq=440, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds, sr=SR):
    return np.zeros(int(seconds * sr), dtype=np.float32)


def test_mic_muted_while_system_active_and_through_hold():
    system = np.concatenate([_tone(1.0), _silence(2.0)])
    echo = np.concatenate([_silence(0.05), 0.5 * _tone(1.0), _silence(1.95)])  # 回声比原声晚 50 ms
    out = duck_mix(system, echo, SR, hold_ms=400)
    # 系统声音期间 + 400 ms 余量内，输出与系统声音一致，回声一点都不进来
    a = slice(0, int(1.38 * SR))
    assert np.max(np.abs(out[a] - system[a])) < 1e-6


def test_mic_passes_when_system_silent():
    system = np.concatenate([_tone(1.0), _silence(2.0)])
    local = np.concatenate([_silence(2.0), _tone(1.0, amp=0.2, freq=300)])
    out = duck_mix(system, local, SR)
    b = slice(int(2.05 * SR), int(2.95 * SR))
    assert np.allclose(out[b], local[b], atol=1e-6)


def test_tracks_of_different_length_are_padded():
    out = duck_mix(_tone(1.0), _tone(1.5, amp=0.1), SR)
    assert len(out) == int(1.5 * SR)


def test_mixdown_two_tracks(tmp_path):
    import soundfile as sf

    # 系统声音和麦克风的采样率可能不同
    sf.write(tmp_path / "system.wav", np.concatenate([_tone(0.5, sr=48000), _silence(1.0, sr=48000)]), 48000)
    sf.write(tmp_path / "mic.wav", np.concatenate([_silence(1.0, sr=44100), _tone(0.5, amp=0.2, sr=44100)]), 44100)
    dst = mixdown(tmp_path)
    assert dst.name == "audio.m4a"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audio.m4a"]  # 原始轨道已清理
    audio = _read16k(dst)
    assert abs(len(audio) / SR - 1.5) < 0.1


def test_mixdown_without_audio_raises(tmp_path):
    with pytest.raises(RuntimeError, match="没有录到"):
        mixdown(tmp_path)


def test_recorder_swift_compiles(tmp_path):
    import shutil

    from jiyao.record import SWIFT_SRC

    if shutil.which("swiftc") is None:
        pytest.skip("没有 swiftc")
    proc = subprocess.run(
        ["swiftc", "-O", str(SWIFT_SRC), "-o", str(tmp_path / "rec")], check=False, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_mic_muted_slightly_before_system_starts():
    # 系统声音轨比麦克风里的回声晚到几十毫秒，开头那一小段也不能漏
    system = np.concatenate([_silence(1.0), _tone(1.0)])
    echo = np.concatenate([_silence(0.95), 0.5 * _tone(1.05)])  # 回声比系统轨早 50 ms
    out = duck_mix(system, echo, SR, pre_ms=120)
    a = slice(int(0.95 * SR), int(1.0 * SR))
    assert np.max(np.abs(out[a])) < 1e-3
