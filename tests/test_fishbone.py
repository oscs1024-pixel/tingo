from tingo import fishbone as fb
from tingo.fishbone import NONE, OK, WEAK, Branch
from tingo.transcript import Turn


def turns():
    return [
        Turn("李经理", 0, 8000, "大家好，今天讨论发布。"),
        Turn("小张", 9000, 18000, "支付模块还有两个问题没修，十五号才能提测。"),
        Turn("李经理", 48000, 52000, "行，散会。"),
    ]


def test_grounding_matches_frontend_rules():
    t = turns()
    g = fb.ground(Branch("t", refs=["00:09", "[00:12]", "00:09"]), t, 52000)
    assert (len(g.quotes), g.level) == (1, OK)  # 同一段只算一次
    assert g.label() == "引用 1 段 · 21 字"
    weak = fb.ground(Branch("t", refs=["00:48"]), t, 52000)
    assert weak.level == WEAK and weak.label() == "依据很少 · 引用 1 段 · 5 字"
    assert fb.ground(Branch("t", refs=["05:12"]), t, 52000).level == NONE  # 超出会议时长
    assert fb.ground(Branch("t", refs=[]), t, 52000).level == NONE
    assert fb.ground(Branch("t", refs=["1:00:00"]), t, 52000).level == NONE
    assert fb.ground(Branch("t", refs=["abc", "00:x9", " 00:5 "]), t, 52000).quotes[0].ms == 0
    assert fb.ground(Branch("t", refs=["00:12", "00:00"]), t, 52000).quotes[0].ms == 0  # 按时间排


def test_parse_is_strict_like_desktop():
    assert fb.parse('{"problem": "p", "branches": [{"title": "a", "refs": [1]}]}') is None
    assert fb.parse('{"problem": "p"}') is None
    assert fb.parse("不是 JSON") is None
    s = fb.parse('{"problem": "p", "branches": [{"title": "a", "x": 1}, {"detail": "d"}]}')
    assert [b.title for b in s.branches] == ["a"]


def test_excalidraw_has_spine_head_and_branches():
    md = (
        "## 原因分析\n```fishbone\n"
        '{"problem": "为什么延期？", "branches": [{"title": "支付有问题", "refs": ["00:09"]}, '
        '{"title": "猜的", "refs": []}, {"title": ""}]}\n```\n'
    )
    bl = fb.blocks(md)
    assert md[bl[0][0][0] : bl[0][0][1]].startswith("```fishbone")
    spec = bl[0][1]
    assert len(spec.branches) == 2  # 空标题的分支丢掉
    g = [fb.ground(b, turns(), 52000) for b in spec.branches]
    d = fb.excalidraw(spec, g)
    els = d["elements"]
    texts = [e["text"] for e in els if "text" in e]
    assert "为什么延期？" in texts
    assert "无原文依据" not in texts
    assert not any(t.startswith("00:09 小张：") for t in texts)
    # 无依据的分支画红色
    assert sum(e["strokeColor"] == "#e03131" for e in els) >= 2
    assert len({e["id"] for e in els}) == len(els)
    # 布局：先画分支（斜骨 + 文字），再画主骨、鱼头
    assert [e["type"] for e in els[-3:]] == ["line", "rectangle", "text"]
    spine = els[-3]
    assert (spine["width"], spine["strokeWidth"], spine["points"]) == (340.0, 4.0, [[0, 0], [340.0, 0.0]])
    assert els[0]["points"] == [[0, 0], [110.0, 150.0]] and els[0]["x"] == 120.0  # 第一个分支在上方
    assert els[-2]["backgroundColor"] == "#e7f5ff"
    assert els[0]["id"] == "jy001eef" and els[0]["seed"] == 107919
    md = fb.excalidraw_md(d)
    assert "excalidraw-plugin: parsed" in md and "为什么延期？ ^jy" in md
    assert '"appState": {\n    "gridSize": null,' in md  # 键按字母序，和桌面版一致


def test_wrap_and_lines():
    assert fb._wrap("一二三四五", 20.0, 50.0) == "一二\n三四\n五"
    assert fb._lines("a\r\nb\n") == ["a", "b"] and fb._lines("") == []
    assert fb._text_width("ab\n一", 10.0) == 12.0


def test_english_labels_and_drawing():
    t = turns()
    g = fb.ground(Branch("t", refs=["00:09"]), t, 52000)
    assert g.label(en=True) == "1 quote(s) · 21 chars"
    assert fb.ground(Branch("t", refs=["00:48"]), t, 52000).label(en=True) == "Thin evidence · 1 quote(s) · 5 chars"
    assert fb.ground(Branch("t", refs=[]), t, 52000).label(en=True) == "No evidence"
    spec = fb.parse(
        '{"problem": "Why late?", "branches": [{"title": "Payments", "refs": ["00:09"]}, {"title": "Guess"}]}'
    )
    grounds = [fb.ground(b, t, 52000) for b in spec.branches]
    texts = [e["text"] for e in fb.excalidraw(spec, grounds, en=True)["elements"] if "text" in e]
    assert "No evidence" not in texts and "1 quote(s) · 21 chars" not in texts
    assert not any(x.startswith("00:09 小张: ") for x in texts)
