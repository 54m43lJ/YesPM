from __future__ import annotations

import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from src.nodes.draft import _find_node, _meta_lines, _top_node
from src.state.prd_state import PRDState
from src.tools.template_loader import load_prompt


class AuditResult(BaseModel):
    passed: bool
    feedback: str = ""


def _recent_dialogue(messages: list, n: int = 8) -> str:
    tail = [m for m in (messages or []) if isinstance(m, (AIMessage, HumanMessage))][-n:]
    lines = []
    for m in tail:
        role = "助手" if isinstance(m, AIMessage) else "用户"
        c = getattr(m, "content", "")
        if isinstance(c, list):
            c = str(c)
        lines.append(f"{role}：{c}")
    return "\n".join(lines)


def _build_spec(state: dict) -> list[str]:
    """构建「当前章节/待澄清字段」的要求说明，供阶段内审核判定。"""
    tree = state.get("prd_draft") or []
    pending = sorted(set(state.get("pending_paths") or []))
    lines: list = []
    if pending:
        lines.append("（本次为整树审核后的澄清会话，以「待澄清字段」为判定基准）")
        for p in pending:
            node = _find_node(tree, p)
            if node is not None:
                lines.append(f"- {node['path']} {node['title']}（必填={'是' if node.get('required') else '否'}，type={node.get('field_type')}）")
                if node.get("description"):
                    lines.append(f"  说明：{node['description']}")
                if node.get("field_type") == "enum":
                    lines.append(f"  可选：{node.get('enum_values')}")
    else:
        chap = _top_node(tree, state.get("chapter_path") or "")
        if chap is None:
            lines.append("章节：未知（按通用对话要求判定）")
        else:
            lines.append(f"章节：{chap['path']} {chap['title']}（tier={chap['tier']}）")
            meta: list = []
            _meta_lines(chap, meta)
            if meta:
                lines.append("本章节字段要求：")
                lines.extend(meta)
    return lines


def audit_input(state: PRDState) -> PRDState:
    """阶段内审核 agent：后台判定用户最新一轮回复是否达到当前章节/待澄清字段的要求。"""
    state = dict(state)
    passed, feedback = True, ""
    try:
        from src.llm import get_llm, llm_structured

        msgs = state.get("messages") or []
        replies = [m for m in msgs if isinstance(m, HumanMessage)]
        if not replies:
            state["audit_result"] = {"passed": passed, "feedback": feedback}
            state["audit_passed"] = passed
            return state
        reply = replies[-1].content
        if isinstance(reply, list):
            reply = str(reply)

        res = llm_structured(
            get_llm(),
            AuditResult,
            [
                SystemMessage(content=load_prompt("audit.txt")),
                HumanMessage(content=(
                    "模板要求：\n" + "\n".join(_build_spec(state)) + "\n\n"
                    f"最近对话：\n{_recent_dialogue(msgs)}\n\n"
                    f"用户最新回复：\n{reply}\n\n"
                    "请判定该回复是否达到当前章节的要求，并给出反馈。"
                )),
            ],
        )
        passed, feedback = bool(res.passed), str(res.feedback or "")
        if not passed:
            max_rounds = int(os.getenv("DRAFT_MAX_ROUNDS", "50"))
            if state.get("draft_rounds", 0) >= max_rounds:
                passed = True
    except Exception:
        passed, feedback = True, ""

    state["audit_result"] = {"passed": passed, "feedback": feedback}
    state["audit_passed"] = passed
    return state
