"""agent 节点共享的上下文组装工具。"""
from __future__ import annotations

import json

from ..tools.value_tree import _walk_template, find_value_node, tree_to_text


def unit_description_text(template: list[dict], unit: dict) -> str:
    """单元覆盖的模板节点描述（标题 + hint + description，供访谈/评审/转录）。"""
    node = _walk_template(template, (unit.get("template_path") or "").split("."))
    if node is None:
        return unit.get("label", "")
    lines: list[str] = []

    def fmt(n: dict, depth: int) -> None:
        indent = "  " * depth
        if n["kind"] == "field":
            extra = ""
            if n.get("field_type") != "text":
                extra += f" [field_type={n['field_type']}]"
            if n.get("enum_values"):
                extra += f" 枚举={n['enum_values']}"
            if n.get("columns"):
                extra += f" 列={[c['title'] for c in n['columns']]}"
            if n.get("required"):
                extra += " [必填]"
            lines.append(f"{indent}{n['path']} {n['title']}{extra}")
        elif n["kind"] == "repeat":
            lines.append(
                f"{indent}{n['path']} {n['title']} (repeat，实例数由访谈确定，item_label={n['item_label']})"
            )
        else:
            lines.append(f"{indent}{n['path']} {n['title']} (章节)")
        if n.get("hint"):
            lines.append(f"{indent}  hint: {n['hint']}")
        if n.get("description"):
            lines.append(f"{indent}  desc: {n['description']}")
        if n["kind"] == "repeat":
            for c in n["children"]:
                fmt(c, depth + 1)
        elif n["kind"] == "group":
            for c in n.get("children") or []:
                fmt(c, depth + 1)

    fmt(node, 0)
    return "\n".join(lines)


def unit_existing_text(value_tree: list[dict], unit: dict) -> str:
    """单元已转录取值（供访谈/评审/转录上下文）。"""
    if unit.get("kind") == "chapter":
        node = find_value_node(value_tree, unit.get("template_path") or "")
        return tree_to_text([node]) if node else "(尚无取值)"
    parts = []
    for p in unit.get("covered") or []:
        node = find_value_node(value_tree, p)
        if node:
            parts.append(tree_to_text([node]))
    return "\n".join(parts) or "(尚无取值)"


def conversation_text(conv: list[dict]) -> str:
    lines = []
    for m in conv:
        role = "用户" if m.get("role") == "user" else "访谈agent"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines) or "(本单元尚无对话)"


def json_block(marker: str, obj: object) -> str:
    return f"{marker}\n```json\n{json.dumps(obj, ensure_ascii=False, default=str)}\n```"
