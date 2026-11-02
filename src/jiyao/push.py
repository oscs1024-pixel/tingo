"""把纪要推送到飞书 / 钉钉 / 企业微信群机器人。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
import urllib.parse

import httpx


def detect(webhook: str) -> str:
    host = urllib.parse.urlparse(webhook).netloc
    if "feishu" in host or "larksuite" in host:
        return "feishu"
    if "dingtalk" in host:
        return "dingtalk"
    if "weixin.qq.com" in host:
        return "wecom"
    raise ValueError(f"认不出这个 webhook 属于哪个平台：{host}（支持飞书、钉钉、企业微信）")


def tables_to_lists(md: str) -> str:
    """钉钉、企业微信的 markdown 不支持表格，把表格转成列表。"""
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
                extra = "，".join(f"{h}：{c}" for h, c in zip(header[1:], rest) if c)
                out.append(f"- {first}" + (f"（{extra}）" if extra else ""))
        else:
            header = None
            out.append(line)
    return "\n".join(out)


def _truncate(text: str, max_bytes: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return data[: max_bytes - 40].decode("utf-8", errors="ignore") + "\n\n……（内容过长已截断）"


def _title(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "会议纪要"


def build_request(webhook: str, minutes_md: str, secret: str = "", now: float | None = None) -> tuple[str, dict]:
    """返回 (url, json body)。拆出来是为了不发请求也能测。"""
    now = now if now is not None else time.time()
    platform = detect(webhook)
    title = _title(minutes_md)

    if platform == "feishu":
        body: dict = {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
                "body": {"elements": [{"tag": "markdown", "content": _truncate(minutes_md, 25000)}]},
            },
        }
        if secret:
            ts = str(int(now))
            sign = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
            body.update(timestamp=ts, sign=base64.b64encode(sign).decode())
        return webhook, body

    text = tables_to_lists(minutes_md)
    if platform == "dingtalk":
        url = webhook
        if secret:
            ts = str(int(now * 1000))
            sign = hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
            sep = "&" if "?" in webhook else "?"
            url = f"{webhook}{sep}timestamp={ts}&sign={urllib.parse.quote_plus(base64.b64encode(sign).decode())}"
        return url, {"msgtype": "markdown", "markdown": {"title": title, "text": _truncate(text, 18000)}}

    return webhook, {"msgtype": "markdown", "markdown": {"content": _truncate(text, 4000)}}


def send(webhook: str, minutes_md: str, secret: str = "") -> None:
    url, body = build_request(webhook, minutes_md, secret)
    resp = httpx.post(url, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    code = data.get("code", data.get("errcode", 0))
    if code not in (0, None):
        raise RuntimeError(f"推送失败：{data}")
