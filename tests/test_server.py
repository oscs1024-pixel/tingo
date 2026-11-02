import io

import pytest
from fastapi.testclient import TestClient

from jiyao import summarize as sm
from jiyao.server import create_app
from jiyao.transcript import Sentence, Transcript


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr("jiyao.config.CONFIG_PATH", tmp_path / "config.toml")
    for k in ("JIYAO_LLM_API_KEY", "JIYAO_LLM_BASE_URL", "JIYAO_LLM_MODEL", "DEEPSEEK_API_KEY", "JIYAO_WEBHOOK"):
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
    monkeypatch.setenv("JIYAO_LLM_API_KEY", "x")

    def fake_chat(cfg, prompt, on_token=None, think=True):
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


def test_only_one_job_at_a_time(client, monkeypatch):
    import threading

    monkeypatch.setenv("JIYAO_LLM_API_KEY", "x")
    gate = threading.Event()
    monkeypatch.setattr(sm, "_chat", lambda cfg, prompt, on_token=None, think=True: gate.wait(5) and "# T")
    assert client.post("/api/meetings/2026-11-02_1430_周会/summarize").status_code == 200
    assert client.post("/api/meetings/2026-11-02_1430_周会/summarize").status_code == 409
    gate.set()
    wait_job(client)


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
    from jiyao import server

    running = {"http://127.0.0.1:3425/v1": ["deepseek/deepseek-v4-flash", "minimax-cn/MiniMax-M3.1-Flash"]}
    monkeypatch.setattr(server, "_list_models", lambda base_url, api_key: running.get(base_url))
    got = {p["id"]: p for p in client.get("/api/llm/providers").json()}
    assert got["magpie"]["running"] and got["magpie"]["recommended"] in got["magpie"]["models"]
    assert not got["ccswitch"]["running"] and "15721" in got["ccswitch"]["base_url"]
    assert not got["ollama"]["running"]
