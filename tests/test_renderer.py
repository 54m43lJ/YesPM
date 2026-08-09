from yespm_backend.render.renderer import render_blocks, render_value_tree
from yespm_backend.tree.value_tree import (
    apply_field_unit_value,
    apply_subtree,
    find_value_node,
    replace_node,
)


def test_render_empty_required_placeholder(value_tree):
    doc = render_value_tree(value_tree)
    assert "# 产品需求文档" in doc
    assert "# 1 文档信息" in doc
    assert "## 3.2 核心功能详述" not in doc  # 无实例 → 跳过
    assert "*待补充*" in doc  # 必填空字段


def test_render_table(template, value_tree):
    apply_field_unit_value(
        value_tree, "3.1",
        [{"模块名称": "认证", "模块职责": "登录"}, {"模块名称": "消息", "模块职责": "通知"}],
    )
    doc = render_value_tree(value_tree)
    assert "| 模块名称 | 模块职责 |" in doc
    assert "| 认证 | 登录 |" in doc
    assert "| 消息 | 通知 |" in doc


def test_render_repeat_instances(template, value_tree):
    rep = find_value_node(value_tree, "3.2")
    raw = {
        "title": "核心功能详述",
        "instances": [
            {"children": [{"title": "功能名称", "value": "用户登录"}, {"title": "优先级", "value": "P1"}]}
        ],
    }
    replace_node(value_tree, "3.2", apply_subtree(template, rep, raw))
    blocks = render_blocks(value_tree)
    headings = [b for b in blocks if b["kind"] == "heading"]
    assert any(b["title"] == "用户登录" for b in headings)  # 实例自定义名
    doc = render_value_tree(value_tree)
    assert "## 3.2 核心功能详述" in doc
    assert "### 3.2.1 用户登录" in doc


def test_render_preserve_field_plain_text(value_tree):
    apply_field_unit_value(value_tree, "4.2", "原文引用内容")
    doc = render_value_tree(value_tree)
    assert "## 4.2 安全与合规" in doc
    assert "原文引用内容" in doc


def test_blocks_roundtrip(value_tree):
    render_value_tree(value_tree)
    blocks = render_blocks(value_tree)
    field_blocks = [b for b in blocks if b["kind"] == "field"]
    assert field_blocks
    assert all(b["path"] for b in field_blocks)
