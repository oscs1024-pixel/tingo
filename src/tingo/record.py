"""录音：macOS 用 ScreenCaptureKit 录系统声音 + 麦克风，其他情况退回 ffmpeg 只录麦克风。"""

from __future__ import annotations

import platform
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

SWIFT_SRC = Path(__file__).with_name("recorder.swift")
BIN_DIR = Path.home() / ".cache" / "tingo"
RECORDER_BIN = BIN_DIR / "tingo-recorder"


def html_to_pdf(html: str, out: Path) -> None:
    """HTML → PDF：交给录音组件用系统 WebKit 排版（A4 分页）。"""
    import tempfile

    binary = ensure_recorder()
    with tempfile.TemporaryDirectory(prefix="tingo-pdf-") as tmp:
        src = Path(tmp) / "minutes.html"
        src.write_text(html, encoding="utf-8")
        res = subprocess.run(
            [str(binary), "pdf", str(src), str(out)], check=False, capture_output=True, text=True, timeout=60
        )
    if res.returncode != 0 or not out.exists():
        err = next((line[7:] for line in res.stdout.splitlines() if line.startswith("ERROR: ")), "导出 PDF 失败")
        raise RuntimeError(err)


def system_audio_supported() -> bool:
    if platform.system() != "Darwin":
        return False
    major = int(platform.mac_ver()[0].split(".")[0] or 0)
    return major >= 15 and shutil.which("swiftc") is not None


def ensure_recorder() -> Path:
    """首次使用时编译 Swift 录音组件，源码更新后自动重编。"""
    if RECORDER_BIN.exists() and RECORDER_BIN.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return RECORDER_BIN
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["swiftc", "-O", str(SWIFT_SRC), "-o", str(RECORDER_BIN)], check=False, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"编译录音组件失败（需要 Xcode Command Line Tools）：\n{proc.stderr}")
    return RECORDER_BIN


class Recording:
    """一次录音。start() 开始，stop() 结束并合成 16 kHz 单声道音频。"""

    def __init__(self, out_dir: Path, system_audio: bool = True, mic: bool = True):
        self.out_dir = out_dir
        self.system_audio = system_audio and system_audio_supported()
        self.mic = mic
        self.proc: subprocess.Popen | None = None
        self.started_at = 0.0

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if self.system_audio:
            cmd = [str(ensure_recorder()), str(self.out_dir)] + ([] if self.mic else ["--no-mic"])
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, start_new_session=True)
            line = self.proc.stdout.readline().strip()
            if line != "READY":
                self.proc.wait()
                raise RuntimeError(line.removeprefix("ERROR: ") or "录音组件意外退出")
        else:
            self.proc = subprocess.Popen(
                [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    *_mic_input(),
                    "-ac",
                    "1",
                    "-ar",
                    "48000",
                    str(self.out_dir / "mic.wav"),
                ],
                stdin=subprocess.PIPE,
                start_new_session=True,
            )
        self.started_at = time.time()

    def elapsed(self) -> float:
        return time.time() - self.started_at if self.started_at else 0.0

    @property
    def sources(self) -> list[str]:
        return [n for n, on in (("system", self.system_audio), ("mic", self.mic)) if on]

    def stop(self) -> Path:
        """结束录音，返回合成后的音频路径。"""
        self.stop_capture()
        return mixdown(self.out_dir)

    def stop_capture(self) -> None:
        """只停止录音进程，原始音轨留着（边录边转还要读最后几秒）。"""
        if self.proc and self.proc.poll() is None:
            if self.system_audio:
                self.proc.send_signal(signal.SIGINT)
            else:
                self.proc.communicate(b"q", timeout=10)  # 让 ffmpeg 正常写完文件头
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _mic_input() -> list[str]:
    system = platform.system()
    if system == "Darwin":
        return ["-f", "avfoundation", "-i", ":default"]
    if system == "Linux":
        return ["-f", "pulse", "-i", "default"]
    raise RuntimeError("当前系统暂不支持直接录音，请先用会议软件录制，再用 `tingo run <音频文件>` 处理")


def mixdown(out_dir: Path) -> Path:
    """把系统声音和麦克风合成一条 16 kHz 单声道 m4a，原始轨道删掉以省空间。"""
    system, mic = out_dir / "system.wav", out_dir / "mic.wav"
    tracks = [p for p in (system, mic) if p.exists() and p.stat().st_size > 1024]
    if not tracks:
        raise RuntimeError("没有录到任何声音")
    dst = out_dir / "audio.m4a"
    if len(tracks) == 2:
        import soundfile as sf

        mixed = out_dir / "mixed.wav"
        sf.write(mixed, duck_mix(_read16k(system), _read16k(mic), SAMPLE_RATE), SAMPLE_RATE)
        tracks.append(mixed)
        src = mixed
    else:
        src = tracks[0]
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(dst),
        ],
        check=True,
        start_new_session=True,
    )
    for t in tracks:
        t.unlink()
    return dst


SAMPLE_RATE = 16000


def _read16k(path: Path):
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True,
        check=True,
        start_new_session=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def duck_mix(
    system, mic, sr: int, threshold_db: float = -50.0, hold_ms: int = 700, pre_ms: int = 120, fade_ms: int = 10
):
    """系统声音里有人说话时只用系统声音，其余时间用麦克风。

    外放开会时，麦克风会把扬声器里对方的声音再录一遍，直接混音会叠成回声，识别率明显下降。
    系统声音一有信号就关麦克风，并多保持 hold_ms 盖住回声尾巴。实测 ScreenCaptureKit 的系统声音轨
    比麦克风里的回声还晚 40～55 ms，所以再提前 pre_ms 关麦克风，免得每句话开头漏进回声。
    代价是双方同时说话时，本地这一侧会被盖掉。
    """
    import numpy as np

    n = max(len(system), len(mic))
    system = np.pad(system, (0, n - len(system)))
    mic = np.pad(mic, (0, n - len(mic)))

    frame = sr // 50  # 20 ms
    frames = -(-n // frame)
    padded = np.pad(system, (0, frames * frame - n)).reshape(frames, frame)
    rms_db = 10 * np.log10(np.mean(padded**2, axis=1) + 1e-12)
    active = rms_db > threshold_db

    hold, pre = max(1, hold_ms // 20), pre_ms // 20
    # counts[i] = 第 i 帧前 hold 帧到后 pre 帧之间有几帧系统声音在响
    counts = np.convolve(active.astype(np.int32), np.ones(hold + pre + 1, dtype=np.int32))[pre : pre + frames]
    gain = np.repeat((counts == 0).astype(np.float32), frame)[:n]

    fade = max(1, sr * fade_ms // 1000)
    gain = np.convolve(gain, np.ones(fade, dtype=np.float32) / fade, mode="same")
    return (system + mic * gain).astype(np.float32)


def record_until_interrupt(
    out_dir: Path,
    system_audio: bool,
    mic: bool,
    on_tick: Callable[[float], None],
    on_stopping: Callable[[], None] | None = None,
    live_factory: Callable[[list[str]], object] | None = None,
) -> tuple[Path, str | None, object | None]:
    """录到 Ctrl+C 为止。录音中途出错时也保住已录部分。

    live_factory 给了就边录边转：用实际录的音轨列表创建 LiveTranscriber 并启动。
    返回 (音频, 错误信息, LiveTranscriber 或 None)；调用方负责 live.finish()，
    原始音轨在那之前不能删，所以这里先不合成，合成交给 finish_recording。
    """
    rec = Recording(out_dir, system_audio=system_audio, mic=mic)
    stop = False

    def request_stop(*_):
        nonlocal stop
        stop = True

    # 显式接管：后台启动时 SIGINT 可能被继承为"忽略"，被其他程序 kill 时也要正常收尾。
    # 保存期间一直接管，多按几次 Ctrl+C 也不会把录音弄丢。
    old_int = signal.signal(signal.SIGINT, request_stop)
    old_term = signal.signal(signal.SIGTERM, request_stop)
    live = None
    try:
        rec.start()
        if live_factory:
            live = live_factory(rec.sources)
            live.start()
        while not stop and rec.proc.poll() is None:
            on_tick(rec.elapsed())
            time.sleep(0.2)
        if on_stopping:
            on_stopping()
        error = None
        if rec.proc.poll() not in (None, 0):
            tail = rec.proc.stdout.read().strip() if rec.proc.stdout else ""
            error = tail.removeprefix("ERROR: ") or "录音组件意外退出"
        rec.stop_capture()
        return out_dir, error, live
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


def track_level(path: Path, window_s: float = 0.15) -> float:
    """正在写入的 wav 文件末尾一小段的音量，0～1。图形界面录音时画电平用。"""
    import struct

    import numpy as np

    try:
        with path.open("rb") as f:
            head = f.read(4096)
            fmt_at, data_at = head.find(b"fmt "), head.find(b"data")
            if fmt_at < 0 or data_at < 0:
                return 0.0
            tag, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", head, fmt_at + 8)
            if tag == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE，真实格式在子格式 GUID 的前两个字节
                tag = struct.unpack_from("<H", head, fmt_at + 8 + 24)[0]
            width = bits // 8
            frame = width * channels
            data_start = data_at + 8
            size = f.seek(0, 2)
            n = int(rate * window_s) * frame
            start = max(data_start, size - n)
            start -= (start - data_start) % frame
            f.seek(start)
            raw = f.read(size - start)
    except (OSError, struct.error):
        return 0.0
    if not raw or width not in (2, 4):
        return 0.0
    raw = raw[: len(raw) - len(raw) % frame]
    if tag == 3 and width == 4:
        x = np.frombuffer(raw, dtype="<f4")
    elif tag == 1 and width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    else:
        return 0.0
    if x.size == 0:
        return 0.0
    db = 10 * np.log10(float(np.mean(x * x)) + 1e-12)
    return float(min(1.0, max(0.0, (db + 60) / 50)))  # -60 dB → 0，-10 dB → 1
