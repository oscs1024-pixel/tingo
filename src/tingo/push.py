"""把纪要推送到飞书 / 钉钉 / 企业微信群机器人。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
import urllib.parse

import httpx

from . import terms
from .i18n import doc_is_en, tr


def detect(webhook: str) -> str:
    host = urllib.parse.urlparse(webhook).netloc
    if "feishu" in host or "larksuite" in host:
        return "feishu"
    if "dingtalk" in host:
        return "dingtalk"
    if "weixin.qq.com" in host:
        return "wecom"
    raise ValueError(
        tr(
            f"认不出这个 webhook 属于哪个平台：{host}（支持飞书、钉钉、企业微信）",
            f"Unrecognized webhook platform: {host} (Feishu/Lark, DingTalk and WeCom are supported)",
        )
    )


def tables_to_lists(md: str, en: bool | None = None) -> str:
    """钉钉、企业微信的 markdown 不支持表格，把表格转成列表。en 不给时按纪要的语言。"""
    en = doc_is_en(md) if en is None else en
    out: list[str] = []
    header: list[str] | None = None
    for line in md.splitlines():
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
            elif all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue
            else:
                first, *rest = cells
                if en:
                    extra = ", ".join(f"{h}: {c}" for h, c in zip(header[1:], rest) if c)
                    out.append(f"- {first}" + (f" ({extra})" if extra else ""))
                else:
                    extra = "，".join(f"{h}：{c}" for h, c in zip(header[1:], rest) if c)
                    out.append(f"- {first}" + (f"（{extra}）" if extra else ""))
        else:
            header = None
            out.append(line)
    return "\n".join(out)


def fishbone_to_text(md: str, en: bool | None = None) -> str:
    """纪要里的鱼骨图是 ```fishbone 代码块（JSON），群消息里换成可读的列表。en 不给时按纪要的语言。"""
    import json

    en = doc_is_en(md) if en is None else en

    def repl(m: re.Match) -> str:
        try:
            fb = json.loads(m.group(1))
        except ValueError:
            return ""
        if en:
            lines = [f"**{fb.get('problem', 'Root Cause Analysis')}**"]
            for b in fb.get("branches", []):
                refs = ", ".join(b.get("refs") or [])
                detail = f": {b['detail']}" if b.get("detail") else ""
                lines.append(f"- {b.get('title', '')}{detail}" + (f" ({refs})" if refs else " (no evidence)"))
            return "\n".join(lines)
        lines = [f"**{fb.get('problem', '原因分析')}**"]
        for b in fb.get("branches", []):
            refs = "、".join(b.get("refs") or [])
            detail = f"：{b['detail']}" if b.get("detail") else ""
            lines.append(f"- {b.get('title', '')}{detail}" + (f"（{refs}）" if refs else "（无原文依据）"))
        return "\n".join(lines)

    return re.sub(r"```fishbone\s*\n(.*?)\n```", repl, md, flags=re.DOTALL)


def _truncate(text: str, max_bytes: int, en: bool = False) -> str:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[: max_bytes - 40].decode("utf-8", errors="ignore") + (
        "\n\n…(truncated)" if en else "\n\n……（内容过长已截断）"
    )


def _title(md: str, en: bool = False) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "Meeting Minutes" if en else "会议纪要"


def build_request(webhook: str, minutes_md: str, secret: str = "", now: float | None = None) -> tuple[str, dict]:
    """返回 (url, json body)。拆出来是为了不发请求也能测。"""
    now = now if now is not None else time.time()
    platform = detect(webhook)
    minutes_md = terms.remove(minutes_md)  # 术语表只给搜索用，不进群消息
    en = doc_is_en(minutes_md)  # 卡片标题、截断提示跟纪要的语言走
    title = _title(minutes_md, en)
    minutes_md = fishbone_to_text(minutes_md, en)

    if platform == "feishu":
        body: dict = {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
                "body": {"elements": [{"tag": "markdown", "content": _truncate(minutes_md, 25000, en)}]},
            },
        }
        if secret:
            ts = str(int(now))
            sign = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
            body.update(timestamp=ts, sign=base64.b64encode(sign).decode())
        return webhook, body

    text = tables_to_lists(minutes_md, en)
    if platform == "dingtalk":
        url = webhook
        if secret:
            ts = str(int(now * 1000))
            sign = hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
            sep = "&" if "?" in webhook else "?"
            url = f"{webhook}{sep}timestamp={ts}&sign={urllib.parse.quote_plus(base64.b64encode(sign).decode())}"
        return url, {"msgtype": "markdown", "markdown": {"title": title, "text": _truncate(text, 18000, en)}}

    return webhook, {"msgtype": "markdown", "markdown": {"content": _truncate(text, 4000, en)}}


def send(webhook: str, minutes_md: str, secret: str = "") -> None:
    url, body = build_request(webhook, minutes_md, secret)
    resp = httpx.post(url, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code", data.get("errcode", 0))
    if code not in (0, None):
        raise RuntimeError(tr(f"推送失败：{data}", f"Push failed: {data}"))
