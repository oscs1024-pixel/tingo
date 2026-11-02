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
    monkeypatch.setattr(
        sm,
        "_chat",
        lambda cfg, prompt, on_token=None, think=True, on_think=None: "```markdown\n# 周会\n\n## 一句话总结\n好\n```",
    )
    tr = _tr()
    tr.rename(["张三"])
    out = sm.summarize(tr, LLMConfig(api_key="x"), meeting_date=date(2026, 11, 2))
    assert out.startswith(
        "# 周会\n\n- 日期：2026-11-02（星期一）\n- 时长：01:30\n- 参会人：张三、说话人2\n\n## 一句话总结"
    )


def test_prompt_carries_date_and_transcript(monkeypatch):
    seen = {}

    def fake(cfg, prompt, on_token=None, think=True, on_think=None):
        seen["prompt"] = prompt
        return "# T"

    monkeypatch.setattr(sm, "_chat", fake)
    sm.summarize(_tr(), LLMConfig(api_key="x"), meeting_date=date(2026, 11, 6))
    assert "2026-11-06（星期五）" in seen["prompt"]
    assert "[00:01] 说话人2：第1句话。" in seen["prompt"]


def test_long_transcript_map_reduce(monkeypatch):
    prompts = []

    def fake(cfg, prompt, on_token=None, think=True, on_think=None):
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


def test_local_ollama_needs_no_key(monkeypatch):
    sm.is_ollama.cache_clear()
    monkeypatch.setattr(sm, "_ollama_show", lambda base_url, model: {"details": {"format": "gguf"}})
    cfg = LLMConfig(base_url="http://localhost:11434/v1", api_key="")
    assert sm._client(cfg).api_key == "ollama"
    assert sm.default_max_chars(cfg) < sm.default_max_chars(LLMConfig())
    sm.is_ollama.cache_clear()


def test_annotate_dates_resolves_relative_days():
    d = date(2026, 11, 2)  # 星期一
    assert sm.annotate_dates("下周五完成", d) == "下周五（11月13日）完成"
    assert sm.annotate_dates("下下周的周一合并", d) == "下下周的周一（11月16日）合并"
    assert sm.annotate_dates("这周三、下个星期二、下礼拜天", d) == (
        "这周三（11月4日）、下个星期二（11月10日）、下礼拜天（11月15日）"
    )
    assert sm.annotate_dates("明天和后天，大后天", d) == "明天（11月3日）和后天（11月4日），大后天（11月5日）"


def test_annotate_dates_leaves_ambiguous_and_annotated_text_alone():
    d = date(2026, 11, 2)
    assert sm.annotate_dates("周五再说，今天天气好", d) == "周五再说，今天天气好"
    assert sm.annotate_dates("下周五（11月13日）", d) == "下周五（11月13日）"


def test_annotate_dates_across_month_end():
    assert sm.annotate_dates("下周一", date(2026, 11, 29)) == "下周一（11月30日）"
    assert sm.annotate_dates("下下周一", date(2026, 11, 29)) == "下下周一（12月7日）"


def test_prompt_dates_are_annotated(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        sm,
        "_chat",
        lambda cfg, prompt, on_token=None, think=True, on_think=None: seen.setdefault("p", prompt) and "# T",
    )
    tr = Transcript("m.wav", 1000, [Sentence(0, 0, 900, "下周五交付。")])
    sm.summarize(tr, LLMConfig(api_key="x"), meeting_date=date(2026, 11, 2))
    assert "下周五（11月13日）交付" in seen["p"]
    assert "下周：周一 11月9日" in seen["p"]


def test_max_chars_follows_ollama_context(monkeypatch):
    import httpx

    class Resp:
        status_code = 200

        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    def serve(data):
        sm.is_ollama.cache_clear()
        monkeypatch.setattr(httpx, "post", lambda url, json, timeout: Resp(data))

    gguf = {"format": "gguf", "family": "qwen3"}
    local = LLMConfig(base_url="http://localhost:11434/v1", model="qwen3-8b-jiyao")
    serve({"parameters": "num_ctx    16384", "details": gguf})
    assert sm.ollama_context(local) == 16384
    assert sm.default_max_chars(local) == sm.LOCAL_CHUNK_CHARS  # 上下文够大也不切太大

    serve({"parameters": "", "details": gguf})
    assert sm.default_max_chars(local) == 4000  # 没设 num_ctx：按 Ollama 的保守默认

    # 本机中转连 /api/show 都仿了，但不是 gguf：按远程模型处理
    relay = LLMConfig(base_url="http://127.0.0.1:3425/v1", api_key="k", model="deepseek/deepseek-v4-flash")
    serve({"parameters": "", "details": {"format": "", "family": "magpie"}})
    assert not sm.is_ollama(relay.base_url, relay.model)
    assert sm.default_max_chars(relay) == 40000

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    sm.is_ollama.cache_clear()
    monkeypatch.setattr(httpx, "post", boom)
    assert sm.default_max_chars(local) == 40000  # 连不上就不当 Ollama
    assert sm.default_max_chars(LLMConfig(base_url="https://api.deepseek.com")) == 40000
    sm.is_ollama.cache_clear()


def _stream(monkeypatch, deltas):
    """假的流式客户端：每个 delta 是 ChoiceDelta 的字段（reasoning_content 等是供应商加的额外字段）"""
    from types import SimpleNamespace as NS

    from openai.types.chat.chat_completion_chunk import ChoiceDelta

    chunks = [NS(choices=[NS(delta=ChoiceDelta(**d))]) for d in deltas]
    client = NS(chat=NS(completions=NS(create=lambda **kw: iter(chunks))))
    monkeypatch.setattr(sm, "_client", lambda cfg: client)


def test_chat_reports_thinking_from_reasoning_fields(monkeypatch):
    _stream(
        monkeypatch,
        [
            {"role": "assistant", "content": None, "reasoning_content": "先看议题"},
            {"content": "", "reasoning_content": "再看待办"},
            {"reasoning": "OpenRouter 风格"},
            {"content": "# 标题"},
            {"content": "\n正文"},
        ],
    )
    thinking, tokens = [], []
    out = sm._chat(LLMConfig(api_key="x"), "p", tokens.append, on_think=thinking.append)
    assert thinking == [4, 8, 21]
    assert "".join(tokens) == out == "# 标题\n正文"


def test_chat_reports_thinking_inside_think_tag(monkeypatch):
    _stream(
        monkeypatch, [{"content": "<think>"}, {"content": "想一想"}, {"content": "</think>\n# T"}, {"content": "，好"}]
    )
    thinking, tokens = [], []
    out = sm._chat(LLMConfig(api_key="x"), "p", tokens.append, on_think=thinking.append)
    assert thinking == [7, 10]  # 正文出现前按 <think> 里累计的字数报
    assert out == "# T，好" and "".join(tokens) == out
    sm._chat(LLMConfig(api_key="x"), "p")  # 不传回调也能跑


def test_only_final_call_reports_thinking(monkeypatch):
    calls = []

    def fake(cfg, prompt, on_token=None, think=True, on_think=None):
        calls.append(on_think)
        if on_think:
            on_think(7)
        return "# 合并后" if "分段笔记" in prompt else "- 要点"

    monkeypatch.setattr(sm, "_chat", fake)
    seen = []
    sm.summarize(_tr(40), LLMConfig(api_key="x"), max_chars=100, on_think=seen.append)
    assert all(c is None for c in calls[:-1]) and calls[-1] is not None
    assert seen == [7]
