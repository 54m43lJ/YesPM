"""渲染润色阶段（Node ③）：生成器-评估器架构。

- 转换生成器：扫描基线，识别 A/B/C/D 场景，产出转换提案；
- 风险分级（规则化，非 LLM）：A/C 类低风险自动执行；B/D 类高风险用户逐条确认；
- 保真评估器：逐条校验提案事实点不增/不减/不改，不通过则反馈生成器修订（最多 N 轮）；
- 转换禁区：field_type table/enum 字段、preserve: true 的 text 字段一律禁止。
"""
from __future__ import annotations

import re

from ..prompts.loader import load_prompt
from ..tools.value_tree import template_node_at

LOW_RISK_CLASSES = ("A", "C")
HIGH_RISK_CLASSES = ("B", "D")


def generate_proposals(llm, baseline: str) -> list[dict]:
    """转换生成器：基线 → 提案列表。"""
    system = load_prompt("polish_generator").replace("{BASELINE}", baseline)
    result = llm.chat_json(system, "请扫描基线并输出转换提案 JSON。")
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
                "location": str(p.get("location") or "").strip(),
                "scenario": str(p.get("scenario") or "").upper(),
                "original": str(p.get("original") or ""),
                "target": str(p.get("target") or ""),
                "reason": str(p.get("reason") or ""),
            }
        )
    return proposals


def classify_risk(proposal: dict) -> str:
    """风险分级（规则化）：A/C 低风险自动执行；B/D 高风险用户确认。"""
    cls = (proposal.get("scenario") or "")[:1].upper()
    return "low" if cls in LOW_RISK_CLASSES else "high"


def filter_proposals(proposals: list[dict], template: list[dict]) -> list[dict]:
    """过滤：location 必须命中 field 块；table/enum/preserve 字段禁止提案。"""
    valid = []
    for p in proposals:
        path = p["location"]
        if not re.fullmatch(r"\d+(?:\.\d+)+", path):
            continue
        tpl = template_node_at(template, path)
        if tpl is None:
            continue
        if tpl.get("field_type") in ("table", "enum") or tpl.get("preserve"):
            continue
        valid.append(p)
    return valid


def evaluate_fidelity(llm, proposal: dict, original_content: str) -> tuple[bool, str]:
    """保真评估器：校验 target 相对 original 事实点不变。"""
    system = load_prompt("fidelity")
    system = (
        system.replace("{location}", proposal["location"])
        .replace("{scenario}", proposal["scenario"])
        .replace("{original}", proposal.get("original") or original_content[:200])
        .replace("{target}", proposal["target"])
        .replace("{ORIGINAL_CONTENT}", original_content)
    )
    result = llm.chat_json(system, "请校验该提案的保真度。") or {}
    return bool(result.get("pass")), str(result.get("feedback") or "")


def revise_proposal(llm, proposal: dict, feedback: str) -> dict:
    """修订提案：生成器根据评估器反馈修订 target。"""
    system = load_prompt("polish_generator").replace("{BASELINE}", proposal.get("original") or "")
    user = (
        f"以下转换提案未通过保真评估，请修订 target 后重新输出（只输出 JSON）：\n"
        f"location: {proposal['location']}\nscenario: {proposal['scenario']}\n"
        f"原 target: {proposal['target']}\n评估器反馈: {feedback}\n"
        f"输出格式：{{\"target\": \"修订后的完整内容\"}}"
    )
    result = llm.chat_json(system, user)
    target = result.get("target") if isinstance(result, dict) else None
    if isinstance(target, str) and target.strip():
        return {**proposal, "target": target.strip(), "revised": True}
    return proposal
