"""边录边转：录音进行中就分段识别，结束后只剩最后一小段和一次说话人聚类。

整段离线转写在长录音上会越来越慢（10 分钟录音约 3 分钟，1 小时 20 分钟以上）。
这里每攒够 10 多秒、在停顿处切一刀，马上识别 + 标点，同时截留每段的声纹；
按下结束后处理剩下的几秒，再用全部声纹做一次聚类，说话人编号在整场会议里保持一致。
聚类、分窗和后处理都直接用 FunASR 自己的实现，结果与离线转写同一套算法。
"""

from __future__ import annotations

import contextlib
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import asr
from .record import duck_mix
from .transcript import Sentence, Transcript

SR = 16000
MIN_CHUNK_S = 10.0  # 攒够这么长才开始找切点；越短，按结束后剩下要处理的越少，实时文字也出得越快
MAX_CHUNK_S = 30.0  # 一直没有停顿时，到这个长度强制切
SEARCH_S = 6.0  # 在片段末尾这么长的范围里找最安静的地方下刀
QUIET_WIN_S = 0.4
MIX_MARGIN_S = 1.0  # 混音时往前多读一点，让"让路"的保持时间跨片段连续
LOOKAHEAD_S = 0.2  # 文件末尾这么长先不处理，留给"提前关麦克风"看后面的数据


class GrowingWav:
    """读取正在被写入的 wav：录音组件持续追加数据，文件头里的长度字段不可信，按文件大小算。"""

    def __init__(self, path: Path):
        self.path = path
        self.rate = 0
        self.channels = 0
        self.width = 0
        self.tag = 0
        self.data_start = 0

    def _header(self) -> bool:
        if self.rate:
            return True
        try:
            with self.path.open("rb") as f:
                head = f.read(4096)
        except OSError:
            return False
        fmt_at, data_at = head.find(b"fmt "), head.find(b"data")
        if fmt_at < 0 or data_at < 0:
            return False
        tag, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", head, fmt_at + 8)
        if tag == 0xFFFE:
            tag = struct.unpack_from("<H", head, fmt_at + 8 + 24)[0]
        if (tag, bits) not in ((3, 32), (1, 16)):
            raise RuntimeError(f"不支持的录音格式：tag={tag} bits={bits}")
        self.tag, self.channels, self.rate, self.width = tag, channels, rate, bits // 8
        self.data_start = data_at + 8
        return True

    def seconds(self) -> float:
        if not self._header():
            return 0.0
        size = self.path.stat().st_size - self.data_start
        return max(0, size // (self.width * self.channels)) / self.rate

    def read(self, start_s: float, end_s: float) -> np.ndarray:
        """[start_s, end_s) 这段，单声道 16 kHz float32。"""
        if not self._header() or end_s <= start_s:
            return np.zeros(0, dtype=np.float32)
        frame = self.width * self.channels
        a, b = int(start_s * self.rate), int(end_s * self.rate)
        with self.path.open("rb") as f:
            f.seek(self.data_start + a * frame)
            raw = f.read((b - a) * frame)
        raw = raw[: len(raw) - len(raw) % frame]
        if self.tag == 3:
            x = np.frombuffer(raw, dtype="<f4")
        else:
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
        if self.channels > 1:
            x = x.reshape(-1, self.channels).mean(axis=1)
        return _resample(x.astype(np.float32), self.rate)


def _resample(x: np.ndarray, rate: int) -> np.ndarray:
    if rate == SR or x.size == 0:
        return x
    import torch
    import torchaudio.functional as F

    return F.resample(torch.from_numpy(x.copy()), rate, SR).numpy()


def find_cut(audio: np.ndarray, force: bool) -> int | None:
    """在片段末尾 SEARCH_S 秒里找最安静的 QUIET_WIN_S 窗口中点作为切点。

    没有足够安静的地方（一直有人在说）且未到强制长度时返回 None，等更多数据。
    """
    n = len(audio)
    lo = max(int(MIN_CHUNK_S * SR) - int(SEARCH_S * SR), n - int(SEARCH_S * SR))
    win = int(QUIET_WIN_S * SR)
    hop = win // 4
    if n - lo < win:
        return n if force else None
    starts = np.arange(lo, n - win, hop)
    energy = np.array([np.mean(audio[s : s + win] ** 2) for s in starts])
    best = int(np.argmin(energy))
    quiet_db = 10 * np.log10(energy[best] + 1e-12)
    loud_db = 10 * np.log10(np.percentile(energy, 90) + 1e-12)
    if force or quiet_db < loud_db - 15:
        return int(starts[best] + win // 2)
    return None


@dataclass
class _Chunk:
    sentences: list[dict]
    segments: list[tuple[float, float]]  # 声纹小窗的绝对起止秒
    embeddings: np.ndarray


@contextlib.contextmanager
def _capture_embeddings(model):
    """调用 FunASR 时截留声纹：把聚类换成"只记录"，后处理顺便记下小窗时间。"""
    import funasr.auto.auto_model as am

    got: dict = {"emb": [], "seg": []}
    real_cb, real_post = model.cb_model, am.postprocess

    def record_only(emb, oracle_num=None):
        got["emb"].append(emb.cpu().numpy())
        return np.zeros(emb.shape[0], dtype=int)

    def spy_post(segments, vad_segments, labels, embeddings, **kw):
        got["seg"].extend((float(s[0]), float(s[1])) for s in segments)
        return real_post(segments, vad_segments, labels, embeddings, **kw)

    model.cb_model, am.postprocess = record_only, spy_post
    try:
        yield got
    finally:
        model.cb_model, am.postprocess = real_cb, real_post


CLUSTER_LIMIT = 1500  # FunASR 在声纹数 ≥ 2048 时改用 UMAP+HDBSCAN：慢，而且容易把一个人拆成几个


def cluster(model, emb: np.ndarray) -> np.ndarray:
    """给每个声纹小窗分配说话人编号。

    声纹多（会议长）时均匀抽 CLUSTER_LIMIT 个做谱聚类，其余按余弦相似度归到最近的说话人。
    76 分钟会议约 6000 个小窗：整体聚类约 50 秒，抽样后几秒。
    """
    import torch

    n = len(emb)
    if n <= CLUSTER_LIMIT:
        return np.asarray(model.cb_model(torch.from_numpy(emb), oracle_num=None))
    idx = np.unique(np.linspace(0, n - 1, CLUSTER_LIMIT).round().astype(int))
    sub = np.asarray(model.cb_model(torch.from_numpy(emb[idx]), oracle_num=None))
    unit = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    ids = np.unique(sub)
    cent = np.stack([unit[idx][sub == k].mean(axis=0) for k in ids])
    cent /= np.linalg.norm(cent, axis=1, keepdims=True) + 1e-9
    return ids[np.argmax(unit @ cent.T, axis=1)]


class ChunkedTranscriber:
    """把音频按停顿切成 10～30 秒的段逐段识别，最后统一聚类。录音中和处理文件共用。"""

    def __init__(self, hotwords: list[str] | None = None, on_sentence: Callable[[str, int], None] | None = None):
        self.hotwords = hotwords or []
        self.on_sentence = on_sentence  # 每识别出一句：(文本, 起始毫秒)
        self.pending = np.zeros(0, dtype=np.float32)
        self.pending_start = 0.0  # pending 第一个样本对应的秒数
        self.chunks: list[_Chunk] = []
        self.lines: list[tuple[int, str]] = []  # 给界面显示的实时文字
        self.timings: dict[str, float] = {}

    def feed(self, audio: np.ndarray) -> None:
        self.pending = np.concatenate([self.pending, audio])

    def next_chunk(self, final: bool) -> tuple[np.ndarray, float] | None:
        """从待处理音频里切出下一段；不够长或找不到合适的切点时返回 None。"""
        if len(self.pending) < (0.3 if final else MIN_CHUNK_S) * SR:
            return None
        # 每段最长 MAX_CHUNK_S：处理落后（比如模型还在加载）时也不会攒出一个巨大的段
        window = self.pending[: int(MAX_CHUNK_S * SR)]
        over = len(self.pending) > len(window)
        if final and not over:
            cut = len(self.pending)
        else:
            cut = find_cut(window, force=final or over or len(window) >= MAX_CHUNK_S * SR)
        if cut is None:
            return None
        chunk, offset = self.pending[:cut], self.pending_start
        self.pending = self.pending[cut:]
        self.pending_start += cut / SR
        return chunk, offset

    def transcribe_chunk(self, audio: np.ndarray, offset_s: float) -> None:
        model = asr._load_model()
        kwargs: dict = {"input": audio, "batch_size_s": 300}
        if self.hotwords:
            kwargs["hotword"] = " ".join(self.hotwords)
        with asr._quiet(), _capture_embeddings(model) as got:
            result = model.generate(**kwargs)
        off_ms = round(offset_s * 1000)
        sentences = [
            {
                "text": s["text"],
                "start": s["start"] + off_ms,
                "end": s["end"] + off_ms,
                "timestamp": [[a + off_ms, b + off_ms] for a, b in s.get("timestamp") or []],
            }
            for s in ((result[0].get("sentence_info") or []) if result else [])
            if s.get("text", "").strip()
        ]
        # 回调放在 _quiet 外面：_quiet 会把整个进程的标准输出吞掉
        for s in sentences:
            self.lines.append((s["start"], s["text"]))
            if self.on_sentence:
                self.on_sentence(s["text"], s["start"])
        emb = np.concatenate(got["emb"]) if got["emb"] else np.zeros((0, 192), dtype=np.float32)
        segs = [(a + offset_s, b + offset_s) for a, b in got["seg"]]
        self.chunks.append(_Chunk(sentences, segs, emb))

    def assemble(self, source: Path) -> Transcript:
        from funasr.models.campplus.utils import postprocess

        model = asr._load_model()
        segs = [s for c in self.chunks for s in c.segments]
        embs = [c.embeddings for c in self.chunks if len(c.embeddings)]
        diar: list = []
        t = time.time()
        if segs and embs:
            emb = np.concatenate(embs)
            labels = cluster(model, emb)
            diar = postprocess([[a, b] for a, b in segs], None, labels, emb)
            self.timings["voiceprints"] = len(emb)
        self.timings["cluster_s"] = time.time() - t
        sentences: list[Sentence] = []
        for c in self.chunks:
            for s in c.sentences:
                sentences.extend(asr.split_by_speaker({**s, "spk": 0}, diar))
        duration = round((self.pending_start + len(self.pending) / SR) * 1000)
        return Transcript(source=str(source), duration_ms=duration, sentences=sentences)

    def recent(self, n: int = 6) -> list[dict]:
        return [{"start_ms": t, "text": x} for t, x in self.lines[-n:]]


def transcribe_file(
    path: Path, hotwords: list[str] | None = None, on_progress: Callable[[float], None] | None = None
) -> Transcript:
    """分段转写已有的录音文件：比整段交给 FunASR 快，长录音也不会越转越慢。"""
    from .record import _read16k

    ct = ChunkedTranscriber(hotwords)
    audio = _read16k(path)
    total = len(audio) / SR
    ct.feed(audio)
    while (job := ct.next_chunk(final=False)) or (job := ct.next_chunk(final=True)):
        ct.transcribe_chunk(*job)
        if on_progress and total:
            on_progress(min(1.0, ct.pending_start / total))
    return ct.assemble(path)


class LiveTranscriber(ChunkedTranscriber):
    """录音进行中读取录音组件正在写的音轨，边录边转。"""

    def __init__(
        self,
        out_dir: Path,
        sources: list[str],
        hotwords: list[str] | None = None,
        on_sentence: Callable[[str, int], None] | None = None,
    ):
        super().__init__(hotwords, on_sentence)
        self.out_dir = out_dir
        self.sources = sources  # "system" / "mic"
        self.tracks = {s: GrowingWav(out_dir / f"{s}.wav") for s in sources}
        self.mixed_until = 0.0  # 已混音到第几秒
        self.error: Exception | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            asr._load_model()
            while not self._stop.is_set():
                if not self.step(final=False):
                    self._stop.wait(1.0)
        except Exception as e:  # noqa: BLE001 - 失败后交给调用方走离线转写
            self.error = e

    def step(self, final: bool) -> bool:
        """读新数据、必要时切一段识别。返回这一步是否处理了一段。"""
        with self._lock:
            self._mix_new(final)
            job = self.next_chunk(final)
        if job is None:
            return False
        self.transcribe_chunk(*job)
        return True

    def _mix_new(self, final: bool) -> None:
        avail = min(t.seconds() for t in self.tracks.values())
        commit_end = avail if final else avail - LOOKAHEAD_S
        if commit_end - self.mixed_until < (0.05 if final else 1.0):
            return
        margin = min(MIX_MARGIN_S, self.mixed_until)
        start = self.mixed_until - margin
        parts = {s: t.read(start, avail) for s, t in self.tracks.items()}
        if len(parts) == 2:
            mixed = duck_mix(parts["system"], parts["mic"], SR)
        else:
            mixed = next(iter(parts.values()))
        a = round(margin * SR)
        b = min(len(mixed), round((commit_end - start) * SR))
        if b <= a:
            return
        self.feed(mixed[a:b])
        # 按实际拿到的样本数推进，两路长度差几个样本也不会累积漂移
        self.mixed_until = start + b / SR

    def finish(self, source: Path) -> Transcript | None:
        """录音已停止后调用：处理剩余音频、统一聚类，返回完整转写。失败返回 None。"""
        self._stop.set()
        if self._thread:
            self._thread.join()
        if self.error:
            return None
        try:
            t = time.time()
            n0 = len(self.chunks)
            while self.step(final=True):
                pass
            self.timings["tail_s"] = time.time() - t
            self.timings["tail_chunks"] = len(self.chunks) - n0
            return self.assemble(source)
        except Exception as e:  # noqa: BLE001
            self.error = e
            return None
