import io

import pytest
from fastapi.testclient import TestClient

from tingo import summarize as sm
from tingo.server import create_app
from tingo.transcript import Sentence, Transcript


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr("tingo.config.CONFIG_PATH", tmp_path / "config.toml")
    for k in ("TINGO_LLM_API_KEY", "TINGO_LLM_BASE_URL", "TINGO_LLM_MODEL", "DEEPSEEK_API_KEY", "TINGO_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)
    r = tmp_path / "meetings"
    m = r / "2026-11-02_1430_周会"
    m.mkdir(parents=True)
    tr = Transcript(
        str(m / "录音.m4a"), 5000, [Sentence(0, 0, 1000, "下周五上线。"), Sentence(1, 1500, 2500, "好的。")]
    )
    tr.save(m / "transcript.json")
    (m / "纪要.md").write_text("# 周会\n\n## 一句话总结\n定了上线时间。\n")
    (m / "录音.m4a").write_bytes(b"\0" * 2048)
    return r


@pytest.fixture
def client(root):
    return TestClient(create_app(root))


def wait_job(client, states=("done", "error")):
    import time

    for _ in range(200):
        job = client.get("/api/job").json()
        if job["state"] in states:
            return job
        time.sleep(0.02)
    raise AssertionError(job)


def test_list_and_detail(client):
    items = client.get("/api/meetings").json()
    assert [(m["id"], m["title"], m["summary"], m["time"]) for m in items] == [
        ("2026-11-02_1430_周会", "周会", "定了上线时间。", "14:30")
    ]
    d = client.get("/api/meetings/2026-11-02_1430_周会").json()
    assert [t["speaker"] for t in d["turns"]] == ["说话人1", "说话人2"]
    assert d["audio"] is True


def test_path_traversal_rejected(client):
    assert client.get("/api/meetings/..").status_code == 404
    assert client.get("/api/meetings/%2E%2E%2Fetc").status_code == 404


def test_rename_speakers_then_clear(client, root):
    d = client.put("/api/meetings/2026-11-02_1430_周会/speakers", json={"names": ["王总", ""]}).json()
    assert [s["name"] for s in d["speaker_list"]] == ["王总", "说话人2"]
    assert "**王总**" in (root / "2026-11-02_1430_周会" / "转写.md").read_text()
    d = client.put("/api/meetings/2026-11-02_1430_周会/speakers", json={"names": ["", ""]}).json()
    assert [s["name"] for s in d["speaker_list"]] == ["说话人1", "说话人2"]


def test_summarize_job_streams_and_saves(client, root, monkeypatch):
    monkeypatch.setenv("TINGO_LLM_API_KEY", "x")

    def fake_chat(cfg, prompt, on_token=None, think=True, on_think=None):
        assert "下周五（11月13日）" in prompt
        for part in ("# 新标题\n\n", "## 一句话总结\n", "改好了"):
            on_token(part)
        return "# 新标题\n\n## 一句话总结\n改好了"

    monkeypatch.setattr(sm, "_chat", fake_chat)
    assert client.post("/api/meetings/2026-11-02_1430_周会/summarize").status_code == 200
    job = wait_job(client)
    assert job["state"] == "done", job
    text = (root / "2026-11-02_1430_周会" / "纪要.md").read_text()
    assert text.startswith("# 新标题\n\n- 日期：2026-11-02（星期一）")


MID = "2026-11-02_1430_周会"
MID2 = "2026-11-03_0900_站会"


def wait_all(client, timeout=5):
    import time

    end = time.time() + timeout
    while time.time() < end:
        jobs = client.get("/api/jobs").json()["jobs"]
        if all(j["state"] in ("done", "error") for j in jobs):
            return jobs
        time.sleep(0.02)
    raise AssertionError(jobs)


@pytest.fixture
def second(root):
    m = root / MID2
    m.mkdir()
    Transcript(str(m / "录音.m4a"), 1000, [Sentence(0, 0, 900, "开始。")]).save(m / "transcript.json")
    return m


@pytest.fixture
def gated_chat(monkeypatch):
    """生成纪要卡在 gate 上，直到测试放行；放行前先报一次思考进度"""
    import threading

    monkeypatch.setenv("TINGO_LLM_API_KEY", "x")
    gate = threading.Event()

    def fake(cfg, prompt, on_token=None, think=True, on_think=None):
        if on_think:
            on_think(42)
        gate.wait(5)
        return "# T"

    monkeypatch.setattr(sm, "_chat", fake)
    yield gate
    gate.set()


def test_same_meeting_rejected_other_meeting_in_parallel(client, second, gated_chat):
    a = client.post(f"/api/meetings/{MID}/summarize")
    assert a.status_code == 200 and a.json()["id"].startswith("j")
    r = client.post(f"/api/meetings/{MID}/summarize")
    assert r.status_code == 409 and r.json()["detail"] == "这场会议正在处理，等它完成"
    assert client.post(f"/api/meetings/{MID}/process").status_code == 409
    b = client.post(f"/api/meetings/{MID2}/summarize")
    assert b.status_code == 200 and b.json()["id"] != a.json()["id"]
    jobs = client.get("/api/jobs").json()["jobs"]
    assert [(j["meeting"], j["state"]) for j in jobs] == [(MID, "summarizing"), (MID2, "summarizing")]
    gated_chat.set()
    assert [j["state"] for j in wait_all(client)] == ["done", "done"]
    # 结束之后同一场会议可以再来
    assert client.post(f"/api/meetings/{MID}/summarize").status_code == 200
    wait_all(client)


def test_jobs_shape_thinking_and_frozen_elapsed(client, gated_chat):
    import time

    empty = client.get("/api/jobs").json()
    assert empty["jobs"] == [] and isinstance(empty["model_loaded"], bool)  # 别的测试可能已经加载了真模型
    assert client.get("/api/job").json() == {"state": "idle"}
    jid = client.post(f"/api/meetings/{MID}/summarize").json()["id"]
    for _ in range(100):
        body = client.get("/api/jobs").json()
        if body["jobs"][0]["thinking"]:
            break
        time.sleep(0.02)
    assert set(body) == {"jobs", "model_loaded"}
    j = body["jobs"][0]
    assert j["id"] == jid and j["kind"] == "summarize" and j["state"] == "summarizing"
    assert (
        j["thinking"] == 42
        and j["finished_at"] == 0
        and j["elapsed"] >= 0
        and j["model_loaded"] == empty["model_loaded"]
    )
    assert client.get("/api/job").json()["id"] == jid  # 旧接口返回最近一个进行中的
    gated_chat.set()
    done = wait_all(client)[0]
    assert done["finished_at"] >= done["started_at"]
    assert done["elapsed"] == pytest.approx(done["finished_at"] - done["started_at"])
    time.sleep(0.05)
    assert client.get("/api/jobs").json()["jobs"][0]["elapsed"] == done["elapsed"]  # 结束后用时不再增长
    assert client.get("/api/job").json()["id"] == jid  # 没有进行中的，返回最近一个


def test_dismiss_one_and_all(client, second, gated_chat):
    gated_chat.set()
    a = client.post(f"/api/meetings/{MID}/summarize").json()["id"]
    wait_all(client)
    b = client.post(f"/api/meetings/{MID2}/summarize").json()["id"]
    wait_all(client)
    c = client.post(f"/api/meetings/{MID}/summarize").json()["id"]
    wait_all(client)
    assert client.post(f"/api/jobs/{b}/dismiss").json() == {"ok": True}
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [a, c]
    assert client.post("/api/jobs/nope/dismiss").status_code == 200
    gated_chat.clear()
    d = client.post(f"/api/meetings/{MID2}/summarize").json()["id"]
    # 进行中的任务收不起来
    client.post(f"/api/jobs/{d}/dismiss")
    client.post("/api/job/dismiss")
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [d]
    gated_chat.set()
    wait_all(client)
    client.post("/api/job/dismiss")
    assert client.get("/api/jobs").json()["jobs"] == []


def test_keeps_only_recent_finished_jobs(client, gated_chat):
    gated_chat.set()
    ids = []
    for _ in range(8):
        ids.append(client.post(f"/api/meetings/{MID}/summarize").json()["id"])
        wait_all(client)
    # 登记新任务时，结束了的超过 5 个就删掉最早的
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == ids[-6:]


@pytest.fixture
def fake_recorder(monkeypatch):
    """不真的录音：录音组件、边录边转都换成假的；结束录音后的转写卡在 gate 上"""
    import threading

    from tingo import live, record

    gate = threading.Event()

    class Rec:
        def __init__(self, out, system_audio=True, mic=True):
            self.out, self.sources = out, ["mic"]

        def start(self):
            self.out.mkdir(parents=True)

        def stop_capture(self):
            pass

    class Live:
        error = None

        def __init__(self, out, sources, hotwords=None):
            self.hotwords, self.timings = hotwords, {}

        def start(self):
            pass

        def recent(self, n):
            return [{"start_ms": 0, "text": "大家好"}]

        def finish(self, final):
            gate.wait(5)
            return Transcript(str(final), 1000, [Sentence(0, 0, 900, "大家好。")])

    def mixdown(out):
        (out / "mix.m4a").write_bytes(b"\0")
        return out / "mix.m4a"

    monkeypatch.setattr(record, "Recording", Rec)
    monkeypatch.setattr(record, "system_audio_supported", lambda: False)
    monkeypatch.setattr(record, "track_level", lambda path, window_s=0.15: 0.5)
    monkeypatch.setattr(record, "mixdown", mixdown)
    monkeypatch.setattr(live, "LiveTranscriber", Live)
    monkeypatch.setenv("TINGO_LLM_API_KEY", "x")
    monkeypatch.setattr(sm, "_chat", lambda cfg, prompt, on_token=None, think=True, on_think=None: "# 录音会")
    yield gate
    gate.set()


def test_second_recording_rejected_and_stop_frees_slot(client, fake_recorder):
    first = client.post("/api/record/start", json={"title": "评审"}).json()
    assert first["state"] == "recording" and first["sources"] == ["mic"]
    r = client.post("/api/record/start", json={"title": "评审"})
    assert r.status_code == 409 and r.json()["detail"] == "已经在录音了，先结束这一场"
    rec = client.get("/api/jobs").json()["jobs"][0]
    assert rec["levels"] == {"mic": 0.5} and rec["live"] == [{"start_ms": 0, "text": "大家好"}]
    assert client.get("/api/job").json()["id"] == first["id"]

    stopped = client.post("/api/record/stop").json()
    assert stopped["id"] == first["id"] and stopped["state"] == "saving"
    assert client.post("/api/record/stop").status_code == 400
    # 上一场还在后台转写，马上就能开下一场
    nxt = client.post("/api/record/start", json={"title": "评审"}).json()
    assert nxt["state"] == "recording" and nxt["meeting"] != first["meeting"]
    jobs = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert jobs[first["id"]]["state"] in ("saving", "finalizing") and "levels" not in jobs[first["id"]]
    assert client.get("/api/job").json()["id"] == nxt["id"]  # 旧接口优先返回正在录音的

    fake_recorder.set()
    import time

    for _ in range(250):
        jobs = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
        if jobs[first["id"]]["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert jobs[first["id"]]["state"] == "done", jobs[first["id"]]
    assert jobs[nxt["id"]]["state"] == "recording"


def test_edit_minutes(client, root):
    client.put("/api/meetings/2026-11-02_1430_周会/minutes", json={"text": "# 改过的\n"})
    assert (root / "2026-11-02_1430_周会" / "纪要.md").read_text() == "# 改过的\n"


def test_config_roundtrip_keeps_key_when_blank(client, tmp_path):
    body = {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "sk-1234567890abcd",
        "hotwords": ["星河", " ", "Kafka"],
        "webhook": "",
        "secret": None,
    }
    c = client.put("/api/config", json=body).json()
    assert c["api_key_set"] and c["api_key_hint"] == "sk-…abcd"
    assert c["hotwords"] == ["星河", "Kafka"]
    c = client.put("/api/config", json={**body, "api_key": None, "model": "deepseek-reasoner"}).json()
    assert c["api_key_set"] and c["model"] == "deepseek-reasoner"
    assert oct((tmp_path / "config.toml").stat().st_mode)[-3:] == "600"


def test_import_rejects_unknown_type(client):
    r = client.post("/api/import", files={"file": ("a.txt", io.BytesIO(b"x"))})
    assert r.status_code == 400


def test_push_without_webhook(client):
    r = client.post("/api/meetings/2026-11-02_1430_周会/push")
    assert r.status_code == 400 and "webhook" in r.json()["detail"]


def test_raw_tracks_are_not_meetings(client, root):
    (root / "2026-11-02_1500_录音中").mkdir()
    (root / "2026-11-02_1500_录音中" / "system.wav").write_bytes(b"\0" * 2048)
    assert [m["id"] for m in client.get("/api/meetings").json()] == ["2026-11-02_1430_周会"]


def test_static_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "纪要" in r.text


def test_llm_providers_reports_running_relays(client, monkeypatch):
    from tingo import server

    running = {"http://127.0.0.1:3425/v1": ["deepseek/deepseek-v4-flash", "minimax-cn/MiniMax-M3.1-Flash"]}
    monkeypatch.setattr(server, "_list_models", lambda base_url, api_key: running.get(base_url))
    got = {p["id"]: p for p in client.get("/api/llm/providers").json()}
    assert got["magpie"]["running"] and got["magpie"]["recommended"] in got["magpie"]["models"]
    assert not got["ccswitch"]["running"] and "15721" in got["ccswitch"]["base_url"]
    assert not got["ollama"]["running"]


def test_index_serves_built_frontend(client):
    """界面由 app/ 用 Vite 构建：资源文件名带内容哈希，首页本身不缓存。"""
    import re

    r = client.get("/")
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', r.text)
    assert assets, "首页应引用 Vite 构建出的资源"
    assert r.headers["cache-control"] == "no-cache"
    for a in assets:
        assert client.get(a).status_code == 200
    assert "cache-control" not in client.get("/api/meetings").headers


def test_cors_allows_tauri_and_vite_only(client):
    ok = client.get("/api/meetings", headers={"Origin": "tauri://localhost"})
    assert ok.headers.get("access-control-allow-origin") == "tauri://localhost"
    dev = client.get("/api/meetings", headers={"Origin": "http://localhost:1420"})
    assert dev.headers.get("access-control-allow-origin") == "http://localhost:1420"
    evil = client.get("/api/meetings", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


FISHBONE_MINUTES = (
    "# 周会\n\n## 原因分析\n```fishbone\n"
    '{"problem": "为什么延期？", "branches": [{"title": "上线定了", "refs": ["00:00"]}, {"title": "猜的"}]}'
    "\n```\n"
)


@pytest.fixture
def opened(monkeypatch):
    calls = []
    monkeypatch.setattr("tingo.server._open", lambda target, reveal=False: calls.append((target, reveal)))
    return calls


def test_config_obsidian_roundtrip_expands_home(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = client.get("/api/config").json()
    assert c["obsidian"] == {"vault": "", "folder": "会议纪要", "auto": True, "transcript": True}
    body = {"base_url": "https://api.deepseek.com", "model": "deepseek-chat", "hotwords": [], "webhook": ""}
    obs = {"vault": " ~/笔记 ", "folder": "/会议/", "auto": False, "transcript": True}
    c = client.put("/api/config", json={**body, "obsidian": obs}).json()
    assert c["obsidian"] == {"vault": f"{tmp_path}/笔记", "folder": "会议", "auto": False, "transcript": True}
    c = client.put("/api/config", json=body).json()  # 不带 obsidian 就不改
    assert c["obsidian"]["vault"] == f"{tmp_path}/笔记"
    assert "[obsidian]" in (tmp_path / "config.toml").read_text()


def test_obsidian_export_and_open(client, root, tmp_path, opened):
    mid = "2026-11-02_1430_周会"
    r = client.post(f"/api/meetings/{mid}/obsidian")
    assert r.status_code == 400 and "Obsidian 库" in r.json()["detail"]
    (root / mid / "纪要.md").write_text(FISHBONE_MINUTES)
    vault = tmp_path / "vault"
    vault.mkdir()
    from tingo import config

    cfg = config.load()
    cfg.obsidian.vault = str(vault)
    config.save(cfg)
    d = client.post(f"/api/meetings/{mid}/obsidian").json()
    assert d["ok"] and d["drawings"] == 1 and opened == []
    assert d["path"] == str(vault / "会议纪要" / "2026-11-02 周会.md")
    assert d["uri"].startswith("obsidian://open?path=") and "%E5%91%A8%E4%BC%9A.md" in d["uri"]
    d = client.post(f"/api/meetings/{mid}/obsidian", json={"open": True}).json()
    assert opened == [(d["uri"], False)]
    assert client.post("/api/meetings/nope/obsidian").status_code == 404


def test_excalidraw_export(client, root, opened):
    mid = "2026-11-02_1430_周会"
    r = client.post(f"/api/meetings/{mid}/excalidraw")
    assert r.status_code == 400 and r.json()["detail"] == "这份纪要里没有鱼骨图"
    (root / mid / "纪要.md").write_text(FISHBONE_MINUTES + "\n```fishbone\n坏的\n```\n" + FISHBONE_MINUTES[5:])
    d = client.post(f"/api/meetings/{mid}/excalidraw").json()
    assert [p.rsplit("/", 1)[1] for p in d["paths"]] == ["鱼骨图.excalidraw", "鱼骨图2.excalidraw"]
    assert opened == [(d["paths"][0], True)]
    import json

    drawing = json.loads((root / mid / "鱼骨图.excalidraw").read_text())
    texts = [e["text"] for e in drawing["elements"] if e["type"] == "text"]
    assert "为什么延期？" in texts and "依据很少 · 引用 1 段 · 6 字" in texts and "无原文依据" in texts


def test_obsidian_vaults_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert client.get("/api/obsidian/vaults").json() == []


def test_summarize_auto_syncs_to_obsidian(client, root, tmp_path, monkeypatch):
    from tingo import config

    monkeypatch.setenv("TINGO_LLM_API_KEY", "x")
    monkeypatch.setattr(sm, "_chat", lambda cfg, prompt, on_token=None, think=True, on_think=None: "# 新标题\n\n正文")
    cfg = config.load()
    cfg.obsidian.vault = str(tmp_path / "没有这个库")
    config.save(cfg)
    client.post("/api/meetings/2026-11-02_1430_周会/summarize")
    job = wait_job(client)
    assert job["state"] == "done" and job["notice"].startswith("没能同步到 Obsidian：还没有设置")
    client.post("/api/job/dismiss")
    (tmp_path / "没有这个库").mkdir()
    client.post("/api/meetings/2026-11-02_1430_周会/summarize")
    job = wait_job(client)
    assert job["notice"] == "纪要已同步到 Obsidian"
    assert (tmp_path / "没有这个库" / "会议纪要" / "2026-11-02 周会.md").exists()
    cfg.obsidian.auto = False
    config.save(cfg)
    client.post("/api/job/dismiss")
    client.post("/api/meetings/2026-11-02_1430_周会/summarize")
    assert wait_job(client)["notice"] == ""


def test_pdf_export(client, root, opened, monkeypatch):
    from tingo import record

    mid = "2026-11-02_1430_周会"
    calls = []

    def fake(html, out):
        calls.append(html)
        out.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(record, "html_to_pdf", fake)
    (root / mid / "纪要.md").write_text("# 周会：发布/排期\n\n## 一句话总结\n定了")
    d = client.post(f"/api/meetings/{mid}/pdf", json={"html": "<p>纪要</p>"}).json()
    assert d["path"].endswith("/周会：发布 排期.pdf") and calls == ["<p>纪要</p>"]
    assert opened == [(d["path"], True)]

    def broken(html, out):
        raise RuntimeError("排版超时")

    monkeypatch.setattr(record, "html_to_pdf", broken)
    r = client.post(f"/api/meetings/{mid}/pdf", json={"html": "<p></p>"})
    assert r.status_code == 500 and r.json()["detail"] == "排版超时"


def test_audio_served_with_standard_mime(client, root):
    mid = "2026-11-02_1430_周会"
    for f in (root / mid).glob("录音.*"):
        f.unlink()
    (root / mid / "录音.m4a").write_bytes(b"\0" * 64)
    r = client.get(f"/api/meetings/{mid}/audio")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mp4"


def test_ui_language_header_switches_messages(client):
    r = client.get("/api/meetings/nope", headers={"X-Tingo-Lang": "en"})
    assert r.status_code == 404 and r.json()["detail"] == "Meeting not found"
    # 不带请求头的请求（比如 <audio> 拉录音）沿用上一次的界面语言
    assert client.get("/api/meetings/nope").json()["detail"] == "Meeting not found"
    r = client.post(f"/api/meetings/{MID}/push", headers={"X-Tingo-Lang": "en"})
    assert r.status_code == 400 and r.json()["detail"] == "No group bot webhook is set; add one in Settings"
    providers = {p["id"]: p for p in client.get("/api/llm/providers").json()}
    assert providers["ollama"]["name"] == "Local Ollama" and "isn't running" in providers["ollama"]["hint"]
    r = client.get("/api/meetings/nope", headers={"X-Tingo-Lang": "zh"})
    assert r.json()["detail"] == "找不到这场会议"
    assert {p["id"]: p for p in client.get("/api/llm/providers").json()}["ollama"]["name"] == "本地 Ollama"


def test_job_messages_follow_ui_language(client, second, gated_chat):
    en = {"X-Tingo-Lang": "en"}
    assert client.post(f"/api/meetings/{MID}/summarize", headers=en).status_code == 200
    r = client.post(f"/api/meetings/{MID}/summarize", headers=en)
    assert r.status_code == 409 and r.json()["detail"] == "This meeting is being processed; wait for it to finish"
    gated_chat.set()
    wait_all(client)


def test_config_language_fields(client, tmp_path):
    c = client.get("/api/config").json()
    assert (c["ui_language"], c["minutes_language"], c["asr_language"]) == ("auto", "auto", "zh")
    body = {"base_url": "https://api.deepseek.com", "model": "deepseek-chat", "hotwords": [], "webhook": ""}
    c = client.put("/api/config", json={**body, "ui_language": "en", "minutes_language": "zh"}).json()
    assert (c["ui_language"], c["minutes_language"], c["asr_language"]) == ("en", "zh", "zh")
    c = client.put("/api/config", json={**body, "asr_language": "auto"}).json()  # 不传的不改
    assert (c["ui_language"], c["minutes_language"], c["asr_language"]) == ("en", "zh", "auto")
    text = (tmp_path / "config.toml").read_text()
    assert text.endswith('[ui]\nlanguage = "en"\n\n[minutes]\nlanguage = "zh"\n\n[asr]\nlanguage = "auto"\n')
    r = client.put("/api/config", json={**body, "minutes_language": "fr"}, headers={"X-Tingo-Lang": "en"})
    assert r.status_code == 400 and r.json()["detail"] == "Unsupported language: fr"


def test_minutes_language_setting_used_and_english_stages(client, root, monkeypatch):
    from tingo import config

    monkeypatch.setenv("TINGO_LLM_API_KEY", "x")
    seen = []

    def fake(cfg, prompt, on_token=None, think=True, on_think=None, **kw):
        seen.append(prompt)
        return "# New\n\n## Summary\nok"

    monkeypatch.setattr(sm, "_chat", fake)
    cfg = config.load()
    cfg.minutes_language = "en"
    config.save(cfg)
    client.post(f"/api/meetings/{MID}/summarize", headers={"X-Tingo-Lang": "en"})
    job = wait_job(client)
    assert job["state"] == "done", job
    assert seen[0].startswith(sm.TRANSLATE_TO_EN) and "[00:01] Speaker 2: 好的。" in seen[0]
    text = (root / MID / "纪要.md").read_text()
    assert text.startswith(
        "# New\n\n- Date: 2026-11-02 (Monday)\n- Duration: 00:05\n- Participants: Speaker 1, Speaker 2"
    )


def test_english_obsidian_notice(client, tmp_path, monkeypatch):
    from tingo import config

    monkeypatch.setenv("TINGO_LLM_API_KEY", "x")
    monkeypatch.setattr(sm, "_chat", lambda cfg, prompt, on_token=None, think=True, on_think=None: "# T")
    cfg = config.load()
    cfg.obsidian.vault = str(tmp_path / "missing")
    config.save(cfg)
    client.post(f"/api/meetings/{MID}/summarize", headers={"X-Tingo-Lang": "en"})
    assert wait_job(client)["notice"].startswith("Couldn't sync to Obsidian: No Obsidian vault is set")
    client.post("/api/job/dismiss")
    (tmp_path / "missing").mkdir()
    client.post(f"/api/meetings/{MID}/summarize")
    assert wait_job(client)["notice"] == "Minutes synced to Obsidian"


def test_excalidraw_export_english_minutes(client, root, opened):
    import json

    en = FISHBONE_MINUTES.replace("# 周会\n\n", "# Weekly\n\n## Summary\nok\n\n")
    (root / MID / "纪要.md").write_text(en + "\n" + en[en.index("```fishbone") :])
    d = client.post(f"/api/meetings/{MID}/excalidraw").json()
    assert [p.rsplit("/", 1)[1] for p in d["paths"]] == ["fishbone.excalidraw", "fishbone2.excalidraw"]
    drawing = json.loads((root / MID / "fishbone.excalidraw").read_text())
    texts = [e["text"] for e in drawing["elements"] if e["type"] == "text"]
    assert "Thin evidence · 1 quote(s) · 6 chars" in texts and "No evidence" in texts
    assert "00:00 Speaker 1: 下周五上线。" in texts


def test_meeting_card_reads_english_summary(client, root):
    (root / MID / "纪要.md").write_text("# Weekly\n\n- Date: 2026-11-02 (Monday)\n\n## Summary\nShip on Friday.\n")
    assert client.get("/api/meetings").json()[0]["summary"] == "Ship on Friday."
