import pytest

from tingo import i18n


@pytest.fixture(autouse=True)
def zh_ui():
    """界面语言是进程里的全局状态：每个测试从中文开始，测完恢复。"""
    i18n.set_lang("zh")
    yield
    i18n.set_lang("zh")
