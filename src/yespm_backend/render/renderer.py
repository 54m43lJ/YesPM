"""确定性基线渲染器：取值树 → Markdown（第一层渲染）。

- 按深度产出标题层级（# / ## / ### …），标题带位置路径（如「3.2 核心功能详述」）；
- group → 仅标题；repeat → 实例标题（item_label + 序号或实例自定义名）；
- field 按 field_type 渲染：text → 段落、enum → 所选项、table → Markdown 表格；
- 空值：required 输出「待补充」占位，其余跳过；空 repeat 实例不输出；
- 纯程序性、可重现，是润色管线的基线。无提案区域不被触碰。

内部以「块」为单位产出（heading / field 块），润色提案按块定位与替换。
"""
from __future__ import annotations

from ..tree.value_tree import PENDING, instance_display_title


def render_blocks(nodes: list[dict], doc_title: str = "产品需求文档") -> list[dict]:
    """取值树 → 渲染块列表。"""
    blocks: list[dict] = []

    def heading_block(level: int, path: str, title: str) -> dict:
        return {"kind": "heading", "level": level, "path": path, "title": title}

    def field_block(level: int, node: dict) -> dict:
        content = _render_field(node)
        return {
            "kind": "field",
            "level": level,
            "path": node["path"],
            "title": node["title"],
            "content": content,
        }

    def rec(nodes: list[dict], depth: int) -> None:
        level = depth + 1
        for node in nodes:
            if node["kind"] == "field":
                content = _render_field(node)
                if content is None:
                    continue
                blocks.append(field_block(level, node))
            elif node["kind"] == "repeat":
                if not node.get("instances"):
                    continue
                blocks.append(heading_block(level, node["path"], node["title"]))
                for inst in node["instances"]:
                    inst_title = instance_display_title(inst)
                    blocks.append(heading_block(level + 1, inst["path"], inst_title))
                    rec(inst["children"], depth + 1)
            else:
                blocks.append(heading_block(level, node["path"], node["title"]))
                rec(node["children"], depth + 1)

    rec(nodes, 0)
    return blocks


def _render_field(node: dict) -> str | None:
    """field 内容渲染；空值（非必填）返回 None（跳过）。"""
    if node["status"] != "filled" or node.get("value") in (None, ""):
        if node.get("required"):
            return f"*{PENDING}*"
        return None
    value = node["value"]
    ftype = node["field_type"]
    if ftype == "table":
        return _render_table(value, node.get("columns") or [])
    if ftype == "enum":
        return str(value)
    return str(value)


def _render_table(rows: list[dict], columns: list[dict]) -> str:
    titles = [c["title"] for c in columns]
    if not isinstance(rows, list):
        return str(rows)

    def esc(s: str | None) -> str:
        return str(s or "").replace("|", "\\|").replace("\n", "<br>")

    header = "| " + " | ".join(titles) + " |"
    sep = "| " + " | ".join(["---"] * len(titles)) + " |"
    body = [
        "| " + " | ".join(esc(r.get(t)) for t in titles) + " |"
        for r in rows
        if isinstance(r, dict)
    ]
    if not body:
        return str(rows)
    return "\n".join([header, sep, *body])


def join_blocks(blocks: list[dict]) -> str:
    """渲染块 → 最终 Markdown 文本。"""
    lines: list[str] = []
    for b in blocks:
        if lines:
            lines.append("")
        lines.append(f"{'#' * b['level']} {b['path']} {b['title']}")
        if b["kind"] == "field" and b.get("content"):
            lines.append("")
            lines.append(b["content"])
    return "\n".join(lines)


def render_value_tree(nodes: list[dict], doc_title: str = "产品需求文档") -> str:
    if doc_title:
        return f"# {doc_title}\n\n" + join_blocks(render_blocks(nodes))
    return join_blocks(render_blocks(nodes))
