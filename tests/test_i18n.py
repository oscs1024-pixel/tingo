from tingo import i18n


def test_tr_follows_set_lang():
    assert i18n.tr("中", "en") == "中" and not i18n.is_en()
    i18n.set_lang("en-US")
    assert i18n.tr("中", "en") == "en" and i18n.is_en()
    i18n.set_lang("fr")  # 认不出的值不改
    assert i18n.lang() == "en"
    i18n.set_lang(" ZH ")
    assert i18n.lang() == "zh"


def test_detect_by_cjk_ratio():
    assert i18n.detect("下周五上线。") == "zh"
    assert i18n.detect("Let's ship it next Friday.") == "en"
    assert i18n.detect("") == "zh" and i18n.detect("123 ...") == "zh"
    # 汉字 3 个、字母 7 个：比例正好 0.3 算中文；字母再多一个就算英文
    assert i18n.detect("上线了 abcdefg") == "zh"
    assert i18n.detect("上线了 abcdefgh") == "en"


def test_doc_is_en():
    assert i18n.doc_is_en("# T\n\n## Summary\nok\n")
    assert not i18n.doc_is_en("# T\n\n## 一句话总结\n好\n")
    assert not i18n.doc_is_en("# T\n\n## Summary of things\n")
