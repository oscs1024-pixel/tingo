"""图形界面的本地服务：只监听 127.0.0.1，前端在 web/ 目录。

同一时间只跑一个任务（录音 / 转写 / 生成纪要），前端轮询 /api/job 拿进度。
"""

from __future__ import annotations

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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, meetings
from .meetings import AUDIO, MINUTES, TRANSCRIPT_JSON
from .transcript import Transcript, fmt_ts

WEB_DIR = Path(__file__).with_name("web")


@dataclass
class Job:
    kind: str  # record | process | summarize
    meeting: str
    state: str  # recording | saving | loading | transcribing | summarizing | done | error
    started_at: float = field(default_factory=time.time)
    error: str = ""
    minutes: str = ""  # 生成中的纪要，流式累加
    stage: str = ""
    sources: list[str] = field(default_factory=list)

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


class ConfigRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None  # None 表示不改
    hotwords: list[str] = []
    webhook: str = ""
    secret: str | None = None


class LLMTestRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None


def create_app(root: Path | None = None) -> FastAPI:
    root = (root or Path(os.environ.get("JIYAO_ROOT", meetings.DEFAULT_ROOT))).expanduser()
    app = FastAPI(title="jiyao", docs_url=None, redoc_url=None)
    lock = threading.Lock()
    state: dict = {"job": None, "recording": None, "hotwords": []}

    def folder(meeting_id: str) -> Path:
        path = (root / meeting_id).resolve()
        if path.parent != root.resolve() or not path.is_dir():
            raise HTTPException(404, "找不到这场会议")
        return path

    def current() -> Job | None:
        return state["job"]

    def start_job(job: Job) -> None:
        with lock:
            if current() and current().busy:
                raise HTTPException(409, "还有任务在进行，请稍等")
            state["job"] = job

    def fail(job: Job, e: Exception) -> None:
        job.state, job.error = "error", str(e) or e.__class__.__name__

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

        text = summarize(tr, cfg.llm, meeting_date=meetings.meeting_date(out), on_token=on_token, on_stage=on_stage)
        (out / MINUTES).write_text(text, encoding="utf-8")
        job.minutes = text

    def run_process(job: Job, audio: Path, out: Path, hotwords: list[str]) -> None:
        from . import asr

        try:
            job.state = "loading"
            asr._load_model()
            job.state = "transcribing"
            tr = asr.transcribe(audio, hotwords=config.load().hotwords + hotwords)
            if not tr.sentences:
                raise RuntimeError("没有识别出任何语音")
            meetings.save_transcript(tr, out)
            run_summarize(job, tr, out)
            job.state = "done"
        except Exception as e:  # noqa: BLE001 - 错误原样显示给用户
            fail(job, e)

    def preload_model() -> None:
        from . import asr

        threading.Thread(target=asr._load_model, daemon=True).start()

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
            "llm_ready": bool(cfg.llm.api_key) or _is_local(cfg.llm.base_url),
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
        config.save(cfg)
        return get_config()

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
                job.state = "done"
            except Exception as e:  # noqa: BLE001
                fail(job, e)

        threading.Thread(target=work, daemon=True).start()
        return asdict(job)

    @app.post("/api/meetings/{meeting_id}/process")
    def post_process(meeting_id: str) -> dict:
        """有录音但还没转写的会议（比如上次处理被打断）重新跑一遍。"""
        path = folder(meeting_id)
        audio = meetings.find_audio(path)
        if not audio:
            raise HTTPException(400, "没有录音文件")
        job = Job("process", meeting_id, "loading")
        start_job(job)
        threading.Thread(target=run_process, args=(job, audio, path, []), daemon=True).start()
        return asdict(job)

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
            state["job"] = None
            shutil.rmtree(out, ignore_errors=True)
            raise HTTPException(500, str(e)) from e
        state["recording"], state["hotwords"] = rec, req.hotwords
        preload_model()
        return asdict(job)

    @app.post("/api/record/stop")
    def record_stop() -> dict:
        job, rec = current(), state["recording"]
        if not job or job.kind != "record" or job.state != "recording" or rec is None:
            raise HTTPException(400, "没有正在进行的录音")
        job.state = "saving"
        state["recording"] = None

        def work() -> None:
            try:
                audio = rec.stop()
                final = audio.with_name(AUDIO)
                audio.rename(final)
            except Exception as e:  # noqa: BLE001
                fail(job, e)
                return
            run_process(job, final, final.parent, state["hotwords"])

        threading.Thread(target=work, daemon=True).start()
        return asdict(job)

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
        threading.Thread(target=run_process, args=(job, audio, out, []), daemon=True).start()
        return asdict(job)

    @app.get("/api/job")
    def get_job() -> dict:
        from . import asr
        from .record import track_level

        job = current()
        if not job:
            return {"state": "idle"}
        data = asdict(job)
        data["elapsed"] = time.time() - job.started_at
        data["model_loaded"] = asr.model_loaded()
        if job.state == "recording":
            out = root / job.meeting
            data["levels"] = {s: track_level(out / f"{s}.wav") for s in job.sources}
        return data

    @app.post("/api/job/dismiss")
    def dismiss() -> dict:
        with lock:
            if current() and not current().busy:
                state["job"] = None
        return {"ok": True}

    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _is_local(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", ":11434"))
