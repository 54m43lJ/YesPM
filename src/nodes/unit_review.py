"""unit_review_agent：单元成熟度评审（Node ① 内）。

评审输入 = 本单元对话 + 取值树 + 模板单元描述；不成熟时产出缺口清单，
interview_agent 据此针对性追问；无对话不评审（直接转录）。
"""
from __future__ import annotations

from ..prompts.loader import load_prompt
from .common import conversation_text, unit_description_text, unit_existing_text


def make_unit_review_node(llm):
    system_tpl = load_prompt("unit_review")

    def node(state: dict) -> dict:
        unit = state.get("current_unit") or {}
        conv = state.get("unit_conversation") or []
        if not conv:
            return {"interview_gaps": []}
        template = state.get("template") or []
        value_tree = state.get("prd_draft") or []
        covered = "\n".join(unit.get("covered") or []) or "(无)"

        user = (
            f"单元：{unit.get('label', '')}\n\n"
            f"单元描述：\n{unit_description_text(template, unit)}\n\n"
            f"现有取值：\n{unit_existing_text(value_tree, unit)}\n\n"
            f"对话：\n{conversation_text(conv)}"
        )
        system = system_tpl.replace("{COVERED_PATHS}", covered)
        result = llm.chat_json(system, user) or {}
        mature = bool(result.get("mature"))
        gaps: list[dict] = []
        if not mature:
            covered_set = set(unit.get("covered") or [])
            for g in result.get("gaps") or []:
                path = g.get("path") if isinstance(g, dict) else None
                if path in covered_set:
                    gaps.append(
                        {
                            "path": path,
                            "dimension": g.get("dimension") or "完整性",
                            "reason": g.get("reason") or "",
                        }
                    )
        return {"interview_gaps": gaps}

    return node


def route_review(state: dict) -> str:
    """评审路由：有缺口 → 回到访谈针对性追问；成熟/无对话 → 转录。"""
    if state.get("interview_gaps"):
        return "interview"
    return "transcribe"
