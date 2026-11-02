from datetime import date

import pytest

from jiyao import summarize as sm
from jiyao.config import LLMConfig
from jiyao.transcript import Sentence, Transcript


def _tr(n=2):
    return Transcript(
        source="m.wav",
        duration_ms=90_000,
        sentences=[Sentence(i % 2, i * 1000, i * 1000 + 900, f"第{i}句话。") for i in range(n)],
    )


def test_strip_think_closed_and_partial():
    assert sm.strip_think("<think>想一想</think>\n# 标题") == "# 标题"
    assert sm.strip_think("<think>还在想", partial=True) == ""
    assert sm.strip_think("# 标题<thi", partial=True) == "# 标题"
    assert sm.strip_think("# 标题<thi") == "# 标题<thi"


def test_split_chunks_respects_limit():
    lines = [f"line{i:02d}" for i in range(10)]  # 每行 6 字符
    chunks = sm.split_chunks(lines, max_chars=20)
    assert all(len(c) <= 21 for c in chunks)
    assert "\n".join(chunks).splitlines() == lines


def test_meta_filled_by_program(monkeypatch):
    monkeypatch.setattr(sm, "_chat", lambda cfg, prompt, on_token=None: "```markdown\n# 周会\n\n## 一句话总结\n好\n```")
    tr = _tr()
    tr.rename(["张三"])
    out = sm.summarize(tr, LLMConfig(api_key="x"), meeting_date=date(2026, 11, 2))
    assert out.startswith(
        "# 周会\n\n- 日期：2026-11-02（星期一）\n- 时长：01:30\n- 参会人：张三、说话人2\n\n## 一句话总结"
    )


def test_prompt_carries_date_and_transcript(monkeypatch):
    seen = {}

    def fake(cfg, prompt, on_token=None):
        seen["prompt"] = prompt
        return "# T"

    monkeypatch.setattr(sm, "_chat", fake)
    sm.summarize(_tr(), LLMConfig(api_key="x"), meeting_date=date(2026, 11, 6))
    assert "2026-11-06（星期五）" in seen["prompt"]
    assert "[00:01] 说话人2：第1句话。" in seen["prompt"]


def test_long_transcript_map_reduce(monkeypatch):
    prompts = []

    def fake(cfg, prompt, on_token=None):
        prompts.append(prompt)
        return "# 合并后" if "分段笔记" in prompt else "- 要点"

    monkeypatch.setattr(sm, "_chat", fake)
    stages = []
    out = sm.summarize(_tr(40), LLMConfig(api_key="x"), max_chars=100, on_stage=stages.append)
    assert len(prompts) > 2
    assert stages[-1] == "合并成纪要"
    assert "分段笔记" in prompts[-1] and "## 待办" in prompts[-1]
    assert out.startswith("# 合并后")


def test_missing_key_for_remote_raises():
    with pytest.raises(RuntimeError, match="API key"):
        sm._client(LLMConfig(base_url="https://api.deepseek.com", api_key=""))


def test_local_ollama_needs_no_key():
    cfg = LLMConfig(base_url="http://localhost:11434/v1", api_key="")
    assert sm._client(cfg).api_key == "ollama"
    assert sm.default_max_chars(cfg) < sm.default_max_chars(LLMConfig())
