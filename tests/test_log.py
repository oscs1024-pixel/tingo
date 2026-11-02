import logging


def test_log_writes_file_and_survives_funasr_quiet(tmp_path, monkeypatch):
    from jiyao import asr, log

    monkeypatch.setattr(log, "LOG_PATH", tmp_path / "jiyao.log")
    logger = logging.getLogger("jiyao")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    lg = log.get()
    with asr._quiet():  # 压 FunASR 日志时，jiyao 自己的日志照常写
        lg.info("录音结束后转写 3.2 秒")
    for h in lg.handlers:
        h.flush()
    assert "录音结束后转写 3.2 秒" in (tmp_path / "jiyao.log").read_text(encoding="utf-8")
    assert logging.getLogger().level != logging.ERROR  # 退出后根 logger 级别已恢复
    for h in list(logger.handlers):
        logger.removeHandler(h)
