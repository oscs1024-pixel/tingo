"""配置：~/.config/jiyao/config.toml，环境变量优先。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("JIYAO_CONFIG", Path.home() / ".config" / "jiyao" / "config.toml"))

EXAMPLE = """\
# jiyao 配置。任一 OpenAI 兼容接口都可以：DeepSeek、通义千问、Kimi、智谱、本地 Ollama……

[llm]
base_url = "https://api.deepseek.com"
api_key = "sk-..."
model = "deepseek-chat"
# 本地 Ollama（不出本机）：
# base_url = "http://localhost:11434/v1"
# api_key = "ollama"
# model = "qwen2.5:7b"

# 专有名词、人名、项目代号，提高识别准确率
hotwords = []

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
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    hotwords: list[str] = field(default_factory=list)
    webhook: str = ""
    webhook_secret: str = ""


def load() -> Config:
    data: dict = {}
    if CONFIG_PATH.exists():
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    llm = data.get("llm", {})
    push = data.get("push", {})
    cfg = Config(
        llm=LLMConfig(
            base_url=llm.get("base_url", LLMConfig.base_url),
            api_key=llm.get("api_key", ""),
            model=llm.get("model", LLMConfig.model),
        ),
        hotwords=list(data.get("hotwords", [])),
        webhook=push.get("webhook", ""),
        webhook_secret=push.get("secret", ""),
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
