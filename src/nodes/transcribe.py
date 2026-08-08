"""transcribe_agent：对话 → 取值树子树（修订式转录）。

硬性约束：
1. 输入必须包含现有结构化数据（单元子树/字段值）；
2. 在原有数据基础上修改：保留有效、修改过时、补充缺失，绝不整体重写；
3. enum 只能转录为枚举值之一（就近修正或「待补充」），由 value_tree.set_field_value 兜底校验。
"""
from __future__ import annotations

import copy

from ..tools.value_tree import (
    apply_field_unit_value,
    apply_subtree,
    find_value_node,
    replace_node,
    template_node_at,
)
from ..prompts.loader import load_prompt
from .common import conversation_text, json_block, unit_description_text


def _with_template_hints(template: list[dict], node: dict) -> dict:
    """在 repeat 节点上附带 children_template（实例模板），供转录参考。"""
    n = copy.deepcopy(node)
    if n["kind"] == "repeat":
        tpl = template_node_at(template, n["path"]) if n.get("path") else None
        if tpl:
            n["children_template"] = tpl.get("children") or []
        for inst in n.get("instances") or []:
            for c in inst.get("children") or []:
                _with_template_hints(template, c)
    elif n["kind"] == "group":
        for c in n.get("children") or []:
            _with_template_hints(template, c)
    return n


def make_transcribe_node(llm):
    system_tpl = load_prompt("transcribe")

    def node(state: dict) -> dict:
        unit = state.get("current_unit") or {}
        template = state.get("template") or []
        value_tree = copy.deepcopy(state.get("prd_draft") or [])
        conv = state.get("unit_conversation") or []
        unit_desc = unit_description_text(template, unit)

        if unit.get("kind") == "chapter":
            root = find_value_node(value_tree, unit.get("template_path") or "")
            tree_block = (
                json_block("TREE_JSON", _with_template_hints(template, root))
                if root
                else "TREE_JSON\n```json\n{}\n```"
            )
            user = (
                f"单元：{unit.get('label', '')}\n\n"
                f"单元描述：\n{unit_desc}\n\n"
                f"现有取值树（在其基础上修订）：\n{tree_block}\n\n"
                f"对话：\n{conversation_text(conv)}"
            )
            system = system_tpl.replace("{UNIT_DESCRIPTION}", unit_desc).replace("{TREE_BLOCK}", tree_block)
            raw = llm.chat_json(system, user)
            subtree = raw.get("subtree") if isinstance(raw, dict) else None
            if isinstance(subtree, list):  # 兼容直接给出子节点列表的输出
                subtree = {"children": subtree}
            if root is not None and isinstance(subtree, dict):
                new_root = apply_subtree(template, root, subtree)
                replace_node(value_tree, root["path"], new_root)
        else:
            covered_nodes = [
                find_value_node(value_tree, p) for p in unit.get("covered") or []
            ]
            tree_block = json_block("TREE_JSON", [n for n in covered_nodes if n])
            user = (
                f"单元：{unit.get('label', '')}\n\n"
                f"单元描述：\n{unit_desc}\n\n"
                f"现有取值：\n{tree_block}\n\n"
                f"对话：\n{conversation_text(conv)}"
            )
            system = system_tpl.replace("{UNIT_DESCRIPTION}", unit_desc).replace("{TREE_BLOCK}", tree_block)
            raw = llm.chat_json(system, user)
            fields = raw.get("fields") if isinstance(raw, dict) else None
            covered_set = set(unit.get("covered") or [])
            if isinstance(fields, dict):
                for path, value in fields.items():
                    if path in covered_set:
                        apply_field_unit_value(value_tree, path, value)

        stack = list(state.get("undo_stack") or [])
        stack.append(
            {
                "kind": "transcribe",
                "unit": copy.deepcopy(unit),
                "snapshot_tree": copy.deepcopy(state.get("prd_draft") or []),
                "snapshot_units_done": list(state.get("units_done") or []),
            }
        )
        units_done = list(state.get("units_done") or []) + [unit.get("id")]
        return {
            "prd_draft": value_tree,
            "unit_conversation": [],
            "unit_turns": 0,
            "current_unit": None,
            "units_done": units_done,
            "undo_stack": stack,
        }

    return node
