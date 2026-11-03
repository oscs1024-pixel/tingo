"""纪要末尾的术语表：```terms 代码块（JSON），列出会上专有名词的正确写法和转写里的听错写法。

推送、Obsidian、会议列表的一句话摘要里整块去掉；跨会议搜索用它扩展查询词。与桌面版一致。
"""

from __future__ import annotations

import json
import re

# 代码块不完整（模型输出被截断）时也去掉到文末
FENCE = re.compile(r"```terms\b.*?(?:\n```[ \t]*(?=\n|\Z)|\Z)", re.DOTALL)


def remove(md: str) -> str:
    """去掉所有 terms 代码块（一般在文末）；去掉后结尾的空行收掉，原来有结尾换行的保留一个。"""
    if "```terms" not in md:
        return md
    out = FENCE.sub("", md).rstrip()
    return out + "\n" if md.endswith("\n") else out


def parse(md: str) -> list[tuple[str, list[str]]]:
    """纪要里所有完整的 terms 代码块：[(正确写法, [听错写法…])]。格式不对的条目跳过。"""
    found: list[tuple[str, list[str]]] = []
    for m in re.finditer(r"```terms[^\n]*\n(.*?)\n```", md, re.DOTALL):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        items = data.get("terms") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("term"), str) or not item["term"].strip():
                continue
            variants = item.get("variants")
            variants = (
                [v.strip() for v in variants if isinstance(v, str) and v.strip()] if isinstance(variants, list) else []
            )
            found.append((item["term"].strip(), variants))
    return found
