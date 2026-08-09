"""润色阶段节点：全文档审核 + P2 兜底推断 + 生成器-评估器润色（阶段 ②③）。

为「轻量图」逻辑：由引擎按阶段编排调用，非 LangGraph。高风险提案的逐条确认
以引擎 interrupt（proposal/respond）实现——本模块产出待确认提案，引擎暂停等待。

- document_review_agent：完整性 / 跨章节一致性 / 逻辑自洽；
- P2 兜底：不访谈字段收尾推断；
- polish_generator：扫描基线，识别 A/B/C/D 场景，产出转换提案；
- 风险分级（规则化）：A/C 低风险自动执行；B/D 高风险用户确认；
- fidelity_evaluation_agent：逐条校验事实点不增/不减/不改（生成器-评估器迭代）；
- 转换禁区：field_type table/enum 字段、preserve:true 的 text 字段一律禁止。
"""
from __future__ import annotations

import copy
import re

from ..prompts.loader import load_prompt
from ..render.renderer import join_blocks, render_blocks
from ..graph.common import json_block
from ..tree.value_tree import (
    apply_field_unit_value,
    apply_subtree,
    find_value_node,
    match_value_paths,
    replace_node,
    template_fields_with_tier,
    template_node_at,
    tree_to_text,
)

LOW_RISK_CLASSES = ("A", "C")
HIGH_RISK_CLASSES = ("B", "D")


# ---------------------------------------------------------------- 全文档审核

def run_document_review(llm, template: list[dict], value_tree: list[dict], brief: str):
    """document_review_agent：完整取值树 → (passed, gap_list)。"""
    system_tpl = load_prompt("document_review")
    user = (
        f"用户初始简述：\n{brief}\n\n"
        f"完整取值树：\n{json_block('TREE_JSON', value_tree)}"
    )
    system = system_tpl.replace("{brief}", brief).replace("{TREE_JSON}", "见用户输入 TREE_JSON 块")
    try:
        result = llm.chat_json(system, user) or {}
    except Exception:
        result = {}
    passed = bool(result.get("passed"))
    gaps: list[dict] = []
    for g in result.get("gaps") or []:
        if not isinstance(g, dict):
            continue
        path = g.get("path")
        if not isinstance(path, str) or not path:
            continue
        # 路径真实性校验
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


# ---------------------------------------------------------------- P2 兜底

def p2_targets(template: list[dict], value_tree: list[dict], gap_paths: list[str]) -> tuple[list[str], list[str]]:
    """P2 兜底目标：(空字段路径, 无实例的 P2 repeat 路径)。"""
    gap_set = set(gap_paths)
    field_paths: list[str] = []
    repeat_paths: list[str] = []
    for tpl, eff in template_fields_with_tier(template):
        if eff != "P2":
            continue
        if tpl["kind"] == "field":
            for p in match_value_paths(value_tree, tpl["path"]):
                node = find_value_node(value_tree, p)
                if node and (node.get("status") != "filled" or node.get("value") in (None, "")):
                    field_paths.append(p)
                if p in gap_set:
                    field_paths.append(p)
        elif tpl["kind"] == "repeat":
            node = find_value_node(value_tree, tpl["path"])
            if node is not None and not node.get("instances"):
                repeat_paths.append(tpl["path"])
            if tpl["path"] in gap_set:
                repeat_paths.append(tpl["path"])
    return list(dict.fromkeys(field_paths)), list(dict.fromkeys(repeat_paths))


def run_p2_infer(
    llm, template: list[dict], value_tree: list[dict], brief: str,
    field_paths: list[str], repeat_paths: list[str],
) -> None:
    """P2 兜底推断：依据简述 + 全局取值树推断 P2 字段；推断不出取 default/待补充。"""
    if not field_paths and not repeat_paths:
        return
    system = load_prompt("p2_infer")
    system = (
        system.replace("{paths}", ", ".join(field_paths))
        .replace("{repeat_paths}", ", ".join(repeat_paths))
        .replace("{brief}", brief)
        .replace("{TREE_JSON}", "见用户输入 TREE_JSON 块")
    )
    user = (
        "目标字段：\n" + "\n".join(f"- {p}" for p in field_paths) + "\n\n"
        + "目标 repeat：\n" + "\n".join(f"- {p}" for p in repeat_paths) + "\n\n"
        + f"用户初始简述：\n{brief}\n\n"
        + f"完整取值树：\n{tree_to_text(value_tree)}"
    )
    try:
        result = llm.chat_json(system, user) or {}
    except Exception:
        return
    if isinstance(result, dict):
        values = result.get("values")
        if isinstance(values, dict):
            for path, value in values.items():
                if path in field_paths:
                    apply_field_unit_value(value_tree, path, value)
        subtrees = result.get("subtree")
        if isinstance(subtrees, dict):
            for path, subtree in subtrees.items():
                if path in repeat_paths and isinstance(subtree, dict):
                    existing = find_value_node(value_tree, path)
                    if existing is not None:
                        new_node = apply_subtree(template, existing, subtree)
                        replace_node(value_tree, path, new_node)


# ---------------------------------------------------------------- 润色提案

def generate_proposals(llm, baseline: str) -> list[dict]:
    """polish_generator：基线 → 原始提案列表。"""
    system = load_prompt("polish_generator").replace("{BASELINE}", baseline)
    try:
        result = llm.chat_json(system, "请扫描基线并输出转换提案 JSON。")
    except Exception:
        return []
    raw = result.get("proposals") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []
    proposals = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        proposals.append(
            {
                "id": str(p.get("id") or f"p{len(proposals) + 1}"),
                "position": str(p.get("location") or p.get("position") or "").strip(),
                "scenario": str(p.get("scenario") or "").upper(),
                "before": str(p.get("original") or ""),
                "after": str(p.get("target") or ""),
                "reason": str(p.get("reason") or ""),
            }
        )
    return proposals


def classify_risk(proposal: dict) -> str:
    """风险分级（规则化）：A/C 低风险自动执行；B/D 高风险用户确认。"""
    cls = (proposal.get("scenario") or "")[:1].upper()
    return "low" if cls in LOW_RISK_CLASSES else "high"


def filter_proposals(proposals: list[dict], template: list[dict]) -> list[dict]:
    """过滤：position 必须命中 field 块；table/enum/preserve 字段禁止提案。"""
    valid = []
    for p in proposals:
        path = p["position"]
        if not re.fullmatch(r"\d+(?:\.\d+)+", path or ""):
            continue
        tpl = template_node_at(template, path)
        if tpl is None:
            continue
        if tpl.get("field_type") in ("table", "enum") or tpl.get("preserve"):
            continue
        valid.append(p)
    return valid


def evaluate_fidelity(llm, proposal: dict, original_content: str) -> tuple[bool, str]:
    """fidelity_evaluation_agent：校验 target 相对 original 事实点不变。"""
    system = load_prompt("fidelity")
    system = (
        system.replace("{location}", proposal["position"])
        .replace("{scenario}", proposal["scenario"])
        .replace("{original}", proposal.get("before") or original_content[:200])
        .replace("{target}", proposal["after"])
        .replace("{ORIGINAL_CONTENT}", original_content)
    )
    try:
        result = llm.chat_json(system, "请校验该提案的保真度。") or {}
    except Exception:
        return False, "评估异常"
    return bool(result.get("pass")), str(result.get("feedback") or "")


def revise_proposal(llm, proposal: dict, feedback: str) -> dict:
    """修订提案：生成器根据评估器反馈修订 target（生成器-评估器迭代）。"""
    system = load_prompt("polish_generator").replace("{BASELINE}", proposal.get("before") or "")
    user = (
        f"以下转换提案未通过保真评估，请修订 target 后重新输出（只输出 JSON）：\n"
        f"location: {proposal['position']}\nscenario: {proposal['scenario']}\n"
        f"原 target: {proposal['after']}\n评估器反馈: {feedback}\n"
        f"输出格式：{{\"target\": \"修订后的完整内容\"}}"
    )
    try:
        result = llm.chat_json(system, user)
    except Exception:
        return proposal
    target = result.get("target") if isinstance(result, dict) else None
    if isinstance(target, str) and target.strip():
        return {**proposal, "after": target.strip(), "revised": True}
    return proposal


# ---------------------------------------------------------------- 编排入口

def prepare_polish(llm, cfg, template: list[dict], value_tree: list[dict]):
    """润色准备：基线渲染 → 生成提案 → 过滤 → 保真评估（迭代）→ 分级。

    返回：
      {
        "blocks": 渲染块（低风险已就地应用），
        "low_applied": 已应用的低风险提案（转换日志条目），
        "high_pending": 待用户确认的高风险提案（保真已通过），
        "discarded": 被丢弃的提案（保真未通过），
      }
    """
    blocks = render_blocks(value_tree)
    baseline = join_blocks(blocks)
    log_applied: list[dict] = []
    discarded: list[dict] = []
    high_pending: list[dict] = []

    try:
        proposals = filter_proposals(generate_proposals(llm, baseline), template)
    except Exception:
        proposals = []

    applied_guard: set[tuple[str, str]] = set()
    for p in proposals:
        path = p["position"]
        target_block = next(
            (b for b in blocks if b["path"] == path and b["kind"] == "field"), None
        )
        if target_block is None:
            continue
        guard_key = (path, p["after"][:40])
        if guard_key in applied_guard:
            continue

        risk = classify_risk(p)
        proposal = p
        passed = False
        feedback = ""
        for _round in range(cfg.polish_max_rounds + 1):
            passed, feedback = evaluate_fidelity(llm, proposal, target_block.get("content") or "")
            if passed:
                break
            if _round < cfg.polish_max_rounds:
                proposal = revise_proposal(llm, proposal, feedback)

        entry = {
            "id": proposal["id"],
            "position": proposal["position"],
            "scenario": proposal["scenario"],
            "before": proposal.get("before") or target_block.get("content") or "",
            "after": proposal["after"],
            "risk": risk,
            "reason": proposal.get("reason") or "",
        }
        if not passed:
            discarded.append({**entry, "status": "discarded", "evaluation": feedback or "保真评估不通过"})
            continue

        if risk == "low":
            target_block["content"] = proposal["after"]
            applied_guard.add(guard_key)
            log_applied.append({**entry, "status": "applied", "evaluation": "通过"})
        else:
            # 高风险：保真已通过，交用户逐条确认
            high_pending.append({**entry, "evaluation": "通过"})

    return {
        "blocks": blocks,
        "low_applied": log_applied,
        "high_pending": high_pending,
        "discarded": discarded,
    }


def apply_block_proposal(blocks: list[dict], position: str, after: str) -> bool:
    """把已确认的提案应用到渲染块；返回是否命中。"""
    for b in blocks:
        if b["kind"] == "field" and b["path"] == position:
            b["content"] = after
            return True
    return False


def finalize_prd(blocks: list[dict], conversion_log: list[dict], unresolved_gaps: list[dict]) -> str:
    """组装最终 Markdown：基线（含已应用润色）+ 转换日志 + 审核未决清单（如有）。"""
    final_doc = join_blocks(blocks)
    sections = [final_doc, "", _render_conversion_log(conversion_log)]
    if unresolved_gaps:
        sections.append(_render_unresolved(unresolved_gaps))
    return "\n\n---\n\n".join(sections) + "\n"


def _render_conversion_log(log: list[dict]) -> str:
    lines = [
        "## 转换日志",
        "",
        "| # | 位置 | 场景 | 风险 | 状态 | 评估 |",
        "|---|------|------|------|------|------|",
    ]
    for i, e in enumerate(log, start=1):
        position = e.get("position") or "-"
        scenario = e.get("scenario") or "-"
        risk = e.get("risk") or "-"
        status = e.get("status") or "-"
        evaluation = (e.get("evaluation") or e.get("reason") or "-").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {position} | {scenario} | {risk} | {status} | {evaluation} |")
    out = "\n".join(lines)
    if not log:
        out += "\n\n（无转换提案）"
    return out


def _render_unresolved(unresolved: list[dict]) -> str:
    lines = ["## 审核未决清单", ""]
    for g in unresolved:
        lines.append(f"- `{g.get('path')}` [{g.get('dimension')}] {g.get('reason')}")
    return "\n".join(lines)


def proposal_description(proposal: dict, idx: int, total: int) -> str:
    """高风险提案的自然语言描述（经 session/message 流式推送）。"""
    return (
        f"提案 {idx}/{total} [{proposal.get('scenario')}] 位置 {proposal.get('position')}\n"
        f"原形态：{proposal.get('before', '')[:120]}\n"
        f"目标形态：{proposal.get('after', '')[:120]}\n"
        f"风险：{proposal.get('risk')} | 理由：{proposal.get('reason')}"
    )
