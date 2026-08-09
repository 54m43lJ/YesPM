"""取值树：与模板同构的中间态（prd_draft）。

- 遍历模板实例化：field 持有值，group 仅结构，repeat 展开为实例数组。
- 位置路径（"3.2.1.1"）为全流程寻址键；模板路径中 repeat 实例级以 X 占位（"3.2.X.1"）。
- 所有值校验（enum 就近修正、table 行规范化）集中在此处，转录合并统一走本模块。

（实现自 YesPM evolution-1，模板同构语义不变。）
"""
from __future__ import annotations

import copy
from typing import Any

PENDING = "待补充"


def _child_nodes(node: dict) -> list[dict]:
    """取值树节点的子节点：repeat 的实例层、其余为 children。"""
    if node["kind"] == "repeat":
        return node.get("instances") or []
    return node.get("children") or []


def _walk(nodes: list[dict]) -> list[dict]:
    """遍历取值树全部节点（含实例层）。"""
    out: list[dict] = []
    for node in nodes:
        out.append(node)
        for child in _child_nodes(node):
            out.extend(_walk([child]))
    return out


# ---------------------------------------------------------------- 实例化

def instantiate_value_tree(template: list[dict]) -> list[dict]:
    """模板树 → 取值树（field 值置空，repeat 展开为空实例数组）。"""

    def make(node: dict, path: str) -> dict:
        base = {
            "kind": node["kind"],
            "title": node["title"],
            "path": path,
        }
        if node["kind"] == "field":
            base.update(
                {
                    "field_type": node.get("field_type", "text"),
                    "enum_values": node.get("enum_values"),
                    "columns": node.get("columns"),
                    "required": node.get("required", False),
                    "default": node.get("default"),
                    "preserve": node.get("preserve", False),
                    "hint": node.get("hint"),
                    "description": node.get("description"),
                    "value": None,
                    "status": "empty",
                }
            )
            return base
        if node["kind"] == "repeat":
            base["item_label"] = node["item_label"]
            base["tier"] = node.get("tier")
            base["instances"] = []
            return base
        base["children"] = [make(c, f"{path}.{i}") for i, c in enumerate(node.get("children") or [], start=1)]
        return base

    return [make(node, str(i)) for i, node in enumerate(template, start=1)]


def compute_template_paths(template: list[dict]) -> None:
    """为模板节点就地计算位置路径；repeat 的 children 模板路径实例级用 X 占位。"""

    def assign(nodes: list[dict], parent: str) -> None:
        for i, node in enumerate(nodes, start=1):
            path = f"{parent}.{i}" if parent else str(i)
            node["path"] = path
            if node["kind"] == "repeat":
                assign(node["children"], f"{path}.X")
            else:
                assign(node.get("children") or [], path)

    assign(template, "")


# ---------------------------------------------------------------- 寻址

def find_value_node(nodes: list[dict], path: str) -> dict | None:
    """按取值树具体路径定位节点。"""
    parts = [int(p) for p in path.split(".")]
    current = nodes
    node = None
    for idx in parts:
        if idx < 1 or idx > len(current):
            return None
        node = current[idx - 1]
        current = _child_nodes(node)
    return node


def template_node_at(template: list[dict], value_path: str) -> dict | None:
    """按取值树具体路径解析对应的模板节点（跳过 repeat 实例级）。"""
    parts = [int(p) for p in value_path.split(".")]
    nodes = template
    node = None
    i = 0
    while i < len(parts):
        idx = parts[i] - 1
        if idx < 0 or idx >= len(nodes):
            return None
        node = nodes[idx]
        i += 1
        if node["kind"] == "repeat":
            i += 1  # 跳过实例序号
            if i >= len(parts):
                return node
            idx2 = parts[i] - 1
            if idx2 < 0 or idx2 >= len(node["children"]):
                return None
            node = node["children"][idx2]
            nodes = node.get("children") or []
            i += 1
            continue
        nodes = node.get("children") or []
    return node


def _walk_template(template: list[dict], segs: list[str]) -> dict | None:
    """沿模板路径（含 X 占位）定位模板节点。"""
    nodes = template
    node = None
    for s in segs:
        if s == "X":
            continue
        idx = int(s) - 1
        if idx < 0 or idx >= len(nodes):
            return None
        node = nodes[idx]
        nodes = node["children"] if node["kind"] == "repeat" else (node.get("children") or [])
    return node


def template_path_of(template: list[dict], value_path: str) -> str:
    """取值树具体路径 → 模板路径（repeat 实例级转 X 占位）。"""
    parts = value_path.split(".")
    segs: list[str] = []
    nodes = template
    i = 0
    while i < len(parts):
        node = nodes[int(parts[i]) - 1]
        segs.append(parts[i])
        i += 1
        if node["kind"] == "repeat":
            i += 1  # 跳过实例序号
            segs.append("X")
            if i >= len(parts):
                break
            segs.append(parts[i])
            node = node["children"][int(parts[i]) - 1]
            nodes = node.get("children") or []
            i += 1
            continue
        nodes = node.get("children") or []
    return ".".join(segs)


def effective_tier_at(template: list[dict], tpl_path: str) -> str:
    """模板路径节点的有效 tier（自身声明或最近祖先声明，默认 P0）。"""
    inherited = None
    nodes = template
    for s in tpl_path.split("."):
        if s == "X":
            continue
        node = nodes[int(s) - 1]
        inherited = node.get("tier") or inherited
        nodes = node["children"] if node["kind"] == "repeat" else (node.get("children") or [])
    return inherited or "P0"


def nearest_p1_ancestor(template: list[dict], tpl_path: str) -> dict | None:
    """向上找最近声明 tier=P1 的祖先模板节点；无则 None（根节点兜底）。"""
    segs = tpl_path.split(".")
    for cut in range(len(segs) - 1, 0, -1):
        anc = _walk_template(template, segs[:cut])
        if anc and anc.get("tier") == "P1":
            return anc
    return None


def match_value_paths(nodes: list[dict], template_path: str) -> list[str]:
    """模板路径（含 X 占位）→ 取值树中所有匹配的具体路径。"""
    tparts = template_path.split(".")
    results: list[str] = []

    def rec(nodes: list[dict], depth: int, prefix: str) -> None:
        for i, node in enumerate(nodes, start=1):
            seg = tparts[depth]
            if seg != "X" and seg != str(i):
                continue
            path = f"{prefix}.{i}" if prefix else str(i)
            if depth == len(tparts) - 1:
                results.append(path)
                continue
            children = _child_nodes(node)
            if children:
                rec(children, depth + 1, path)

    rec(nodes, 0, "")
    return results


def effective_tier(node: dict, inherited: str | None = None) -> str:
    """有效 tier = 自身声明或最近祖先声明，默认 P0。"""
    return node.get("tier") or inherited or "P0"


def template_fields_with_tier(template: list[dict]) -> list[tuple[dict, str]]:
    """返回所有模板 field 节点及有效 tier（DFS 序）。"""
    out: list[tuple[dict, str]] = []

    def walk(nodes: list[dict], inherited: str | None) -> None:
        for node in nodes:
            eff = effective_tier(node, inherited)
            if node["kind"] == "field":
                out.append((node, eff))
            elif node["kind"] == "repeat":
                walk(node["children"], eff)
            else:
                walk(node.get("children") or [], eff)

    walk(template, None)
    return out


# ---------------------------------------------------------------- 值校验与合并

def _nearest_enum(value: Any, enum_values: list[str] | None) -> Any:
    if value is None or not enum_values:
        return value
    if value in enum_values:
        return value
    vs = str(value)
    for ev in enum_values:
        if ev in vs or vs in ev:
            return ev
    return None  # 无效 → 由调用方决定（保留原值或待补充）


def validate_value(node: dict, value: Any) -> Any:
    """按 field 元数据校验值；无效时返回 None 表示「待补充」。"""
    ftype = node["field_type"]
    if ftype == "enum":
        return _nearest_enum(value, node.get("enum_values"))
    if ftype == "table":
        return _normalize_table(value, node.get("columns") or [])
    if isinstance(value, list):  # text 拒绝列表
        return "\n".join(str(v) for v in value)
    return None if value is None else str(value)


def _normalize_table(value: Any, columns: list[dict]) -> list[dict] | None:
    titles = [c["title"] for c in columns]
    if not isinstance(value, list):
        return None
    rows = []
    for item in value:
        if isinstance(item, str):
            row = {titles[0]: item} if titles else {}
        elif isinstance(item, dict):
            row = {t: item.get(t) for t in titles}
        else:
            continue
        rows.append(row)
    return rows if rows else None


def set_field_value(node: dict, value: Any) -> None:
    """对取值树 field 节点写值（含校验）；无效值标记「待补充」。"""
    validated = validate_value(node, value)
    if validated is None and value is not None and str(value).strip() != "":
        validated = PENDING
    node["value"] = validated
    node["status"] = "filled" if validated not in (None, "") else "empty"


def apply_field_unit_value(tree: list[dict], path: str, value: Any) -> bool:
    """field 单元转录：按路径写单个字段值。"""
    node = find_value_node(tree, path)
    if node is None:
        return False
    set_field_value(node, value)
    return True


def apply_subtree(template: list[dict], existing: dict, raw: dict) -> dict:
    """按模板结构规范化 LLM 输出的子树（修订式转录核心）。

    - field：raw 有值则覆盖（校验/就近修正），无则保留原值；
    - group：逐子节点递归；
    - repeat：raw 提供实例则重建（无实例则保留原实例），实例按 children 模板实例化。
    """
    node = copy.deepcopy(existing)
    if node["kind"] == "field":
        set_field_value(node, raw.get("value") if isinstance(raw, dict) else None)
        return node

    raw_inst = None
    if isinstance(raw, dict):
        if node["kind"] == "repeat":
            raw_inst = raw.get("instances") or raw.get("children") or []
        else:
            raw_inst = raw.get("children") or []

    if node["kind"] == "repeat":
        if not raw_inst:
            return node
        tpl_children = template_node_at(template, node["path"])["children"] if node.get("path") else None
        instances = []
        for i, ri in enumerate(raw_inst, start=1):
            inst_path = f"{node['path']}.{i}"
            children = _normalize_children_from_template(tpl_children, ri, inst_path)
            inst = {
                "kind": "instance",
                "title": node["title"],
                "path": inst_path,
                "children": children,
            }
            instances.append(inst)
        if instances:
            node["instances"] = instances
        return node

    # group
    raw_by_title = {rc.get("title"): rc for rc in raw_inst if isinstance(rc, dict) and rc.get("title")}
    node["children"] = [
        apply_subtree(template, child, raw_by_title.get(child["title"], {}))
        for child in node.get("children") or []
    ]
    return node


def _normalize_children_from_template(
    tpl_children: list[dict] | None, raw: dict, prefix: str
) -> list[dict]:
    """按 repeat 的 children 模板实例化实例子节点，raw 为 LLM 提供的实例内容。"""
    if not tpl_children:
        return []
    raw_children = raw.get("children") if isinstance(raw, dict) else None
    raw_by_title = (
        {rc.get("title"): rc for rc in raw_children if isinstance(rc, dict) and rc.get("title")}
        if isinstance(raw_children, list)
        else {}
    )

    def build(tpl: dict) -> dict:
        node = {
            "kind": tpl["kind"],
            "title": tpl["title"],
            "path": "",  # 由 assign_paths 补全
        }
        if tpl["kind"] == "field":
            node.update(
                {
                    "field_type": tpl.get("field_type", "text"),
                    "enum_values": tpl.get("enum_values"),
                    "columns": tpl.get("columns"),
                    "required": tpl.get("required", False),
                    "default": tpl.get("default"),
                    "preserve": tpl.get("preserve", False),
                    "hint": tpl.get("hint"),
                    "description": tpl.get("description"),
                    "value": None,
                    "status": "empty",
                }
            )
            raw_node = raw_by_title.get(tpl["title"], {})
            set_field_value(node, raw_node.get("value") if raw_node else None)
            return node
        if tpl["kind"] == "repeat":
            node["item_label"] = tpl["item_label"]
            node["instances"] = []
            return node
        node["children"] = [build(c) for c in tpl.get("children") or []]
        return node

    children = [build(c) for c in tpl_children]

    def assign_paths(nodes: list[dict], prefix: str) -> None:
        for i, n in enumerate(nodes, start=1):
            n["path"] = f"{prefix}.{i}"
            if n["kind"] == "repeat":
                for j, inst in enumerate(n["instances"], start=1):
                    assign_paths(inst["children"], f"{n['path']}.{j}")
            else:
                assign_paths(n.get("children") or [], n["path"])

    assign_paths(children, prefix)
    return children


def replace_node(nodes: list[dict], path: str, new_node: dict) -> bool:
    """以新节点替换取值树中 path 处的节点（章节级转录落盘）。"""
    parent_path, _, last = path.rpartition(".")
    if parent_path:
        parent = find_value_node(nodes, parent_path)
        if parent is None:
            return False
        if parent["kind"] == "repeat":
            idx = int(last) - 1
            if 0 <= idx < len(parent["instances"]):
                parent["instances"][idx] = new_node
                return True
            return False
        children = parent.get("children") or []
        idx = int(last) - 1
        if 0 <= idx < len(children):
            children[idx] = new_node
            return True
        return False
    idx = int(last) - 1
    if 0 <= idx < len(nodes):
        nodes[idx] = new_node
        return True
    return False


# ---------------------------------------------------------------- 摘要与展示

def summarize_tree(nodes: list[dict]) -> dict:
    filled = total = req_filled = req_total = 0
    for node in _walk(nodes):
        if node["kind"] == "field":
            total += 1
            if node["status"] == "filled" and node["value"] not in (None, ""):
                filled += 1
            if node.get("required"):
                req_total += 1
                if node["status"] == "filled" and node["value"] not in (None, ""):
                    req_filled += 1
    return {
        "filled": filled,
        "total": total,
        "required_filled": req_filled,
        "required_total": req_total,
    }


def value_summary_text(nodes: list[dict]) -> str:
    s = summarize_tree(nodes)
    return (
        f"字段 {s['filled']}/{s['total']} 已填；"
        f"必填 {s['required_filled']}/{s['required_total']} 已填"
    )


def instance_display_title(inst: dict) -> str:
    """repeat 实例标题：优先取实例内与 item_label 同名的已填字段值，否则「{item_label} {序号}」。"""
    item_label = inst.get("item_label") or "实例"
    candidates = []
    for child in inst.get("children") or []:
        if child["kind"] == "field" and child["title"] in (item_label, f"{item_label}名称", "名称"):
            candidates.append(child)
    if not candidates:
        for child in inst.get("children") or []:
            if child["kind"] == "field":
                candidates.append(child)
                break
    for c in candidates:
        if c.get("status") == "filled" and c.get("value"):
            return str(c["value"])
    idx = inst.get("path", "").split(".")[-1]
    return f"{item_label} {idx}"


def tree_to_text(nodes: list[dict], include_value: bool = True, max_depth: int = 99) -> str:
    """取值树紧凑文本：供 agent 上下文与 /view 展示。"""

    def field_line(node: dict) -> str:
        meta = ""
        if node.get("required"):
            meta += " [必填]"
        if node.get("preserve"):
            meta += " [润色禁区]"
        if not include_value:
            return f"{node['path']} {node['title']}{meta}"
        if node["status"] == "filled" and node["value"] not in (None, ""):
            return f"{node['path']} {node['title']}{meta}: {node['value']}"
        return f"{node['path']} {node['title']}{meta}: (未填)"

    lines: list[str] = []

    def rec(nodes: list[dict], depth: int) -> None:
        for node in nodes:
            indent = "  " * depth
            if node["kind"] == "field":
                lines.append(indent + field_line(node))
            elif node["kind"] == "repeat":
                lines.append(indent + f"{node['path']} {node['title']} (可重复)"
                             + (f" ×{len(node['instances'])}" if node["instances"] else ""))
                for inst in node["instances"]:
                    lines.append(indent + f"  - {inst['path']} {instance_display_title(inst)}")
                    rec(inst["children"], depth + 2)
            else:
                lines.append(indent + f"{node['path']} {node['title']}")
                rec(node["children"], depth + 1)

    rec(nodes, 0)
    return "\n".join(lines)
