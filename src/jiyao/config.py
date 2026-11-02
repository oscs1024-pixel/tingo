"""配置：~/.config/jiyao/config.toml，环境变量优先。"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("JIYAO_CONFIG", Path.home() / ".config" / "jiyao" / "config.toml"))

EXAMPLE = """\
# jiyao 配置。任一 OpenAI 兼容接口都可以：DeepSeek、通义千问、Kimi、智谱、本地 Ollama……

# 专有名词、人名、项目代号，提高识别准确率（必须写在下面的 [llm] 之前，否则会被当成 llm 的配置）
hotwords = []

[llm]
base_url = "https://api.deepseek.com"
api_key = "sk-..."
model = "deepseek-chat"
# 本地 Ollama（不出本机）：
# base_url = "http://localhost:11434/v1"
# api_key = "ollama"
# model = "qwen2.5:7b"

[push]
# 飞书 / 钉钉 / 企业微信 群机器人 webhook，按域名自动识别
webhook = ""
# 机器人开启了"加签"时填
secret = ""
"""


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"


@dataclass
class ObsidianConfig:
    """存进 Obsidian：库的路径、库里的文件夹、生成纪要后是否自动存、是否附带转写全文"""

    vault: str = ""
    folder: str = "会议纪要"
    auto: bool = True
    transcript: bool = True


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    hotwords: list[str] = field(default_factory=list)
    webhook: str = ""
    webhook_secret: str = ""
    obsidian: ObsidianConfig = field(default_factory=ObsidianConfig)


def load() -> Config:
    data: dict = {}
    if CONFIG_PATH.exists():
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    llm = data.get("llm", {})
    push = data.get("push", {})
    obs = data.get("obsidian", {})
    cfg = Config(
        llm=LLMConfig(
            base_url=llm.get("base_url", LLMConfig.base_url),
            api_key=llm.get("api_key", ""),
            model=llm.get("model", LLMConfig.model),
        ),
        hotwords=list(data.get("hotwords", llm.get("hotwords", []))),  # 早期模板误把 hotwords 写在 [llm] 下
        webhook=push.get("webhook", ""),
        webhook_secret=push.get("secret", ""),
        obsidian=ObsidianConfig(
            vault=obs.get("vault", ObsidianConfig.vault),
            folder=obs.get("folder", ObsidianConfig.folder),
            auto=obs.get("auto", ObsidianConfig.auto),
            transcript=obs.get("transcript", ObsidianConfig.transcript),
        ),
    )

    env = os.environ
    cfg.llm.base_url = env.get("JIYAO_LLM_BASE_URL", cfg.llm.base_url)
    cfg.llm.model = env.get("JIYAO_LLM_MODEL", cfg.llm.model)
    cfg.llm.api_key = env.get("JIYAO_LLM_API_KEY") or cfg.llm.api_key or env.get("DEEPSEEK_API_KEY", "")
    cfg.webhook = env.get("JIYAO_WEBHOOK", cfg.webhook)
    cfg.webhook_secret = env.get("JIYAO_WEBHOOK_SECRET", cfg.webhook_secret)
    return cfg


def write_example() -> Path:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(EXAMPLE, encoding="utf-8")
    return CONFIG_PATH


def _toml_str(s: str) -> str:
    # JSON 字符串的转义规则是 TOML 基本字符串的子集
    return json.dumps(s, ensure_ascii=False)


def _toml_bool(b: bool) -> str:
    return "true" if b else "false"


def save(cfg: Config) -> Path:
    """写回配置文件（图形界面的设置页用）。注释不保留，格式与桌面版（Rust）的 save 一致。"""
    words = ", ".join(_toml_str(w) for w in cfg.hotwords)
    text = (
        "# jiyao 配置，由图形界面写入；也可以直接编辑。\n\n"
        "# 专有名词、人名、项目代号，提高识别准确率\n"
        f"hotwords = [{words}]\n\n"
        "[llm]\n"
        f"base_url = {_toml_str(cfg.llm.base_url)}\n"
        f"api_key = {_toml_str(cfg.llm.api_key)}\n"
        f"model = {_toml_str(cfg.llm.model)}\n\n"
        "[push]\n"
        f"webhook = {_toml_str(cfg.webhook)}\n"
        f"secret = {_toml_str(cfg.webhook_secret)}\n\n"
        "[obsidian]\n"
        f"vault = {_toml_str(cfg.obsidian.vault)}\n"
        f"folder = {_toml_str(cfg.obsidian.folder)}\n"
        f"auto = {_toml_bool(cfg.obsidian.auto)}\n"
        f"transcript = {_toml_bool(cfg.obsidian.transcript)}\n"
    )
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(text, encoding="utf-8")
    CONFIG_PATH.chmod(0o600)  # 里面有 API key
    return CONFIG_PATH
