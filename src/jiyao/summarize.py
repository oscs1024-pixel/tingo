"""用任一 OpenAI 兼容接口把转写整理成中文会议纪要。"""

from __future__ import annotations

import functools
import re
import urllib.parse
from collections.abc import Callable
from datetime import date, timedelta

from .config import LLMConfig
from .transcript import Transcript, fmt_ts

SYSTEM = "你是一位严谨的会议秘书，擅长把口语化、带识别错误的会议转写整理成中国职场常用的会议纪要。"

MINUTES_PROMPT = """\
下面是一场会议的语音转写，格式为「[时间戳] 说话人：内容」。会议日期：{meeting_date}。

请整理成会议纪要，严格使用以下 Markdown 结构，不要输出其他内容：

# <根据内容拟一个会议标题>

## 一句话总结
<一句话>

## 议题与讨论
### 1. <议题>
- <要点>（[mm:ss]）

## 决议
- <已经拍板的事项>（[mm:ss]）

## 待办
| 事项 | 负责人 | 截止时间 | 出处 |
|---|---|---|---|
| <具体动作> | <人名或说话人> | <时间> | [mm:ss] |

## 待定问题与风险
- <没有结论的问题、提到的风险>

## 原因分析
```fishbone
{{"problem": "<被分析的问题，用问句>", "branches": [{{"title": "<一个原因，10 字以内>", "detail": "<一句话补充>", "refs": ["mm:ss", "mm:ss"]}}]}}
```

要求：
1. 只依据转写内容，不编造。负责人或截止时间没有明确说出的，写"未明确"。
2. 转写有同音错字，结合上下文改正明显的错误；拿不准的保留原文。
3. 转写里的"下周五""明天"等已由程序标好具体日期，如"下周五（11月13日）"，照抄括号里的日期，不要自己推算。其他相对时间（如"十五号"）保留原话，并查上面的日期对照表在括号里写出具体日期。
4. 每条要点、决议、待办都附上出处时间戳，便于回听核对。
5. 某一节没有内容时写"无"，不要删掉这一节（「原因分析」除外，见第 7 条）。
6. 去掉寒暄和口头禅，语言简洁、书面化。
7. 只有会议里明确分析了某个问题的原因时，才写「原因分析」这一节：每个问题一个 fishbone 代码块（最多两个），每块 2～6 个分支，内容是合法 JSON。refs 填支撑这个原因的原话的时间戳，必须是上面转写里出现过的 [mm:ss]（不带方括号）；找不到原话支撑就留空数组，不要编。没有分析原因的会议，整节省略，不要写"无"。

转写：
{transcript}
"""

CHUNK_PROMPT = """\
下面是一场长会议中的一段转写（第 {index}/{total} 段），格式为「[时间戳] 说话人：内容」。会议日期：{meeting_date}。

请提炼这一段的：讨论要点、已拍板的决议、待办（事项/负责人/截止时间）、待定问题与风险。
每条都附出处时间戳 [mm:ss]，只依据原文，不编造，用简洁的条目输出。

转写：
{transcript}
"""

REDUCE_PROMPT = """\
下面是一场长会议按时间顺序分段提炼的笔记。请把它们合并成一份完整的会议纪要，去重、按议题归并，保留出处时间戳。

{instructions}

分段笔记：
{notes}
"""


WEEKDAYS = "一二三四五六日"


def calendar_hint(d: date) -> str:
    """日期对照表。小模型算"下周五是几号"经常算错，直接给表让它查。"""

    def md(x: date) -> str:
        return f"{x.month}月{x.day}日"

    monday = d - timedelta(days=d.weekday())
    lines = [
        "日期对照表（每周从周一开始）：",
        f"- 今天 {md(d)}（星期{WEEKDAYS[d.weekday()]}），明天 {md(d + timedelta(1))}，后天 {md(d + timedelta(2))}",
    ]
    for name, offset in (("本周", 0), ("下周", 7), ("下下周", 14)):
        days = "、".join(f"周{WEEKDAYS[i]} {md(monday + timedelta(offset + i))}" for i in range(7))
        lines.append(f"- {name}：{days}")
    return "\n".join(lines)


_WEEK_OFFSET = {"下下": 14, "下": 7, "本": 0, "这": 0}
_DAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEK_RE = re.compile(r"(下下|下|本|这)个?(?:周|星期|礼拜)的?(?:周|星期|礼拜)?([一二三四五六日天])(?![（(])")
_DAY_RE = re.compile(r"(大后天|后天|明天)(?![（(])")
_DAY_OFFSET = {"明天": 1, "后天": 2, "大后天": 3}


def annotate_dates(text: str, d: date) -> str:
    """在"下周五""下下周的周一""明天"后面标上具体日期，换算由程序完成，不靠模型。"""
    monday = d - timedelta(days=d.weekday())

    def week(m: re.Match) -> str:
        x = monday + timedelta(_WEEK_OFFSET[m.group(1)] + _DAY_INDEX[m.group(2)])
        return f"{m.group(0)}（{x.month}月{x.day}日）"

    def day(m: re.Match) -> str:
        x = d + timedelta(_DAY_OFFSET[m.group(1)])
        return f"{m.group(0)}（{x.month}月{x.day}日）"

    return _DAY_RE.sub(day, _WEEK_RE.sub(week, text))


def _client(cfg: LLMConfig):
    from openai import OpenAI

    api_key = cfg.api_key or ("ollama" if _is_local(cfg) else "")
    if not api_key:
        raise RuntimeError("没有配置 LLM API key：运行 `jiyao config` 查看配置方法，或设置环境变量 JIYAO_LLM_API_KEY")
    return OpenAI(base_url=cfg.base_url, api_key=api_key)


def _is_local(cfg: LLMConfig) -> bool:
    return is_ollama(cfg.base_url, cfg.model)


@functools.lru_cache(maxsize=16)
def is_ollama(base_url: str, model: str) -> bool:
    """是不是本机 Ollama 上的这个模型。

    不能只看地址是不是 127.0.0.1：本机也可能跑着别的 OpenAI 兼容中转，有的还仿了 Ollama 的
    /api/version、/api/tags、/api/show。以 /api/show 里真实的模型文件格式为准。
    """
    if urllib.parse.urlparse(base_url).hostname not in ("localhost", "127.0.0.1", "::1"):
        return False
    return _ollama_show(base_url, model) is not None


def _ollama_show(base_url: str, model: str) -> dict | None:
    import httpx

    root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
    try:
        r = httpx.post(f"{root}/api/show", json={"model": model}, timeout=5)
        data = r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 - 不是 Ollama 或连不上
        return None
    # 有的中转连 /api/show 也仿了，但给不出真实的模型文件格式；Ollama 跑的本地模型是 gguf（或 safetensors 导入）
    fmt = (data or {}).get("details", {}).get("format") if isinstance(data, dict) else None
    return data if fmt in ("gguf", "safetensors") else None


CHARS_PER_TOKEN = 1.3  # 中文转写实测约 1.36 字/token，取保守值
RESERVED_TOKENS = 3500  # 提示词、模型思考和输出要留的位置
LOCAL_CHUNK_CHARS = 6000


def default_max_chars(cfg: LLMConfig) -> int:
    """一次交给模型的转写最多多少字，超过就分段提炼再合并。

    本地 Ollama 按模型实际的上下文长度（num_ctx）检查，上下文小就切得更碎：超过上下文时 Ollama 会悄悄截掉开头。
    远程模型普遍 64k 以上。
    """
    if not _is_local(cfg):
        return 40000
    ctx = ollama_context(cfg) or 4096  # 没显式设置 num_ctx 时按 Ollama 的保守默认值
    fits = int((ctx - RESERVED_TOKENS) * CHARS_PER_TOKEN)
    # 上下文够大也不超过 LOCAL_CHUNK_CHARS：实测 8B 模型一次读 1.3 万字，纪要里的待办从 14 条掉到 6 条
    return max(4000, min(LOCAL_CHUNK_CHARS, fits))


def ollama_context(cfg: LLMConfig) -> int | None:
    """问 Ollama 这个模型实际生效的上下文长度；问不到返回 None。"""
    data = _ollama_show(cfg.base_url, cfg.model) or {}
    m = re.search(r"num_ctx\s+(\d+)", data.get("parameters") or "")
    return int(m.group(1)) if m else None


def _chat(
    cfg: LLMConfig,
    prompt: str,
    on_token: Callable[[str], None] | None = None,
    think: bool = True,
    on_think: Callable[[int], None] | None = None,
) -> str:
    """流式调用 chat/completions，返回去掉思考过程的完整回答。

    on_think(已思考字数)：推理模型先长时间思考，不报出来界面就像卡住了。
    思考内容有的放在 reasoning_content / reasoning 字段，有的夹在正文的 <think> 里，两种都算。
    """
    extra: dict = {}
    if not think and _is_local(cfg):
        # Ollama 的 OpenAI 兼容接口用 reasoning_effort=none 关掉 Qwen3 等模型的思考；只对本地生效，远程接口未必认这个值
        extra["reasoning_effort"] = "none"
    stream = _client(cfg).chat.completions.create(
        model=cfg.model,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0.2,
        stream=True,
        extra_body=extra or None,
    )
    raw = ""
    shown = 0
    thought = 0
    for chunk in stream:
        if not chunk.choices:
            continue
        d = chunk.choices[0].delta
        reasoning = getattr(d, "reasoning_content", None)
        if not isinstance(reasoning, str):
            reasoning = getattr(d, "reasoning", None)
        if isinstance(reasoning, str) and reasoning:
            thought += len(reasoning)
            if on_think:
                on_think(thought)
        delta = d.content or ""
        if not delta:
            continue
        raw += delta
        visible = strip_think(raw, partial=True)
        if len(visible) > shown:
            if on_token:
                on_token(visible[shown:])
            shown = len(visible)
        elif shown == 0 and raw.lstrip().startswith("<think>") and on_think:
            on_think(thought + len(raw))
    return strip_think(raw).strip()


def strip_think(text: str, partial: bool = False) -> str:
    """去掉推理模型（Qwen3、DeepSeek-R1 等）输出里的 <think>…</think>。

    partial=True 用于流式输出：未闭合的 <think> 以及末尾半截标签先不显示。
    """
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    if partial:
        open_at = text.find("<think>")
        if open_at != -1:
            text = text[:open_at]
        for i in range(len("<think>") - 1, 0, -1):
            if text.endswith("<think>"[:i]):
                return text[:-i]
    return text


def split_chunks(lines: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if buf and size + len(line) > max_chars:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def summarize(
    transcript: Transcript,
    cfg: LLMConfig,
    meeting_date: date | None = None,
    max_chars: int | None = None,
    on_token: Callable[[str], None] | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_think: Callable[[int], None] | None = None,
) -> str:
    """on_think(已思考字数) 只报最终成文那一次调用的思考进度；分段提炼不报。"""
    meeting_date = meeting_date or date.today()
    day = f"{meeting_date.isoformat()}（星期{WEEKDAYS[meeting_date.weekday()]}）"
    dated = f"{day}\n\n{calendar_hint(meeting_date)}"
    max_chars = max_chars or default_max_chars(cfg)
    text = annotate_dates(transcript.to_prompt_text(), meeting_date)

    if len(text) <= max_chars:
        body = _chat(cfg, MINUTES_PROMPT.format(meeting_date=dated, transcript=text), on_token, on_think=on_think)
    else:
        chunks = split_chunks(text.splitlines(), max_chars)
        notes = []
        for i, chunk in enumerate(chunks, 1):
            if on_stage:
                on_stage(f"分段提炼 {i}/{len(chunks)}")
            notes.append(
                f"### 第 {i} 段\n"
                + _chat(
                    cfg,
                    CHUNK_PROMPT.format(index=i, total=len(chunks), meeting_date=dated, transcript=chunk),
                    think=False,  # 分段提炼主要是摘条目，不需要思考；最后合并时再思考
                )
            )
        if on_stage:
            on_stage("合并成纪要")
        instructions = MINUTES_PROMPT.split("转写：")[0].format(meeting_date=dated, transcript="")
        body = _chat(
            cfg,
            REDUCE_PROMPT.format(instructions=instructions, notes="\n\n".join(notes)),
            on_token,
            on_think=on_think,
        )

    return _with_meta(body, transcript, day)


def _with_meta(body: str, transcript: Transcript, day: str) -> str:
    """会议时间、时长、参会人由程序填，不让模型猜。"""
    body = _strip_fence(body)
    names = "、".join(transcript.name_of(s) for s in transcript.speakers_in_order()) or "无"
    meta = f"- 日期：{day}\n- 时长：{fmt_ts(transcript.duration_ms)}\n- 参会人：{names}"
    title, rest = "# 会议纪要", body
    first, _, remainder = body.partition("\n")
    if first.startswith("# "):
        title, rest = first, remainder
    return f"{title}\n\n{meta}\n\n{rest.strip()}\n"


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
