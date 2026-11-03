"""界面语言（中文 / 英文）。

单用户本地应用：前端每个请求都带 X-Tingo-Lang，记下最近一次的值，后端给用户看的文字按它选中英文。
日志不翻译。纪要、推送、Obsidian 里的文字跟纪要本身的语言走（见 doc_is_en），不看界面语言。
与桌面版（app/engine/src/i18n.rs）一致。
"""

from __future__ import annotations

import re

HEADER = "X-Tingo-Lang"

_lang = "zh"


def set_lang(value: str | None) -> None:
    """zh / en（也认 zh-CN、en-US 这类写法）；认不出的值不改。"""
    global _lang
    v = (value or "").strip().lower()
    if v.startswith("en"):
        _lang = "en"
    elif v.startswith("zh"):
        _lang = "zh"


def lang() -> str:
    return _lang


def is_en() -> bool:
    return _lang == "en"


def tr(zh: str, en: str) -> str:
    """按当前界面语言二选一。带参数的两边各写一个 f-string。"""
    return en if _lang == "en" else zh


_EN_DOC = re.compile(r"^## Summary\s*$", re.MULTILINE)


def doc_is_en(md: str) -> bool:
    """纪要是不是英文的：有 `## Summary` 这一节就当英文，否则中文。"""
    return _EN_DOC.search(md) is not None


_CJK = re.compile(r"[㐀-䶿一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")
CJK_RATIO = 0.3


def detect(text: str) -> str:
    """文字是中文还是英文：汉字占"汉字 + 拉丁字母"的比例 ≥ 0.3 算中文；两样都没有也算中文。"""
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN.findall(text))
    if cjk + latin == 0:
        return "zh"
    return "zh" if cjk / (cjk + latin) >= CJK_RATIO else "en"
