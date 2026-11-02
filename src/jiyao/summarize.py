"""用任一 OpenAI 兼容接口把转写整理成中文会议纪要。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

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

要求：
1. 只依据转写内容，不编造。负责人或截止时间没有明确说出的，写"未明确"。
2. 转写有同音错字，结合上下文改正明显的错误；拿不准的保留原文。
3. 相对时间（如"下周三""十五号"）保留原话，并按会议日期在括号里换算成具体日期，例如：下周三（11月11日）。
4. 每条要点、决议、待办都附上出处时间戳，便于回听核对。
5. 某一节没有内容时写"无"，不要删掉这一节。
6. 去掉寒暄和口头禅，语言简洁、书面化。

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


def _client(cfg: LLMConfig):
    from openai import OpenAI

    api_key = cfg.api_key or ("ollama" if _is_local(cfg) else "")
    if not api_key:
        raise RuntimeError("没有配置 LLM API key：运行 `jiyao config` 查看配置方法，或设置环境变量 JIYAO_LLM_API_KEY")
    return OpenAI(base_url=cfg.base_url, api_key=api_key)


def _is_local(cfg: LLMConfig) -> bool:
    return any(h in cfg.base_url for h in ("localhost", "127.0.0.1", ":11434"))


def default_max_chars(cfg: LLMConfig) -> int:
    # 本地模型上下文通常只有 4k～8k token，远程模型普遍 64k 以上
    return 6000 if _is_local(cfg) else 40000


def _chat(cfg: LLMConfig, prompt: str, on_token: Callable[[str], None] | None = None) -> str:
    stream = _client(cfg).chat.completions.create(
        model=cfg.model,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        temperature=0.2,
        stream=True,
    )
    raw = ""
    shown = 0
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content or ""
        if not delta:
            continue
        raw += delta
        if on_token:
            visible = strip_think(raw, partial=True)
            if len(visible) > shown:
                on_token(visible[shown:])
                shown = len(visible)
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
) -> str:
    meeting_date = meeting_date or date.today()
    day = f"{meeting_date.isoformat()}（星期{'一二三四五六日'[meeting_date.weekday()]}）"
    max_chars = max_chars or default_max_chars(cfg)
    text = transcript.to_prompt_text()

    if len(text) <= max_chars:
        body = _chat(cfg, MINUTES_PROMPT.format(meeting_date=day, transcript=text), on_token)
    else:
        chunks = split_chunks(text.splitlines(), max_chars)
        notes = []
        for i, chunk in enumerate(chunks, 1):
            if on_stage:
                on_stage(f"分段提炼 {i}/{len(chunks)}")
            notes.append(
                f"### 第 {i} 段\n"
                + _chat(cfg, CHUNK_PROMPT.format(index=i, total=len(chunks), meeting_date=day, transcript=chunk))
            )
        if on_stage:
            on_stage("合并成纪要")
        instructions = MINUTES_PROMPT.split("转写：")[0].format(meeting_date=day, transcript="")
        body = _chat(cfg, REDUCE_PROMPT.format(instructions=instructions, notes="\n\n".join(notes)), on_token)

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
