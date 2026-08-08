import pytest

from src.tools.template_loader import TemplateValidationError, load_template


MINIMAL_VALID = """
- title: 产品概述
  tier: P0
  children:
    - title: 一句话定位
      required: true
"""


def test_minimal_field_only_title(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(MINIMAL_VALID, encoding="utf-8")
    tpl = load_template(p)
    assert tpl[0]["kind"] == "group"
    assert tpl[0]["children"][0]["kind"] == "field"
    assert tpl[0]["children"][0]["field_type"] == "text"


def test_type_inference(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(
        """
- title: 核心功能
  item_label: 功能
  children:
    - title: 名称
- title: 风险
  field_type: table
  columns: [风险描述, 应对]
- title: 优先级
  field_type: enum
  enum_values: [P0, P1]
""",
        encoding="utf-8",
    )
    tpl = load_template(p)
    assert tpl[0]["kind"] == "repeat"
    assert tpl[0]["item_label"] == "功能"
    assert tpl[1]["kind"] == "field" and tpl[1]["field_type"] == "table"
    assert tpl[2]["kind"] == "field" and tpl[2]["field_type"] == "enum"


@pytest.mark.parametrize(
    "yaml_text, keyword",
    [
        ("- title: ''", "title"),
        ("- children: [{title: x}]", "title"),  # 缺 title
        ("- title: x\n  item_label: f", "children"),  # repeat 缺 children
        ("- title: x\n  item_label: f\n  children: []", "children"),  # children 空
        ("- title: x\n  children: []", "children"),
        ("- title: x\n  field_type: enum", "enum_values"),
        ("- title: x\n  field_type: table", "columns"),
        ("- title: x\n  field_type: enum\n  enum_values: []", "enum_values"),
        ("- title: x\n  field_type: table\n  columns: [a]\n  preserve: true", "preserve"),
        ("- title: x\n  field_type: enum\n  enum_values: [a]\n  preserve: true", "preserve"),
        ("- title: x\n  tier: P9", "tier"),
        ("- title: x\n  unknown_key: 1", "未知字段"),
    ],
)
def test_invalid_templates_rejected(tmp_path, yaml_text, keyword):
    p = tmp_path / "t.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(TemplateValidationError, match=keyword):
        load_template(p)


def test_root_must_be_list(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("title: 不是列表", encoding="utf-8")
    with pytest.raises(TemplateValidationError, match="列表"):
        load_template(p)
