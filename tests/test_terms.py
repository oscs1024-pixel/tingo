import json

from tingo import meetings, obsidian, terms
from tingo.config import ObsidianConfig
from tingo.meetings import MINUTES, TRANSCRIPT_JSON
from tingo.push import build_request

TERMS = '```terms\n{"terms": [{"term": "历史工单", "variants": ["历史中单", " "]}, {"term": "Kafka"}, {"x": 1}]}\n```'
MINUTES_TEXT = (
    "# 周会\n\n## 一句话总结\n定了上线时间。\n\n## 原因分析\n```fishbone\n"
    '{"problem": "为什么延期？", "branches": [{"title": "上线定了", "refs": ["00:00"]}]}'
    "\n```\n\n" + TERMS + "\n"
)


def test_remove_complete_and_unfinished_blocks():
    assert terms.remove(MINUTES_TEXT).endswith('"refs": ["00:00"]}]}\n```\n')
    assert "```terms" not in terms.remove(MINUTES_TEXT)
    # 输出被截断、代码块没收尾：去掉到文末
    assert terms.remove('# 周会\n\n正文\n\n```terms\n{"terms": [{"te') == "# 周会\n\n正文"
    assert terms.remove("# 周会\n\n```terms") == "# 周会"
    # 没有 terms 块的原样返回，别的代码块不动
    assert terms.remove("# 周会\n```fishbone\n{}\n```\n") == "# 周会\n```fishbone\n{}\n```\n"
    # 在中间的也去掉
    assert terms.remove(f"a\n{TERMS}\nb\n") == "a\n\nb\n"


def test_parse_skips_bad_entries():
    assert terms.parse(MINUTES_TEXT) == [("历史工单", ["历史中单"]), ("Kafka", [])]
    assert terms.parse("```terms\nnot json\n```") == []
    assert terms.parse('```terms\n{"terms": {}}\n```') == []
    assert terms.parse('```terms\n{"terms": []}') == []  # 不完整的不解析


def test_one_liner_ignores_terms():
    assert meetings.one_liner(MINUTES_TEXT) == "定了上线时间。"
    assert meetings.one_liner("# 周会\n\n## 一句话总结\n\n" + TERMS) == ""


def test_push_drops_terms():
    for url in ("https://open.feishu.cn/open-apis/bot/v2/hook/x", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"):
        _, body = build_request(url, MINUTES_TEXT)
        text = json.dumps(body, ensure_ascii=False)
        assert "terms" not in text and "历史中单" not in text and "上线定了" in text


def test_obsidian_drops_terms(tmp_path):
    d = tmp_path / "meetings" / "2026-11-02_1030_周会"
    d.mkdir(parents=True)
    (d / MINUTES).write_text(MINUTES_TEXT, encoding="utf-8")
    (d / TRANSCRIPT_JSON).write_text(
        json.dumps(
            {
                "source": "a.wav",
                "duration_ms": 1000,
                "sentences": [{"speaker": 0, "start_ms": 0, "end_ms": 900, "text": "好。"}],
            }
        ),
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    out = obsidian.export(ObsidianConfig(str(vault), "会议纪要", True, False), d)
    note = out.note.read_text(encoding="utf-8")
    assert "terms" not in note and "历史中单" not in note and "aliases" not in note
    assert "- **上线定了**" in note and note.endswith("\n") and not note.endswith("\n\n")
    assert len(out.drawings) == 1
    assert "历史中单" not in out.drawings[0].read_text(encoding="utf-8")
