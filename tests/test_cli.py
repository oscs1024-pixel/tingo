from pathlib import Path

import pytest
from typer.testing import CliRunner

from jiyao import summarize as sm
from jiyao.cli import app
from jiyao.transcript import Sentence, Transcript

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "meeting_zh.wav"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr("jiyao.config.CONFIG_PATH", tmp_path / "config.toml")
    for k in ("JIYAO_LLM_API_KEY", "JIYAO_LLM_BASE_URL", "JIYAO_LLM_MODEL", "DEEPSEEK_API_KEY", "JIYAO_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)


def _meeting(tmp_path):
    tr = Transcript("m.wav", 5000, [Sentence(0, 0, 1000, "开始。"), Sentence(1, 1000, 2000, "好。")])
    tr.save(tmp_path / "transcript.json")
    return tmp_path


def test_summarize_renames_speakers_and_writes_minutes(tmp_path, monkeypatch):
    monkeypatch.setenv("JIYAO_LLM_API_KEY", "x")
    monkeypatch.setattr(sm, "_chat", lambda cfg, prompt, on_token=None, think=True, on_think=None: "# 测试会")
    d = _meeting(tmp_path)
    result = runner.invoke(app, ["summarize", str(d), "-s", "张三，李四", "-d", "2026-11-02"])
    assert result.exit_code == 0, result.output
    assert "参会人：张三、李四" in (d / "纪要.md").read_text()
    assert "**李四**" in (d / "转写.md").read_text()
    assert Transcript.load(d / "transcript.json").speaker_names == {0: "张三", 1: "李四"}


def test_summarize_without_key_keeps_transcript_and_explains(tmp_path):
    d = _meeting(tmp_path)
    result = runner.invoke(app, ["summarize", str(d)])
    assert result.exit_code == 1
    assert not (d / "纪要.md").exists()


def test_push_without_webhook_fails(tmp_path):
    (tmp_path / "纪要.md").write_text("# x")
    result = runner.invoke(app, ["push", str(tmp_path)])
    assert result.exit_code == 1


def test_config_writes_template(tmp_path):
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert (tmp_path / "config.toml").read_text().startswith("# jiyao 配置")


@pytest.mark.slow
def test_run_end_to_end_transcribes_two_speakers(tmp_path):
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(FIXTURE), "-o", str(out), "--no-summary", "-w", "客服,提测"])
    assert result.exit_code == 0, result.output
    tr = Transcript.load(out / "transcript.json")
    assert len(tr.speakers_in_order()) == 2
    text = tr.to_prompt_text()
    assert "二十五号上线" in text
    # 换人处不应把下一位的开头算给上一位
    first_turn = tr.turns()[0].text
    assert first_turn.endswith("客服系统的迁移。")


def test_hotwords_read_from_top_level_and_legacy_llm_table(tmp_path):
    from jiyao import config

    config.CONFIG_PATH.write_text('hotwords = ["星河"]\n\n[llm]\nmodel = "m"\n')
    assert config.load().hotwords == ["星河"]
    config.CONFIG_PATH.write_text('[llm]\nmodel = "m"\nhotwords = ["旧版"]\n')
    assert config.load().hotwords == ["旧版"]
    config.CONFIG_PATH.unlink()
    config.write_example()
    import tomllib

    assert "hotwords" in tomllib.loads(config.CONFIG_PATH.read_text())  # 模板里的 hotwords 在顶层
