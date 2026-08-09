from yespm_backend.tree.units import build_gap_units, build_units


def test_build_units_order_and_kinds(template, value_tree):
    units = build_units(template, value_tree)
    ids = [u["id"] for u in units]
    # P1 章节内嵌 P0 字段：先字段级单元，再章节单元
    assert ids.index("field:1.2") < ids.index("chapter:1")
    assert "field:1.1" not in ids  # P1 章节覆盖
    # 产品概述 P0 章节：字段逐个单独访谈
    for p in ("2.1", "2.2", "2.3", "2.4"):
        assert f"field:{p}" in ids
    # 功能需求：P0 字段 + P1 repeat 章节
    assert "field:3.1" in ids
    assert "chapter:3.2" in ids
    assert "field:3.3" in ids
    # P2 不产生单元
    assert not any("6" in u["id"] for u in units)
    assert not any(u["id"] == "field:7" for u in units)
    # 章节单元覆盖字段
    ch1 = next(u for u in units if u["id"] == "chapter:1")
    assert set(ch1["covered"]) == {"1.1", "1.3", "1.4"}


def test_build_gap_units_p0_field(template, value_tree):
    units = build_gap_units(template, value_tree, ["2.1"])
    assert [u["id"] for u in units] == ["field:2.1"]


def test_build_gap_units_p1_merge_to_chapter(template, value_tree):
    # 3.2.1.1 功能名称：有效 tier P1（继承自 repeat）→ 归并到章节单元 chapter:3.2
    units = build_gap_units(template, value_tree, ["3.2.1.1"])
    assert [u["id"] for u in units] == ["chapter:3.2"]


def test_build_gap_units_p2_skipped(template, value_tree):
    units = build_gap_units(template, value_tree, ["7"])
    assert units == []


def test_build_gap_units_skip_no_gap_units(template, value_tree):
    units = build_gap_units(template, value_tree, ["2.1", "4.1"])
    assert [u["id"] for u in units] == ["field:2.1", "field:4.1"]
    assert "chapter:1" not in [u["id"] for u in units]
