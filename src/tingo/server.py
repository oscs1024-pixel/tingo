"""图形界面的本地服务：只监听 127.0.0.1，前端在 web/ 目录。

录音和后台任务（转写、生成纪要）可以同时进行：录音同时只有一场，同一场会议同时只有一个任务，
前端轮询 /api/jobs 拿所有任务的进度。
"""

from __future__ import annotations

import itertools
import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, meetings
from .log import get as get_logger
from .meetings import AUDIO, MINUTES, TRANSCRIPT_JSON
from .summarize import is_ollama
from .transcript import Transcript, fmt_ts

WEB_DIR = Path(__file__).with_name("web")

# 本机常见的 OpenAI 兼容中转。recommended 是实测过、又快又好的纪要模型（76 分钟会议 51 秒出纪要）
LOCAL_PROVIDERS = [
    {
        "id": "magpie",
        "name": "Magpie",
        "base_url": "http://127.0.0.1:3425/v1",
        "api_key": "magpie",
        "recommended": "deepseek/deepseek-v4-flash",
        "hint": "Magpie 没有在运行：打开 Magpie 应用后再试",
    },
    {
        "id": "ccswitch",
        "name": "CC Switch",
        "base_url": "http://127.0.0.1:15721/v1",
        "api_key": "cc-switch",
        "recommended": "",
        "hint": "CC Switch 的本地代理没有打开：在 CC Switch 设置里开启「本地代理」（默认端口 15721）",
    },
    {
        "id": "ollama",
        "name": "本地 Ollama",
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "recommended": "",
        "hint": "Ollama 没有在运行，或者还没有下载模型",
    },
]


def _list_models(base_url: str, api_key: str) -> list[str] | None:
    """GET /v1/models；连不上返回 None。"""
    import httpx

    try:
        r = httpx.get(
            base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {api_key or 'none'}"}, timeout=2
        )
        if r.status_code != 200:
            return None
        return sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
    except Exception:  # noqa: BLE001
        return None


log = get_logger()


def _launch(args: list[str]) -> None:
    """后台打开文件 / 链接，不等它结束。"""
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _open(target: str, reveal: bool = False) -> None:
    """用系统默认程序打开；reveal=True 时在访达里选中这个文件。"""
    if platform.system() == "Darwin":
        _launch(["open", "-R", target] if reveal else ["open", target])
    elif platform.system() == "Windows":
        os.startfile(str(Path(target).parent) if reveal else target)
    else:
        _launch(["xdg-open", str(Path(target).parent) if reveal else target])


_job_ids = itertools.count(1)


def _next_job_id() -> str:
    return f"j{next(_job_ids)}"


KEEP_FINISHED = 5  # 结束了的任务只留最近几个，给界面显示结果


@dataclass
class Job:
    kind: str  # record | process | summarize | models
    meeting: str
    state: str  # recording | saving | finalizing | loading | transcribing | summarizing | done | error
    started_at: float = field(default_factory=time.time)
    error: str = ""
    minutes: str = ""  # 生成中的纪要，流式累加
    stage: str = ""
    sources: list[str] = field(default_factory=list)
    notice: str = ""  # 完成后给用户的附加提示（比如"已存到 Obsidian"）
    # 任务编号：录音和后台任务可以同时进行，界面按编号区分
    id: str = field(default_factory=_next_job_id)
    thinking: int = 0  # 推理模型已经思考了多少字（还没开始写纪要时显示）
    finished_at: float = 0.0  # 结束时间；结束后用时不再增长

    @property
    def busy(self) -> bool:
        return self.state not in ("done", "error")


class RecordRequest(BaseModel):
    title: str = "会议"
    system: bool = True
    mic: bool = True
    hotwords: list[str] = []


class SpeakersRequest(BaseModel):
    names: list[str]


class MinutesRequest(BaseModel):
    text: str


class ObsidianRequest(BaseModel):
    vault: str
    folder: str
    auto: bool
    transcript: bool


class ConfigRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None  # None 表示不改
    hotwords: list[str] = []
    webhook: str = ""
    secret: str | None = None
    obsidian: ObsidianRequest | None = None  # None 表示不改


class OpenRequest(BaseModel):
    open: bool = False


class LLMTestRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None


def create_app(root: Path | None = None) -> FastAPI:
    root = (root or Path(config.env_var("ROOT") or meetings.DEFAULT_ROOT)).expanduser()
    app = FastAPI(title="tingo", docs_url=None, redoc_url=None)
    lock = threading.Lock()  # 保护 jobs 列表和录音状态
    jobs: list[Job] = []  # 进行中和刚结束（还没被界面收起）的任务，按开始先后
    state: dict = {"recording": None, "live": None, "hotwords": []}  # 正在进行的那一场录音

    def folder(meeting_id: str) -> Path:
        path = (root / meeting_id).resolve()
        if path.parent != root.resolve() or not path.is_dir():
            raise HTTPException(404, "找不到这场会议")
        return path

    def conflict(new: Job, j: Job) -> str | None:
        if new.kind == "record" and j.state == "recording":
            return "已经在录音了，先结束这一场"
        if new.kind == "models" and j.kind == "models":
            return "已经在下载模型了，等它下完"
        if new.meeting and new.kind != "models" and j.kind != "models" and j.meeting == new.meeting:
            return "这场会议正在处理，等它完成"
        return None

    def start_job(job: Job) -> None:
        """登记一个任务。录音同时只能有一场；同一场会议、模型下载各自同时只能有一个任务；其余可以并行
        （转写共用一个识别模型，在 asr.infer_lock 上排队；生成纪要主要等网络）。"""
        with lock:
            for j in jobs:
                if j.busy and (msg := conflict(job, j)):
                    raise HTTPException(409, msg)
            finished = [j for j in jobs if not j.busy]
            if len(finished) > KEEP_FINISHED:
                drop = {j.id for j in finished[: len(finished) - KEEP_FINISHED]}
                jobs[:] = [j for j in jobs if j.id not in drop]
            jobs.append(job)

    def finish(job: Job) -> None:
        with lock:
            job.finished_at = time.time()
            job.state = "done"

    def fail(job: Job, e: Exception) -> None:
        with lock:
            job.finished_at = time.time()
            job.error = str(e) or e.__class__.__name__
            job.state = "error"
        log.error("%s %s 失败：%s", job.kind, job.meeting, job.error, exc_info=e)

    def job_json(job: Job) -> dict:
        """调用方持有 lock。录音中的任务另带电平和实时文字（见 snapshot）。"""
        data = asdict(job)
        data["elapsed"] = (job.finished_at or time.time()) - job.started_at
        return data

    # ---------- 处理流程 ----------

    def run_summarize(job: Job, tr: Transcript, out: Path) -> None:
        from .summarize import summarize

        cfg = config.load()
        job.state = "summarizing"

        def on_token(t: str) -> None:
            job.minutes += t

        def on_stage(s: str) -> None:
            job.stage = s
            job.minutes = ""
            job.thinking = 0

        def on_think(n: int) -> None:
            job.thinking = n

        t = time.time()
        text = summarize(
            tr,
            cfg.llm,
            meeting_date=meetings.meeting_date(out),
            on_token=on_token,
            on_stage=on_stage,
            on_think=on_think,
        )
        (out / MINUTES).write_text(text, encoding="utf-8")
        job.minutes = text
        log.info("%s 纪要完成：模型 %s，%.0f 秒", out.name, cfg.llm.model, time.time() - t)
        if cfg.obsidian.auto and cfg.obsidian.vault.strip():
            # 自动存进 Obsidian；失败不影响纪要本身，只提示一下
            from . import obsidian

            try:
                obsidian.export(cfg.obsidian, out)
                job.notice = "纪要已同步到 Obsidian"
            except Exception as e:  # noqa: BLE001
                job.notice = f"没能同步到 Obsidian：{e}"
                log.warning("%s 同步到 Obsidian 失败：%s", out.name, e)

    def run_process(job: Job, audio: Path, out: Path, hotwords: list[str]) -> None:
        from . import asr
        from .live import transcribe_file

        try:
            job.state = "loading"
            asr._load_model()
            job.state = "transcribing"
            t = time.time()
            tr = transcribe_file(
                audio,
                hotwords=config.load().hotwords + hotwords,
                on_progress=lambda f: setattr(job, "stage", f"{f:.0%}"),
            )
            job.stage = ""
            log.info("%s 文件转写完成：%s，%.0f 秒", out.name, fmt_ts(tr.duration_ms), time.time() - t)
            if not tr.sentences:
                raise RuntimeError("没有识别出任何语音")
            meetings.save_transcript(tr, out)
            run_summarize(job, tr, out)
            finish(job)
        except Exception as e:  # noqa: BLE001 - 错误原样显示给用户
            fail(job, e)

    # ---------- 环境与配置 ----------

    @app.get("/api/env")
    def env() -> dict:
        from . import asr
        from .record import system_audio_supported

        cfg = config.load()
        cache = Path(os.environ.get("MODELSCOPE_CACHE", Path.home() / ".cache" / "modelscope"))
        return {
            "version": __version__,
            "root": str(root),
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "system_audio": system_audio_supported(),
            "model_cached": asr.model_loaded() or any(cache.glob("**/*speech_seaco_paraformer*")),
            "model_loaded": asr.model_loaded(),
            "llm_ready": bool(cfg.llm.api_key) or is_ollama(cfg.llm.base_url, cfg.llm.model),
            "llm_model": cfg.llm.model,
        }

    @app.get("/api/config")
    def get_config() -> dict:
        cfg = config.load()
        key = cfg.llm.api_key
        return {
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "api_key_set": bool(key),
            "api_key_hint": f"{key[:3]}…{key[-4:]}" if len(key) > 10 else ("已设置" if key else ""),
            "hotwords": cfg.hotwords,
            "webhook": cfg.webhook,
            "secret_set": bool(cfg.webhook_secret),
            "obsidian": asdict(cfg.obsidian),
            "path": str(config.CONFIG_PATH),
        }

    @app.put("/api/config")
    def put_config(req: ConfigRequest) -> dict:
        cfg = config.load()
        cfg.llm.base_url, cfg.llm.model = req.base_url.strip(), req.model.strip()
        if req.api_key is not None:
            cfg.llm.api_key = req.api_key.strip()
        cfg.hotwords = [w.strip() for w in req.hotwords if w.strip()]
        cfg.webhook = req.webhook.strip()
        if req.secret is not None:
            cfg.webhook_secret = req.secret.strip()
        if req.obsidian is not None:
            o = req.obsidian
            vault = o.vault.strip()
            home = os.environ.get("HOME")
            if vault.startswith("~/") and home is not None:
                vault = f"{home}/{vault[2:]}"  # 设置页里可以填 ~/…
            cfg.obsidian = config.ObsidianConfig(vault, o.folder.strip().strip("/"), o.auto, o.transcript)
        config.save(cfg)
        return get_config()

    @app.get("/api/llm/providers")
    def llm_providers() -> list[dict]:
        """本机常见的模型中转是否在跑、有哪些模型。设置页用来一键选择。"""
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(len(LOCAL_PROVIDERS)) as pool:
            found = list(pool.map(lambda p: _list_models(p["base_url"], p["api_key"]), LOCAL_PROVIDERS))
        return [
            {**p, "running": models is not None, "models": models or []}
            for p, models in zip(LOCAL_PROVIDERS, found, strict=True)
        ]

    @app.post("/api/llm/models")
    def llm_models(req: LLMTestRequest) -> dict:
        key = req.api_key if req.api_key is not None else config.load().llm.api_key
        models = _list_models(req.base_url, key or "none")
        return {"ok": models is not None, "models": models or []}

    @app.post("/api/config/test")
    def test_llm(req: LLMTestRequest) -> dict:
        from openai import OpenAI

        key = req.api_key if req.api_key is not None else config.load().llm.api_key
        try:
            client = OpenAI(base_url=req.base_url, api_key=key or "ollama", timeout=15)
            ids = [m.id for m in client.models.list().data]
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"连不上：{e}"}
        # Ollama 列出的模型名带 :latest
        if ids and req.model not in ids and f"{req.model}:latest" not in ids:
            return {"ok": False, "message": f"连上了，但没有模型 {req.model}。可用：{'、'.join(ids[:8])}"}
        return {"ok": True, "message": "连接正常"}

    # ---------- 会议 ----------

    @app.get("/api/meetings")
    def list_meetings() -> list[dict]:
        return meetings.list_meetings(root)

    @app.get("/api/meetings/{meeting_id}")
    def get_meeting(meeting_id: str) -> dict:
        path = folder(meeting_id)
        info = meetings.summary(path)
        minutes_path = path / MINUTES
        data: dict = {
            **info,
            "minutes": minutes_path.read_text(encoding="utf-8") if minutes_path.exists() else "",
            "turns": [],
            "speaker_list": [],
            "audio": meetings.find_audio(path) is not None,
            "path": str(path),
        }
        if info["has_transcript"]:
            tr = Transcript.load(path / TRANSCRIPT_JSON)
            order = tr.speakers_in_order()
            data["speaker_list"] = [
                {"index": i, "name": tr.name_of(s), "custom": s in tr.speaker_names} for i, s in enumerate(order)
            ]
            index_of_name = {tr.name_of(s): i for i, s in enumerate(order)}
            data["turns"] = [
                {
                    "speaker": t.speaker,
                    "color": index_of_name.get(t.speaker, 0),
                    "start_ms": t.start_ms,
                    "ts": fmt_ts(t.start_ms),
                    "text": t.text,
                }
                for t in tr.turns()
            ]
        return data

    @app.get("/api/meetings/{meeting_id}/audio")
    def get_audio(meeting_id: str) -> FileResponse:
        audio = meetings.find_audio(folder(meeting_id))
        if not audio:
            raise HTTPException(404, "没有录音文件")
        return FileResponse(audio)

    @app.put("/api/meetings/{meeting_id}/speakers")
    def put_speakers(meeting_id: str, req: SpeakersRequest) -> dict:
        path = folder(meeting_id)
        tr = Transcript.load(path / TRANSCRIPT_JSON)
        tr.speaker_names = {s: n.strip() for s, n in zip(tr.speakers_in_order(), req.names) if n.strip()}
        meetings.save_transcript(tr, path)
        return get_meeting(meeting_id)

    @app.put("/api/meetings/{meeting_id}/minutes")
    def put_minutes(meeting_id: str, req: MinutesRequest) -> dict:
        path = folder(meeting_id)
        (path / MINUTES).write_text(req.text.rstrip() + "\n", encoding="utf-8")
        return {"ok": True}

    @app.post("/api/meetings/{meeting_id}/summarize")
    def post_summarize(meeting_id: str) -> dict:
        path = folder(meeting_id)
        if not (path / TRANSCRIPT_JSON).exists():
            raise HTTPException(400, "还没有转写，不能生成纪要")
        job = Job("summarize", meeting_id, "summarizing")
        start_job(job)

        def work() -> None:
            try:
                run_summarize(job, Transcript.load(path / TRANSCRIPT_JSON), path)
                finish(job)
            except Exception as e:  # noqa: BLE001
                fail(job, e)

        reply = asdict(job)
        threading.Thread(target=work, daemon=True).start()
        return reply

    @app.post("/api/meetings/{meeting_id}/process")
    def post_process(meeting_id: str) -> dict:
        """有录音但还没转写的会议（比如上次处理被打断）重新跑一遍。"""
        path = folder(meeting_id)
        audio = meetings.find_audio(path)
        if not audio:
            raise HTTPException(400, "没有录音文件")
        job = Job("process", meeting_id, "loading")
        start_job(job)
        reply = asdict(job)
        threading.Thread(target=run_process, args=(job, audio, path, []), daemon=True).start()
        return reply

    @app.post("/api/meetings/{meeting_id}/push")
    def post_push(meeting_id: str) -> dict:
        from .push import detect, send

        path = folder(meeting_id)
        cfg = config.load()
        if not cfg.webhook:
            raise HTTPException(400, "还没有配置群机器人 webhook，去设置里填一下")
        if not (path / MINUTES).exists():
            raise HTTPException(400, "还没有纪要")
        try:
            send(cfg.webhook, (path / MINUTES).read_text(encoding="utf-8"), cfg.webhook_secret)
        except Exception as e:
            raise HTTPException(502, f"推送失败：{e}") from e
        name = {"feishu": "飞书", "dingtalk": "钉钉", "wecom": "企业微信"}[detect(cfg.webhook)]
        return {"ok": True, "message": f"已推送到{name}群"}

    @app.post("/api/meetings/{meeting_id}/reveal")
    def reveal(meeting_id: str) -> dict:
        path = folder(meeting_id)
        if platform.system() == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        elif platform.system() == "Windows":
            os.startfile(path)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return {"ok": True}

    @app.post("/api/meetings/{meeting_id}/obsidian")
    def post_obsidian(meeting_id: str, req: OpenRequest | None = None) -> dict:
        """存进 Obsidian；open=true 时顺便用 Obsidian 打开这篇笔记"""
        from . import obsidian

        path = folder(meeting_id)
        try:
            out = obsidian.export(config.load().obsidian, path)
        except Exception as e:
            raise HTTPException(400, str(e)) from e
        uri = obsidian.uri(out.note)
        if req and req.open:
            _open(uri)
        return {"ok": True, "path": str(out.note), "uri": uri, "drawings": len(out.drawings)}

    @app.post("/api/meetings/{meeting_id}/excalidraw")
    def post_excalidraw(meeting_id: str) -> dict:
        """把纪要里的鱼骨图导出成 .excalidraw 文件（放在会议文件夹里），并在访达里选中，
        可以拖进 excalidraw.com 或用 Obsidian 的 Excalidraw 插件继续改。"""
        from . import fishbone

        path = folder(meeting_id)
        if not (path / MINUTES).exists():
            raise HTTPException(400, "还没有纪要")
        minutes = (path / MINUTES).read_text(encoding="utf-8")
        try:
            tr: Transcript | None = Transcript.load(path / TRANSCRIPT_JSON)
        except Exception:  # noqa: BLE001
            tr = None
        turns = tr.turns() if tr is not None else []
        duration = tr.duration_ms if tr is not None else 2**64 - 1
        specs = [s for _, s in fishbone.blocks(minutes) if s is not None]
        if not specs:
            raise HTTPException(400, "这份纪要里没有鱼骨图")
        paths = []
        for k, spec in enumerate(specs):
            grounds = [fishbone.ground(b, turns, duration) for b in spec.branches]
            p = path / ("鱼骨图.excalidraw" if k == 0 else f"鱼骨图{k + 1}.excalidraw")
            p.write_text(fishbone.dumps(fishbone.excalidraw(spec, grounds)), encoding="utf-8")
            paths.append(str(p))
        _open(paths[0], reveal=True)
        return {"ok": True, "paths": paths}

    @app.get("/api/obsidian/vaults")
    def obsidian_vaults() -> list[dict]:
        from . import obsidian

        return [{"name": n, "path": p} for n, p in obsidian.vaults()]

    # ---------- 录音与导入 ----------

    @app.post("/api/record/start")
    def record_start(req: RecordRequest) -> dict:
        from .record import Recording, system_audio_supported

        if not req.system and not req.mic:
            raise HTTPException(400, "系统声音和麦克风至少开一个")
        system = req.system and system_audio_supported()
        out = meetings.new_folder(root, req.title)
        job = Job(
            "record", out.name, "recording", sources=[n for n, on in (("system", system), ("mic", req.mic)) if on]
        )
        start_job(job)
        rec = Recording(out, system_audio=system, mic=req.mic)
        try:
            rec.start()
        except Exception as e:
            with lock:
                jobs[:] = [j for j in jobs if j.id != job.id]
            shutil.rmtree(out, ignore_errors=True)
            raise HTTPException(500, str(e)) from e
        from .live import LiveTranscriber

        # 边录边转：录音时就分段识别，结束后只剩最后一段和一次说话人聚类
        live = LiveTranscriber(out, rec.sources, hotwords=config.load().hotwords + req.hotwords)
        live.start()
        with lock:
            state["recording"], state["hotwords"], state["live"] = rec, req.hotwords, live
        log.info("%s 开始录音：%s", out.name, "+".join(rec.sources))
        return asdict(job)

    @app.post("/api/record/stop")
    def record_stop() -> dict:
        with lock:
            job = next((j for j in jobs if j.kind == "record" and j.state == "recording"), None)
            rec = state["recording"]
            if job is None or rec is None:
                raise HTTPException(400, "没有正在进行的录音")
            # 录音停下后转到后台：转写、生成纪要的同时就可以开下一场
            job.state = "saving"
            reply = asdict(job)  # 后台线程马上会改状态，回给界面的固定是 saving
            live, hotwords = state["live"], state["hotwords"]
            state["recording"], state["live"], state["hotwords"] = None, None, []

        def work() -> None:
            from .record import mixdown

            out = root / job.meeting
            final = out / AUDIO
            stopped = time.time()
            try:
                rec.stop_capture()
                job.state = "finalizing"
                tr = live.finish(final) if live else None  # 原始音轨合成前先把最后几秒转完
            except Exception as e:  # noqa: BLE001
                fail(job, e)
                return
            if live:
                log.info(
                    "%s 结束录音后转写：%.1f 秒（剩余 %d 段 %.1f 秒，%d 个声纹聚类 %.1f 秒）%s",
                    out.name,
                    time.time() - stopped,
                    live.timings.get("tail_chunks", 0),
                    live.timings.get("tail_s", 0),
                    live.timings.get("voiceprints", 0),
                    live.timings.get("cluster_s", 0),
                    f"，边录边转出错：{live.error!r}" if live.error else "",
                )
            if tr is None or not tr.sentences:
                # 边录边转出错（或什么都没识别到）时退回整段转写；合成音频在先，因为要用它转写
                try:
                    mixdown(out).rename(final)
                except Exception as e:  # noqa: BLE001
                    fail(job, e)
                    return
                run_process(job, final, out, hotwords)
                return
            # 转写先落盘，合成音频（长会议要二三十秒）和生成纪要同时进行
            meetings.save_transcript(tr, out)
            mix_error: list[Exception] = []

            def mix() -> None:
                t = time.time()
                try:
                    mixdown(out).rename(final)
                    log.info("%s 合成录音：%.1f 秒", out.name, time.time() - t)
                except Exception as e:  # noqa: BLE001
                    mix_error.append(e)

            mixer = threading.Thread(target=mix, daemon=True)
            mixer.start()
            try:
                run_summarize(job, tr, out)
                mixer.join()
                if mix_error:
                    raise RuntimeError(f"纪要已生成，但合成录音失败：{mix_error[0]}（原始音轨还在会议文件夹里）")
                finish(job)
            except Exception as e:  # noqa: BLE001
                fail(job, e)

        threading.Thread(target=work, daemon=True).start()
        return reply

    @app.post("/api/import")
    def import_file(
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()] = "",
        modified: Annotated[float, Form()] = 0,  # 文件的修改时间（毫秒），当作会议时间
    ) -> dict:
        name = Path(file.filename or "录音.m4a")
        if name.suffix.lower() not in meetings.AUDIO_EXTS:
            raise HTTPException(400, f"不支持的文件类型：{name.suffix}")
        when = datetime.fromtimestamp(modified / 1000) if modified else datetime.now()
        out = meetings.new_folder(root, title or name.stem, when)
        job = Job("process", out.name, "saving")
        start_job(job)
        out.mkdir(parents=True)
        audio = out / f"录音{name.suffix.lower()}"
        with audio.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        reply = asdict(job)
        threading.Thread(target=run_process, args=(job, audio, out, []), daemon=True).start()
        return reply

    def snapshot() -> list[dict]:
        from . import asr
        from .record import track_level

        loaded = asr.model_loaded()
        with lock:
            data = [job_json(j) for j in jobs]
            live = state["live"]
        for d in data:
            d["model_loaded"] = loaded
            if d["state"] == "recording":
                out = root / d["meeting"]
                d["levels"] = {s: track_level(out / f"{s}.wav") for s in d["sources"]}
                d["live"] = live.recent(8) if live else []
        return data

    @app.get("/api/jobs")
    def get_jobs() -> dict:
        """所有任务（进行中的和刚结束的），按开始时间排列"""
        from . import asr

        return {"jobs": snapshot(), "model_loaded": asr.model_loaded()}

    @app.get("/api/job")
    def get_job() -> dict:
        """旧接口：只返回一个任务（正在录音的优先，其次最近一个进行中的，再次最近一个）"""
        data = snapshot()
        busy = [d for d in data if d["state"] not in ("done", "error")]
        pick = next((d for d in data if d["state"] == "recording"), None) or (busy or data or [None])[-1]
        return pick or {"state": "idle"}

    @app.post("/api/job/dismiss")
    def dismiss() -> dict:
        """收起所有已结束的任务"""
        with lock:
            jobs[:] = [j for j in jobs if j.busy]
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/dismiss")
    def dismiss_one(job_id: str) -> dict:
        """收起一个已结束的任务"""
        with lock:
            jobs[:] = [j for j in jobs if j.busy or j.id != job_id]
        return {"ok": True}

    from fastapi.middleware.cors import CORSMiddleware

    # 桌面版：界面跑在 Tauri 的 webview 里（tauri://localhost），开发时在 Vite（localhost:1420）
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(tauri://localhost|https?://tauri\.localhost|http://localhost:1420)$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def no_cache_web(request, call_next):
        # 界面文件随版本更新，浏览器 / 独立窗口缓存旧的 CSS、JS 会出现"新结构 + 旧样式"的错乱
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        # 界面由 app/ 用 Vite 构建，资源文件名自带内容哈希；首页本身不缓存，更新后立即生效
        return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
