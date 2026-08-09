from yespm_backend.tree.value_tree import (
    apply_field_unit_value,
    apply_subtree,
    effective_tier_at,
    find_value_node,
    replace_node,
    template_node_at,
    template_path_of,
    tree_to_text,
)


def test_instantiation_paths(value_tree):
    assert [n["path"] for n in value_tree] == ["1", "2", "3", "4", "5", "6", "7"]
    assert find_value_node(value_tree, "3.2.1") is None  # 无实例
    node = find_value_node(value_tree, "1.1")
    assert node["kind"] == "field" and node["required"] is True
    assert find_value_node(value_tree, "9") is None


def test_template_addressing(template):
    assert template_path_of(template, "3.2.2.1") == "3.2.X.1"
    assert template_path_of(template, "3.2.1") == "3.2.X"
    assert template_node_at(template, "3.2.2.3.1")["title"] == "主流程"
    assert template_node_at(template, "3.2.1")["kind"] == "repeat"
    assert effective_tier_at(template, "1.1") == "P1"      # 继承章节
    assert effective_tier_at(template, "1.2") == "P0"      # 自身声明覆盖
    assert effective_tier_at(template, "3.1") == "P0"      # 默认
    assert effective_tier_at(template, "6.1") == "P2"      # repeat 声明
    assert effective_tier_at(template, "7") == "P2"


def test_apply_subtree_revision_semantics(template, value_tree):
    """修订式转录：对话未覆盖的内容保留原样。"""
    apply_field_unit_value(value_tree, "2.1", "已经填好的值")
    rep = find_value_node(value_tree, "3.2")
    raw = {
        "title": "核心功能详述",
        "instances": [
            {
                "children": [
                    {"title": "功能名称", "value": "用户登录"},
                    {"title": "功能描述", "value": "支持账号密码登录"},
                    {"title": "优先级", "value": "P0"},
                ]
            }
        ],
    }
    replace_node(value_tree, "3.2", apply_subtree(template, rep, raw))
    assert find_value_node(value_tree, "3.2.1.1")["value"] == "用户登录"
    assert find_value_node(value_tree, "2.1")["value"] == "已经填好的值"
    # 未覆盖的既有内容保持不变
    assert find_value_node(value_tree, "3.2.1.3.1")["value"] is None
    assert find_value_node(value_tree, "3.2.1.4")["value"] == "P0"


def test_enum_and_table_validation(template, value_tree):
    rep = find_value_node(value_tree, "3.2")
    raw = {
        "title": "核心功能详述",
        "instances": [{"children": [{"title": "功能名称", "value": "登录"}]}],
    }
    replace_node(value_tree, "3.2", apply_subtree(template, rep, raw))
    apply_field_unit_value(value_tree, "3.2.1.4", "P1")       # 合法
    assert find_value_node(value_tree, "3.2.1.4")["value"] == "P1"
    apply_field_unit_value(value_tree, "3.2.1.4", "高优先级")  # 非法 → 待补充
    assert find_value_node(value_tree, "3.2.1.4")["value"] == "待补充"
    apply_field_unit_value(value_tree, "3.2.1.4", "P0（最高）")  # 就近修正
    assert find_value_node(value_tree, "3.2.1.4")["value"] == "P0"

    apply_field_unit_value(
        value_tree, "3.1",
        [{"模块名称": "认证", "模块职责": "登录注册"}],
    )
    assert find_value_node(value_tree, "3.1")["value"][0]["模块名称"] == "认证"
    apply_field_unit_value(value_tree, "3.1", "不是列表")  # 非法
    assert find_value_node(value_tree, "3.1")["value"] == "待补充"


def test_preserve_flag_present(template):
    tpl = template_node_at(template, "4.2")
    assert tpl["preserve"] is True


def test_tree_text(value_tree):
    text = tree_to_text(value_tree)
    assert "1 文档信息" in text
    assert "2.1 一句话定位 [必填]: (未填)" in text
