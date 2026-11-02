"""FunASR 中文识别 + 说话人区分。

模型从 ModelScope 下载（国内直连），首次运行约 2.1 GB，之后走本地缓存。
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .transcript import Sentence, Transcript

ASR_MODEL = "paraformer-zh"  # SeACo-Paraformer，支持热词
VAD_MODEL = "fsmn-vad"
PUNC_MODEL = "ct-punc"
SPK_MODEL = "cam++"

_model = None


def _load_model():
    global _model
    if _model is None:
        os.environ.setdefault("MODELSCOPE_LOG_LEVEL", str(logging.ERROR))
        with _quiet():
            from funasr import AutoModel

            _model = AutoModel(
                model=ASR_MODEL,
                vad_model=VAD_MODEL,
                punc_model=PUNC_MODEL,
                spk_model=SPK_MODEL,
                disable_update=True,
                disable_pbar=True,
                log_level="ERROR",
            )
    return _model


@contextlib.contextmanager
def _quiet():
    """FunASR 会往 stdout/stderr 和 logging 打大量进度和告警，统一吞掉。设 JIYAO_DEBUG=1 可看原始输出。"""
    if os.environ.get("JIYAO_DEBUG"):
        yield
        return
    buf = io.StringIO()
    root = logging.getLogger()
    level = root.level
    logging.disable(logging.WARNING)
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            yield
    finally:
        logging.disable(logging.NOTSET)
        root.setLevel(level)  # FunASR 会把根 logger 调成 INFO，连带 httpx 等库开始刷日志


def probe_duration_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return int(float(out) * 1000) if out else 0


def to_wav16k(src: Path, dst: Path) -> None:
    """任意音视频（腾讯会议/飞书导出的 mp4、m4a 等）转 16 kHz 单声道 wav。"""
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(dst)],
        check=True,
    )


def transcribe(path: Path, hotwords: list[str] | None = None) -> Transcript:
    model = _load_model()
    with tempfile.TemporaryDirectory(prefix="jiyao-") as tmp:
        wav = Path(tmp) / "audio.wav"
        to_wav16k(path, wav)
        duration = probe_duration_ms(wav)
        kwargs = {"input": str(wav), "batch_size_s": 300}
        if hotwords:
            kwargs["hotword"] = " ".join(hotwords)
        with _quiet(), _capture_diarization() as segments:
            result = model.generate(**kwargs)

    sentences: list[Sentence] = []
    for s in (result[0].get("sentence_info") or []) if result else []:
        sentences.extend(split_by_speaker(s, segments))
    return Transcript(source=str(path), duration_ms=duration, sentences=sentences)


@contextlib.contextmanager
def _capture_diarization():
    """截获 FunASR 内部的说话人分段 [(起秒, 止秒, 说话人)]。

    FunASR 先按标点断句、再给整句分配说话人，换人处没有停顿标点时，
    后一个人开头的几个字会被算到前一个人头上。拿到分段后可以按字重新划分。
    """
    import funasr.auto.auto_model as am

    segments: list[tuple[float, float, int]] = []
    original = am.distribute_spk

    def spy(sentence_list, sd_time_list):
        segments.extend(sd_time_list)
        return original(sentence_list, sd_time_list)

    am.distribute_spk = spy
    try:
        yield segments
    finally:
        am.distribute_spk = original


_TOKEN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]|[A-Za-z0-9][A-Za-z0-9'._-]*")
_ENDS = "，。？！；、,.?!;"


def split_by_speaker(sentence: dict, segments: list[tuple[float, float, int]]) -> list[Sentence]:
    """把一个句子在说话人切换处切开。对不上字与时间戳时原样返回整句。"""
    text = sentence.get("text", "").strip()
    if not text:
        return []
    whole = [Sentence(int(sentence.get("spk", 0)), int(sentence["start"]), int(sentence["end"]), text)]
    stamps = sentence.get("timestamp") or []
    tokens = list(_TOKEN.finditer(text))
    if not segments or len(tokens) != len(stamps) or len(tokens) < 2:
        return whole

    labels = [_speaker_at((a + b) / 2000, segments) for a, b in stamps]
    if len(set(labels)) == 1:
        whole[0].speaker = labels[0]
        return whole

    pieces: list[Sentence] = []
    run_start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[run_start]:
            lo = 0 if run_start == 0 else tokens[run_start].start()
            hi = len(text) if i == len(labels) else tokens[i].start()
            piece = text[lo:hi].strip()
            if piece and piece[-1] not in _ENDS:
                piece += "。"
            pieces.append(Sentence(labels[run_start], int(stamps[run_start][0]), int(stamps[i - 1][1]), piece))
            run_start = i
    return pieces


def _speaker_at(t: float, segments: list[tuple[float, float, int]]) -> int:
    """时间点 t（秒）所在分段的说话人；落在分段间隙时取最近的分段。"""
    best, best_dist = segments[0][2], float("inf")
    for start, end, spk in segments:
        if start <= t <= end:
            return int(spk)
        dist = min(abs(t - start), abs(t - end))
        if dist < best_dist:
            best, best_dist = int(spk), dist
    return best
