import subprocess

import numpy as np
import pytest

from tingo.record import _read16k, duck_mix, mixdown

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

    from tingo.record import SWIFT_SRC

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


def _write_wav(path, x, sr=48000, float32=True, extra_chunk=False):
    """录音组件那样的 wav：48 kHz 单声道 float32；extra_chunk 模拟 AVAudioFile 写的 FLLR 填充块。"""
    import struct

    data = x.astype("<f4").tobytes() if float32 else (x * 32767).astype("<i2").tobytes()
    tag, width = (3, 4) if float32 else (1, 2)
    fmt = struct.pack("<HHIIHH", tag, 1, sr, sr * width, width, width * 8)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if extra_chunk:
        chunks += b"FLLR" + struct.pack("<I", 12) + b"\0" * 12
    # 正在写入的文件头里长度是 0，按文件大小算
    path.write_bytes(b"RIFF" + struct.pack("<I", 0) + b"WAVE" + chunks + b"data" + struct.pack("<I", 0) + data)


def test_truncate_wav_fixes_header_and_keeps_samples(tmp_path):
    import struct

    import soundfile as sf

    from tingo.record import truncate_wav

    sr = 48000
    x = np.concatenate([_tone(2.0, sr=sr), _silence(3.0, sr=sr)])
    p = tmp_path / "system.wav"
    _write_wav(p, x, sr, extra_chunk=True)
    assert truncate_wav(p, 2.5)
    head = p.read_bytes()
    data_at = head.find(b"data")
    assert struct.unpack_from("<I", head, 4)[0] == len(head) - 8
    assert struct.unpack_from("<I", head, data_at + 4)[0] == len(head) - data_at - 8 == int(2.5 * sr) * 4
    y, rate = sf.read(p, dtype="float32")
    assert rate == sr and len(y) == int(2.5 * sr) and np.allclose(y, x[: len(y)])
    # 已经比 keep_s 短：不截，但长度字段写对
    assert not truncate_wav(p, 100)
    assert len(sf.read(p)[0]) == int(2.5 * sr)
    # 16 位
    q = tmp_path / "mic.wav"
    _write_wav(q, x, sr, float32=False)
    assert truncate_wav(q, 1.0) and len(sf.read(q)[0]) == sr
    # 不是 wav：不动
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"hello")
    assert not truncate_wav(bad, 1.0) and bad.read_bytes() == b"hello"
    assert not truncate_wav(tmp_path / "missing.wav", 1.0)


def test_levels(tmp_path):
    from tingo.record import max_level, track_level

    p = tmp_path / "mic.wav"
    _write_wav(p, np.concatenate([_tone(1.0, sr=48000), _silence(6.0, sr=48000)]), 48000)
    assert max_level(p) > 0.8
    assert track_level(p) == 0.0 and track_level(p, window_s=5.0) == 0.0  # 最近 5 秒没声音
    assert track_level(p, window_s=6.5) > 0.04
    quiet = tmp_path / "quiet.wav"
    _write_wav(quiet, _tone(1.0, amp=0.0005, sr=48000), 48000)
    assert max_level(quiet) < 0.01
    empty = tmp_path / "empty.wav"
    _write_wav(empty, _silence(0), 48000)
    assert max_level(empty) == 0.0
    assert max_level(tmp_path / "missing.wav") is None


def test_auto_stop_silence_and_app_quit(monkeypatch):
    from tingo import record
    from tingo.record import AutoStop

    a = AutoStop(10, True, set(), started=1000.0)
    assert not a.watch_apps  # 开始时没有会议软件在运行：不看会议软件
    assert a.observe(1005, False, set()) is None and a.silent_s(1005) == 5 and a.auto_stop_s(1005) == 595
    assert a.observe(1300, True) is None and a.silent_s(1302.9) == 2
    assert a.observe(1899, False) is None
    assert a.observe(1900, False) == AutoStop.SILENCE
    assert a.keep_s() == 330.0  # 最后有声（开始后 300 秒）+ 30 秒

    b = AutoStop(0, True, {"zoom.us"}, started=0.0)
    assert b.auto_stop_s(10) is None and b.silent_s(10) == 10
    assert b.observe(99999, False, {"zoom.us"}) is None  # 0 = 不因为没人说话结束
    assert b.observe(100000, False, None) is None  # 这次没查会议软件
    assert b.observe(100005, False, set()) == AutoStop.APP_QUIT
    c = AutoStop(10, False, {"zoom.us"}, started=0.0)
    assert c.observe(5, True, set()) is None

    monkeypatch.setattr(record, "SECONDS_PER_MIN", 1.0)
    assert AutoStop(2, False, set(), 0.0).observe(2.0, False) == AutoStop.SILENCE


def test_running_meeting_apps(monkeypatch):
    from tingo import record

    ps = (
        "/sbin/launchd\n/Applications/zoom.us.app/Contents/MacOS/zoom.us\n"
        "/Applications/腾讯会议.app/Contents/MacOS/腾讯会议\n/Applications/Microsoft Teams.app/Contents/MacOS/MSTeams\n"
        "/Applications/Slack.app/Contents/Frameworks/Slack Helper.app/Contents/MacOS/Slack Helper\n"
        "/Applications/LARK.app/Contents/MacOS/LARK\n  \n"
    )

    def fake_run(cmd, **kw):
        assert cmd == ["ps", "-axo", "comm="]
        return subprocess.CompletedProcess(cmd, 0, stdout=ps)

    monkeypatch.setattr(record.subprocess, "run", fake_run)
    assert record.running_meeting_apps() == {"zoom.us", "腾讯会议", "msteams", "lark"}

    def broken(cmd, **kw):
        raise OSError("no ps")

    monkeypatch.setattr(record.subprocess, "run", broken)
    assert record.running_meeting_apps() == set()


def test_probe_devices_statuses(monkeypatch):
    from tingo import record

    def capture(out, seconds, system):
        _write_wav(out / "mic.wav", _tone(0.5, sr=48000), 48000)
        if system:
            _write_wav(out / "system.wav", _silence(0.5, sr=48000), 48000)

    monkeypatch.setattr(record, "_capture_once", capture)
    monkeypatch.setattr(record, "system_audio_supported", lambda: True)
    assert record.probe_devices(1) == {"system": "silent", "mic": "ok", "message": ""}
    monkeypatch.setattr(record, "system_audio_supported", lambda: False)
    assert record.probe_devices(1) == {"system": "unsupported", "mic": "ok", "message": ""}

    # 没有录屏权限：系统声音 denied，麦克风单独再试
    calls = []

    def no_screen(out, seconds, system):
        calls.append(system)
        if system:
            return "没有录屏与系统录音权限：系统设置 → 隐私与安全性 → 屏幕与系统录音"
        return capture(out, seconds, system)

    monkeypatch.setattr(record, "_capture_once", no_screen)
    monkeypatch.setattr(record, "system_audio_supported", lambda: True)
    r = record.probe_devices(1)
    assert (r["system"], r["mic"], calls) == ("denied", "ok", [True, False]) and "权限" in r["message"]

    monkeypatch.setattr(record, "_capture_once", lambda out, seconds, system: "找不到麦克风")
    assert record.probe_devices(1) == {"system": "error", "mic": "error", "message": "找不到麦克风"}
    # 录音组件没写出音轨
    monkeypatch.setattr(record, "_capture_once", lambda out, seconds, system: None)
    assert record.probe_devices(1)["mic"] == "error"
