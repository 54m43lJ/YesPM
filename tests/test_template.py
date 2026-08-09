import pytest

from yespm_backend.template import (
    TemplateValidationError,
    load_default_template,
    load_template,
)


def test_default_template_loads():
    tpl = load_default_template()
    assert [n["title"] for n in tpl] == [
        "文档信息",
        "产品概述",
        "功能需求",
        "非功能需求",
        "风险登记",
        "版本计划",
        "术语表",
    ]
    # kind 推断
    assert tpl[0]["kind"] == "group"
    assert tpl[4]["kind"] == "field"  # 风险登记：table 叶子
    assert tpl[5]["kind"] == "repeat"
    # 路径就绪
    assert tpl[2]["path"] == "3"
    assert tpl[2]["children"][1]["path"] == "3.2"  # 核心功能详述
    # preserve 标记
    nonfunc = tpl[3]
    assert nonfunc["children"][1]["preserve"] is True


def test_infer_kind_by_fields(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(
        """
- title: A
  children:
    - title: leaf
- title: R
  item_label: x
  children:
    - title: name
- title: F
""".strip(),
        encoding="utf-8",
    )
    tpl = load_template(p)
    assert [n["kind"] for n in tpl] == ["group", "repeat", "field"]


def test_invalid_repeat_without_children(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- title: R\n  item_label: x\n", encoding="utf-8")
    with pytest.raises(TemplateValidationError):
        load_template(p)


def test_invalid_enum_without_values(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- title: F\n  field_type: enum\n", encoding="utf-8")
    with pytest.raises(TemplateValidationError):
        load_template(p)


def test_invalid_table_without_columns(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- title: F\n  field_type: table\n", encoding="utf-8")
    with pytest.raises(TemplateValidationError):
        load_template(p)


def test_invalid_preserve_on_enum(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "- title: F\n  field_type: enum\n  enum_values: [a]\n  preserve: true\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateValidationError):
        load_template(p)


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- title: F\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(TemplateValidationError):
        load_template(p)


def test_field_with_children_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "- title: F\n  item_label: x\n  children: []\n",  # 空 children
        encoding="utf-8",
    )
    with pytest.raises(TemplateValidationError):
        load_template(p)
