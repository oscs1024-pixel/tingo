"""运行日志：~/.cache/jiyao/jiyao.log，记录每个环节的耗时和出错信息，排查问题用。"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

LOG_PATH = Path(os.environ.get("JIYAO_LOG", Path.home() / ".cache" / "jiyao" / "jiyao.log"))


def get() -> logging.Logger:
    logger = logging.getLogger("jiyao")
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
