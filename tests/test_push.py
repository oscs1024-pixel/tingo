import base64
import hashlib
import hmac
import urllib.parse

import pytest

from tingo.push import build_request, detect, tables_to_lists

MINUTES = """# 周会

## 待办
| 事项 | 负责人 | 截止时间 | 出处 |
|---|---|---|---|
| 修复支付模块 | 小王 | 11月15日 | [00:29] |
| 出迁移方案 | 小张 |  | [00:40] |

## 风险
- 无
"""


def test_detect():
    assert detect("https://open.feishu.cn/open-apis/bot/v2/hook/abc") == "feishu"
    assert detect("https://open.larksuite.com/open-apis/bot/v2/hook/abc") == "feishu"
    assert detect("https://oapi.dingtalk.com/robot/send?access_token=x") == "dingtalk"
    assert detect("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x") == "wecom"
    with pytest.raises(ValueError):
        detect("https://example.com/hook")


def test_tables_to_lists():
    out = tables_to_lists(MINUTES)
    assert "|" not in out
    assert "- 修复支付模块（负责人：小王，截止时间：11月15日，出处：[00:29]）" in out
    assert "- 出迁移方案（负责人：小张，出处：[00:40]）" in out
    assert "## 风险\n- 无" in out


def test_feishu_card_and_sign():
    _, body = build_request("https://open.feishu.cn/open-apis/bot/v2/hook/abc", MINUTES, secret="s3", now=1700000000)
    assert body["card"]["header"]["title"]["content"] == "周会"
    assert body["card"]["body"]["elements"][0]["content"] == MINUTES
    expected = base64.b64encode(hmac.new(b"1700000000\ns3", b"", hashlib.sha256).digest()).decode()
    assert (body["timestamp"], body["sign"]) == ("1700000000", expected)


def test_dingtalk_markdown_and_sign():
    hook = "https://oapi.dingtalk.com/robot/send?access_token=x"
    url, body = build_request(hook, MINUTES, secret="s3", now=1700000000.5)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["timestamp"] == ["1700000000500"]
    expected = base64.b64encode(hmac.new(b"s3", b"1700000000500\ns3", hashlib.sha256).digest()).decode()
    assert q["sign"] == [expected]
    assert body["markdown"]["title"] == "周会"
    assert "|" not in body["markdown"]["text"]


def test_wecom_truncates_to_4096_bytes():
    _, body = build_request("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "# 长\n" + "字" * 3000)
    content = body["markdown"]["content"]
    assert len(content.encode()) <= 4096
    assert content.endswith("（内容过长已截断）")


def test_fishbone_becomes_list():
    from tingo.push import fishbone_to_text

    md = '## 原因分析\n```fishbone\n{"problem": "为什么慢？", "branches": [{"title": "页面大", "detail": "6 秒", "refs": ["01:06"]}, {"title": "猜的", "refs": []}]}\n```\n'
    out = fishbone_to_text(md)
    assert "**为什么慢？**\n- 页面大：6 秒（01:06）\n- 猜的（无原文依据）" in out
    assert "```" not in out


EN_MINUTES = """# Weekly sync

## Summary
Fine.

## Action Items
| Item | Owner | Due | Source |
|---|---|---|---|
| Fix payments | Lin | Nov 13 | [00:29] |
| Draft plan | Chen |  | [00:40] |

## Root Cause Analysis
```fishbone
{"problem": "Why slow?", "branches": [{"title": "Big page", "detail": "6 s", "refs": ["01:06", "01:10"]}, {"title": "Guess", "refs": []}]}
```
"""


def test_english_minutes_follow_doc_language():
    from tingo.push import fishbone_to_text

    out = fishbone_to_text(EN_MINUTES)
    assert "**Why slow?**\n- Big page: 6 s (01:06, 01:10)\n- Guess (no evidence)" in out
    lists = tables_to_lists(EN_MINUTES)
    assert "- Fix payments (Owner: Lin, Due: Nov 13, Source: [00:29])" in lists
    assert "- Draft plan (Owner: Chen, Source: [00:40])" in lists
    _, body = build_request("https://oapi.dingtalk.com/robot/send?access_token=x", EN_MINUTES)
    assert body["markdown"]["title"] == "Weekly sync" and "(no evidence)" in body["markdown"]["text"]
    _, body = build_request("https://open.feishu.cn/open-apis/bot/v2/hook/abc", "## Summary\nx")
    assert body["card"]["header"]["title"]["content"] == "Meeting Minutes"


def test_english_truncation_note():
    _, body = build_request("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "## Summary\n" + "word " * 2000)
    content = body["markdown"]["content"]
    assert len(content.encode()) <= 4096 and content.endswith("\n\n…(truncated)")


def test_detect_error_follows_ui_language():
    from tingo import i18n

    i18n.set_lang("en")
    with pytest.raises(ValueError, match="Unrecognized webhook platform: example.com"):
        detect("https://example.com/hook")
