"""document_review_agent：全文档审核（Node ②，图外循环调用）。

审核维度：完整性 / 跨章节一致性 / 逻辑自洽。
输出 gap_list（[{path, dimension, reason}]）+ passed；path 只允许取值树中真实存在的路径。
"""
from __future__ import annotations

from ..tools.value_tree import find_value_node, template_node_at
from ..prompts.loader import load_prompt
from .common import json_block


def run_document_review(llm, template: list[dict], value_tree: list[dict], brief: str):
    system_tpl = load_prompt("document_review")
    user = (
        f"用户初始简述：\n{brief}\n\n"
        f"完整取值树：\n{json_block('TREE_JSON', value_tree)}"
    )
    system = system_tpl.replace("{brief}", brief).replace("{TREE_JSON}", "见用户输入 TREE_JSON 块")
    result = llm.chat_json(system, user) or {}
    passed = bool(result.get("passed"))
    gaps: list[dict] = []
    for g in result.get("gaps") or []:
        if not isinstance(g, dict):
            continue
        path = g.get("path")
        if not isinstance(path, str) or not path:
            continue
        # 路径真实性校验：取值树中存在，或可解析到模板节点（缺实例等边界）
        if find_value_node(value_tree, path) is None and template_node_at(template, path) is None:
            continue
        gaps.append(
            {
                "path": path,
                "dimension": g.get("dimension") or "完整性",
                "reason": g.get("reason") or "",
            }
        )
    return passed, gaps
