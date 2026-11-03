import tomllib

import pytest

from tingo import config
from tingo.config import Config, LLMConfig, ObsidianConfig

# 桌面版（app/engine/src/config.rs 的 save）写出的文件
RUST_SAVED = (
    "# tingo 配置，由图形界面写入；也可以直接编辑。\n\n# 专有名词、人名、项目代号，提高识别准确率\n"
    'hotwords = ["星河", "Kafka"]\n\n[llm]\nbase_url = "http://127.0.0.1:3425/v1"\napi_key = "k\\"q"\n'
    'model = "deepseek/deepseek-v4-flash"\n\n[push]\nwebhook = ""\nsecret = ""\n\n'
    '[obsidian]\nvault = "/Users/a/笔记"\nfolder = "会议"\nauto = false\ntranscript = true\n'
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    for k in ("TINGO_LLM_API_KEY", "TINGO_LLM_BASE_URL", "TINGO_LLM_MODEL", "DEEPSEEK_API_KEY", "TINGO_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)


def test_roundtrip_with_obsidian_matches_desktop_format():
    cfg = Config(
        llm=LLMConfig("http://127.0.0.1:3425/v1", 'k"q', "deepseek/deepseek-v4-flash"),
        hotwords=["星河", "Kafka"],
        obsidian=ObsidianConfig("/Users/a/笔记", "会议", False, True),
    )
    config.save(cfg)
    assert config.CONFIG_PATH.read_text(encoding="utf-8") == RUST_SAVED  # 两个版本写出的文件逐字相同
    assert config.load() == cfg
    config.CONFIG_PATH.write_text(RUST_SAVED, encoding="utf-8")
    assert config.load() == cfg


def test_obsidian_defaults_when_section_missing():
    config.CONFIG_PATH.write_text('hotwords = ["青鸾"]\n\n[llm]\nmodel = "m"\n\n[push]\nwebhook = ""\nsecret = ""\n')
    assert config.load().obsidian == ObsidianConfig("", "会议纪要", True, True)
    config.CONFIG_PATH.write_text('[obsidian]\nvault = "/v"\n')
    assert config.load().obsidian == ObsidianConfig("/v", "会议纪要", True, True)
    assert "obsidian" not in tomllib.loads(config.EXAMPLE)
