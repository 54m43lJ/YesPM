from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.state.prd_state import PRDState
from src.tools.template_loader import load_prompt


def polish_prd(state: PRDState) -> PRDState:
    """对 finalize_prd 产出的 Markdown 初稿做 LLM 润色定稿（仅表达层面，不改结构与内容）。"""
    state = dict(state)
    md = state.get("final_prd") or ""
    if not md.strip():
        return state
    try:
        from src.llm import get_llm

        res = get_llm().invoke([
            SystemMessage(content=load_prompt("polish.txt")),
            HumanMessage(content=md),
        ])
        content = getattr(res, "content", None)
        if isinstance(content, str) and content.strip():
            md = content
    except Exception:
        pass
    state["final_prd"] = md
    return state
