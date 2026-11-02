"""可行性验证：sherpa-onnx 各中文模型 vs 现在的 FunASR（PyTorch）。

每个引擎在单独的进程里跑，才能分别量内存：
    uv run --with sherpa-onnx python spike/sherpa/bench.py run <引擎> <wav> [热词...]
输出一行 JSON：识别文本、耗时、音频时长、模型加载耗时、峰值内存。

    uv run --with sherpa-onnx python spike/sherpa/bench.py all   # 跑全套对比，打印表格
"""

from __future__ import annotations

import json
import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

MODELS = Path(os.environ.get("SHERPA_MODELS", Path.home() / ".cache" / "jiyao-dev" / "sherpa"))
SR = 16000
THREADS = 4


def read16k(path: str) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


# ---------- sherpa-onnx ----------


def _vad_segments(audio: np.ndarray) -> list[np.ndarray]:
    import sherpa_onnx as so

    cfg = so.VadModelConfig()
    cfg.silero_vad.model = str(MODELS / "silero_vad.onnx")
    cfg.silero_vad.min_silence_duration = 0.4
    cfg.silero_vad.max_speech_duration = 20
    cfg.sample_rate = SR
    vad = so.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
    window = cfg.silero_vad.window_size
    segs = []
    for i in range(0, len(audio), window):
        vad.accept_waveform(audio[i : i + window])
        while not vad.empty():
            segs.append(np.array(vad.front.samples, dtype=np.float32))
            vad.pop()
    vad.flush()
    while not vad.empty():
        segs.append(np.array(vad.front.samples, dtype=np.float32))
        vad.pop()
    return segs


def _sherpa_recognizer(engine: str, hotwords: list[str]):
    import sherpa_onnx as so

    R = so.OfflineRecognizer
    if engine == "paraformer":
        d = MODELS / "sherpa-onnx-paraformer-zh-int8-2025-10-07"
        return R.from_paraformer(
            paraformer=str(d / "model.int8.onnx"), tokens=str(d / "tokens.txt"), num_threads=THREADS
        )
    if engine == "sherpa-funasr":
        # FunASR 官方 Paraformer-large ONNX，补了 sherpa 元数据（见 to_sherpa.py）
        d = MODELS / "funasr-paraformer-large-int8"
        return R.from_paraformer(
            paraformer=str(d / "model.int8.onnx"), tokens=str(d / "tokens.txt"), num_threads=THREADS
        )
    if engine == "sensevoice":
        d = MODELS / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09"
        return R.from_sense_voice(
            model=str(d / "model.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=THREADS,
            language="zh",
            use_itn=True,
        )
    if engine == "funasr-nano":
        d = MODELS / "sherpa-onnx-funasr-nano-int8-2025-12-30"
        files = {p.name: str(p) for p in d.rglob("*") if p.is_file()}
        pick = lambda key: next(v for k, v in files.items() if key in k and k.endswith(".onnx"))
        tok = next(v for k, v in files.items() if "tokenizer" in k or k == "Qwen3-0.6B")
        return R.from_funasr_nano(
            encoder_adaptor=pick("encoder_adaptor"),
            llm=pick("llm"),
            embedding=pick("embedding"),
            tokenizer=str(Path(tok).parent) if Path(tok).is_file() else tok,
            num_threads=THREADS,
            hotwords=",".join(hotwords),
        )
    if engine == "qwen3-asr":
        d = MODELS / "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"
        files = {p.name: str(p) for p in d.rglob("*") if p.is_file()}
        pick = lambda key: next(v for k, v in files.items() if key in k and k.endswith(".onnx"))
        tok_dir = next(str(p.parent) for p in d.rglob("*") if p.name in ("tokenizer.json", "vocab.json"))
        return R.from_qwen3_asr(
            conv_frontend=pick("conv_frontend"),
            encoder=pick("encoder"),
            decoder=pick("decoder"),
            tokenizer=tok_dir,
            num_threads=THREADS,
            hotwords=",".join(hotwords),
        )
    raise ValueError(engine)


def run_sherpa(engine: str, audio: np.ndarray, hotwords: list[str]) -> tuple[str, float]:
    import sherpa_onnx as so

    t = time.time()
    rec = _sherpa_recognizer(engine, hotwords)
    pd = MODELS / "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8"
    punct = so.OfflinePunctuation(
        so.OfflinePunctuationConfig(model=so.OfflinePunctuationModelConfig(ct_transformer=str(pd / "model.int8.onnx")))
    )
    load = time.time() - t
    texts = []
    for seg in _vad_segments(audio):
        s = rec.create_stream()
        s.accept_waveform(SR, seg)
        rec.decode_stream(s)
        texts.append(s.result.text.strip())
    text = "".join(texts)
    if engine in ("paraformer", "sherpa-funasr"):  # 其他几个模型自带标点
        text = punct.add_punctuation(text)
    return text, load


# ---------- FunASR 官方 ONNX（C++ 运行时用的同一套模型） ----------

FUN_ONNX = Path.home() / ".cache" / "jiyao-dev" / "funasr-onnx" / "models"


def _fsmn_segments(audio: np.ndarray) -> list[np.ndarray]:
    """FunASR 自己的 VAD（FSMN）切段：和 PyTorch 版 FunASR 的切点一致。"""
    import funasr_onnx

    d = FUN_ONNX / "iic--speech_fsmn_vad_zh-cn-16k-common-onnx/snapshots/master"
    vad = funasr_onnx.Fsmn_vad(str(d), quantize=True)
    spans = vad(audio)[0]
    return [audio[int(a * SR / 1000) : int(b * SR / 1000)] for a, b in spans]


def run_fun_onnx(engine: str, audio: np.ndarray, hotwords: list[str]) -> tuple[str, float]:
    import funasr_onnx
    import sherpa_onnx as so

    t = time.time()
    segmenter = _fsmn_segments if engine.endswith("-fsmn") else _vad_segments
    engine = engine.removesuffix("-fsmn")
    if engine.startswith("seaco-onnx"):
        # 自己从现在用的 SeACo-Paraformer 导出的 ONNX（-q 是 int8 量化版），带热词
        d = FUN_ONNX.parent / "seaco"
        m = funasr_onnx.SeacoParaformer(
            str(d), batch_size=1, quantize=engine.endswith("-q"), intra_op_num_threads=THREADS
        )
        call = lambda x: m(x, hotwords=" ".join(hotwords))
    elif engine == "fun-onnx":
        d = FUN_ONNX / "damo--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx/snapshots/master"
        m = funasr_onnx.Paraformer(str(d), batch_size=1, quantize=True, intra_op_num_threads=THREADS)
        call = lambda x: m(x)
    else:
        # 官方发布的热词编码器叫 model_eb.onnx，funasr_onnx 量化模式找 model_eb_quant.onnx，需要建个软链接
        d = (
            FUN_ONNX
            / "iic--speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx/snapshots/master"
        )
        m = funasr_onnx.ContextualParaformer(str(d), batch_size=1, quantize=True, intra_op_num_threads=THREADS)
        call = lambda x: m(x, hotwords=" ".join(hotwords))
    pd = MODELS / "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8"
    punct = so.OfflinePunctuation(
        so.OfflinePunctuationConfig(model=so.OfflinePunctuationModelConfig(ct_transformer=str(pd / "model.int8.onnx")))
    )
    load = time.time() - t
    texts = []
    for seg in segmenter(audio):
        r = call(seg)
        if r:
            texts.append(r[0]["preds"][0])
    return punct.add_punctuation("".join(texts)), load


# ---------- FunASR（现在用的） ----------


def run_funasr(audio: np.ndarray, hotwords: list[str]) -> tuple[str, float]:
    from funasr import AutoModel

    t = time.time()
    m = AutoModel(
        model="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        disable_update=True,
        disable_pbar=True,
        log_level="ERROR",
    )
    load = time.time() - t
    kw = {"hotword": " ".join(hotwords)} if hotwords else {}
    r = m.generate(input=audio, batch_size_s=300, **kw)
    return r[0]["text"], load


# ---------- 评测 ----------


def norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s).lower()


def cer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hc in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rc != hc))
    return d[len(h)] / max(1, len(r))


def run_one(engine: str, wav: str, hotwords: list[str]) -> dict:
    audio = read16k(wav)
    t = time.time()
    if engine == "funasr":
        text, load = run_funasr(audio, hotwords)
    elif engine.startswith(("fun-onnx", "seaco-onnx")):
        text, load = run_fun_onnx(engine, audio, hotwords)
    else:
        text, load = run_sherpa(engine, audio, hotwords)
    total = time.time() - t
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576  # macOS 上单位是字节
    return {
        "engine": engine,
        "text": text,
        "audio_s": len(audio) / SR,
        "load_s": load,
        "asr_s": total - load,
        "rss_mb": rss_mb,
    }


if __name__ == "__main__":
    if sys.argv[1] == "run":
        print(json.dumps(run_one(sys.argv[2], sys.argv[3], sys.argv[4:]), ensure_ascii=False))


# 测试素材的原文（macOS 语音合成生成，所以有标准答案）
REF_MEETING = (
    "大家好，今天我们开会主要讨论两件事，第一是下个月的产品发布，第二是客服系统的迁移。"
    "发布这边，前端基本做完了，但是支付模块还有两个问题没修，我估计要到十一月十五号才能提测。"
    "那十五号提测的话，二十号上线来得及吗？"
    "有点紧。我建议把上线推迟到二十五号，留出一周给测试。"
    "好，那就定二十五号上线。小王负责支付模块，十五号之前修完。客服迁移的事情谁来跟？"
    "客服迁移我来跟吧，下周三之前我出一个方案，主要风险是历史工单的数据量比较大。"
    "行，那今天就先这样，散会。"
)
REF_HOT = "青鸾计划下周启动，钟离岚负责数据平台，我们用Kafka和Flink搭实时数仓，部署在鲲鹏服务器上。"
HOTWORDS = ["青鸾计划", "钟离岚", "Kafka", "Flink", "鲲鹏"]
ENGINES = [
    "funasr",
    "seaco-onnx-fsmn",
    "seaco-onnx-q-fsmn",
    "fun-onnx",
    "fun-onnx-hot",
    "paraformer",
    "sensevoice",
    "funasr-nano",
    "qwen3-asr",
]
HOT_ENGINES = {"funasr", "seaco-onnx-fsmn", "seaco-onnx-q-fsmn", "fun-onnx-hot", "funasr-nano", "qwen3-asr"}


def _cmd(engine: str) -> list[str]:
    # 现在的 FunASR 用项目环境；其余引擎放在不装 PyTorch 的干净环境里跑，内存才反映打包后的真实情况
    if engine == "funasr":
        return ["uv", "run", "python"]
    return ["uv", "run", "--no-project", "--with", "sherpa-onnx", "--with", "funasr-onnx", "--with", "numpy", "python"]


def sub(engine: str, wav: str, hotwords: list[str]) -> dict:
    cmd = [*_cmd(engine), __file__, "run", engine, wav, *hotwords]
    p = subprocess.run(cmd, check=False, capture_output=True, text=True)
    line = next((ln for ln in reversed(p.stdout.splitlines()) if ln.startswith("{")), None)
    if not line:
        return {"engine": engine, "error": (p.stderr or p.stdout)[-400:]}
    return json.loads(line)


def main_all(data: Path) -> None:
    meeting, hot, clip = str(data / "meeting_zh.wav"), str(data / "hotwords.wav"), str(data / "clip3.wav")
    print("| 引擎 | 会议错字率 | 热词句错字率（不给热词 → 给热词） | 3 分钟音频耗时 | 实时倍数 | 加载 | 峰值内存 |")
    print("|---|---|---|---|---|---|---|")
    samples = {}
    for e in ENGINES:
        m = sub(e, meeting, [])
        h0 = sub(e, hot, [])
        h1 = sub(e, hot, HOTWORDS) if e in HOT_ENGINES else None
        c = sub(e, clip, [])
        if "error" in m or "error" in c:
            print(f"| {e} | 失败：{(m.get('error') or c.get('error'))[-120:]!r} |")
            continue
        hot_cell = f"{cer(REF_HOT, h0['text']):.1%}" + (
            f" → {cer(REF_HOT, h1['text']):.1%}" if h1 else "（不支持热词）"
        )
        print(
            f"| {e} | {cer(REF_MEETING, m['text']):.1%} | {hot_cell} | {c['asr_s']:.1f}s | "
            f"{c['audio_s'] / c['asr_s']:.0f}x | {c['load_s']:.1f}s | {c['rss_mb']:.0f} MB |",
            flush=True,
        )
        samples[e] = (h0["text"], h1["text"] if h1 else None)
    print("\n热词句识别结果：")
    print(f"  原文：{REF_HOT}")
    for e, (a, b) in samples.items():
        print(f"  {e}：{a}")
        if b:
            print(f"  {e} + 热词：{b}")


if __name__ == "__main__" and sys.argv[1] == "all":
    main_all(Path(sys.argv[2]))
