"""访谈单元构建：tier 语义 → 单元划分与 gap 归并（TEMPLATE_SPEC 寻址一节）。

单元形态：
- field 单元：有效 tier P0 的字段（含 repeat 实例内同名字段）；
- chapter 单元：声明 tier P1 的节点子树（P0/P2 覆盖字段拆出，P0 先于章节访谈）；
- P2：不产生单元，收尾/回灌时直接转录兜底。
"""
from __future__ import annotations

from .value_tree import (
    _walk_template,
    effective_tier,
    effective_tier_at,
    match_value_paths,
    nearest_p1_ancestor,
    template_path_of,
)


def _chapter_covered_fields(template: list[dict], node: dict, value_tree: list[dict]) -> list[str]:
    """章节单元的覆盖字段：子树中有效 tier 为 P1 的字段（P0/P2 覆盖字段已拆出）。"""
    tpl_paths: list[str] = []

    def walk(nodes: list[dict], inherited: str | None) -> None:
        for n in nodes:
            eff = effective_tier(n, inherited)
            if n["kind"] == "field":
                if eff == "P1":
                    tpl_paths.append(n["path"])
            elif n["kind"] == "repeat":
                walk(n["children"], eff)
            else:
                walk(n.get("children") or [], eff)

    walk(node["children"] if node["kind"] == "repeat" else node.get("children") or [], "P1")
    covered: list[str] = []
    for tp in tpl_paths:
        covered.extend(match_value_paths(value_tree, tp))
    return covered


def make_unit(kind: str, node: dict, tier: str, value_tree: list[dict], template: list[dict]) -> dict:
    tpl_path = node["path"]
    if kind == "field":
        covered = match_value_paths(value_tree, tpl_path)
    else:
        covered = _chapter_covered_fields(template, node, value_tree)
    return {
        "id": f"{kind}:{tpl_path}",
        "kind": kind,
        "tier": tier,
        "template_path": tpl_path,
        "label": f"{tpl_path} {node['title']}",
        "covered": covered,
    }


def build_units(template: list[dict], value_tree: list[dict]) -> list[dict]:
    """正常模式：按 tier 划分全部访谈单元（DFS 序，P0 先于章节单元）。"""
    units: list[dict] = []

    def walk(nodes: list[dict], inherited: str | None) -> None:
        for node in nodes:
            eff = effective_tier(node, inherited)
            if node["kind"] == "field":
                if eff == "P0":
                    units.append(make_unit("field", node, "P0", value_tree, template))
                continue
            if node.get("tier") == "P1":
                # 先收集子树内的 P0 覆盖字段与内嵌 P1 章节，再整体访谈本章节
                walk(node["children"], "P1")
                units.append(make_unit("chapter", node, "P1", value_tree, template))
                continue
            if node["kind"] == "repeat":
                walk(node["children"], eff)
            else:
                walk(node.get("children") or [], eff)

    walk(template, None)
    return [u for u in units if u["kind"] == "chapter" or u["covered"]]


def build_gap_units(
    template: list[dict], value_tree: list[dict], gap_paths: list[str]
) -> list[dict]:
    """缺口驱动模式：按 gap 路径归并构造访谈单元（跳过无 gap 的单元）。"""
    units_by_key: dict[str, dict] = {}
    for gp in gap_paths:
        tpl_path = template_path_of(template, gp)
        eff = effective_tier_at(template, tpl_path)
        if eff == "P2":
            continue  # P2 不走访谈，由调用方直接转录兜底
        if eff == "P0":
            node = _walk_template(template, tpl_path.split("."))
            if node is None:
                continue
            unit = make_unit("field", node, "P0", value_tree, template)
        else:
            anc = nearest_p1_ancestor(template, tpl_path)
            if anc is not None:
                node = anc
            else:
                # 根节点兜底：整个文档作为章节单元
                node = template[0]
            unit = make_unit("chapter", node, "P1", value_tree, template)
        units_by_key.setdefault(unit["id"], unit)
    return list(units_by_key.values())


def gap_targets(unit: dict, gap_paths: list[str]) -> list[str]:
    """单元覆盖的 gap 路径子集（供访谈上下文提示「待澄清缺口」）。"""
    covered = set(unit["covered"])
    return [g for g in gap_paths if g in covered]
