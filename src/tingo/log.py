"""运行日志：~/.cache/tingo/tingo.log，记录每个环节的耗时和出错信息，排查问题用。"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import env_var

LOG_PATH = Path(env_var("LOG") or Path.home() / ".cache" / "tingo" / "tingo.log")


def get() -> logging.Logger:
    logger = logging.getLogger("tingo")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.propagate = False  # 不经过根 logger，FunASR 调根 logger 级别时不受影响
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.handlers.RotatingFileHandler(
                LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        except OSError:
            handler = logging.NullHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger
