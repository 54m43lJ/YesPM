"""interview_agent + route_user：自然对话访谈与输入路由。

- interview：依据（模板单元描述 + 已转录取值树 + 当前单元对话 + 待澄清缺口）生成提问，
  流式输出后 interrupt 等待用户；声明覆盖完毕（[COVERED]）或对话超限时进入评审。
- route_user：用户输入分发（/skip 跳过评审强制转录、/finish 结束访谈阶段、/undo 回退）。
"""
from __future__ import annotations

import copy
import re

from langgraph.types import interrupt

from ..prompts.loader import load_prompt
from .common import conversation_text, unit_description_text, unit_existing_text

def make_interview_node(llm, cfg):
    """访谈提问节点：LLM 生成提问（一次），结果暂存状态，交由 await_input 中断等待用户。"""
    system_tpl = load_prompt("interview")

    def node(state: dict) -> dict:
        unit = state.get("current_unit") or {}
        template = state.get("template") or []
        value_tree = state.get("prd_draft") or []
        conv = state.get("unit_conversation") or []
        gaps = state.get("interview_gaps") or []
        gap_text = "\n".join(
            f"- {g.get('path')} [{g.get('dimension')}] {g.get('reason')}" for g in gaps
        ) or "(无)"

        user = (
            f"单元：{unit.get('label', '')}\n\n"
            f"单元描述：\n{unit_description_text(template, unit)}\n\n"
            f"现有取值：\n{unit_existing_text(value_tree, unit)}\n\n"
            f"对话历史：\n{conversation_text(conv)}"
        )
        system = system_tpl.replace("{GAP_TARGETS}", gap_text)

        text = ""
        try:
            for chunk in llm.stream(system, user):
                print(chunk, end="", flush=True)
                text += chunk
        except KeyboardInterrupt:
            print("\n[流式输出已中断]\n", flush=True)
        question = text.strip()
        complete = "[COVERED]" in question
        question = re.sub(r"\[(?:COVERED|CONTINUE)\]\s*$", "", question).strip()
        if not question:
            question = "请继续补充本单元信息。"
        return {
            "pending_question": question,
            "interview_complete": complete,
        }

    return node


def make_await_input_node():
    """中断等待节点：interrupt 等待用户输入；恢复后返回输入值。

    与 LLM 调用分离，避免 langgraph resume 语义下 LLM 重复调用。
    """

    def node(state: dict) -> dict:
        unit = state.get("current_unit") or {}
        question = state.get("pending_question") or "请继续补充本单元信息。"
        payload = {
            "kind": "interview",
            "unit": unit.get("label", ""),
            "question": question,
            "streamed": True,
        }
        reply = interrupt(payload)
        return {
            "last_user_input": reply if isinstance(reply, str) else "",
            "pending_question": "",
        }

    return node


def _do_undo(state: dict) -> dict:
    """回退最近一次转录：恢复取值树与进度，回到该单元重新访谈。"""
    stack = list(state.get("undo_stack") or [])
    while stack:
        entry = stack.pop()
        if entry.get("kind") == "transcribe":
            return {
                "prd_draft": copy.deepcopy(entry.get("snapshot_tree") or []),
                "units_done": list(entry.get("snapshot_units_done") or []),
                "current_unit": copy.deepcopy(entry.get("unit")),
                "unit_conversation": [],
                "unit_turns": 0,
                "interview_complete": False,
                "interview_gaps": [],
                "undo_stack": stack,
                "undo_applied": True,
            }
    return {"undo_applied": False}


def route_user_node(state: dict) -> dict:
    user_input = (state.get("last_user_input") or "").strip()
    if user_input in ("/finish", "/skip"):
        return {}
    if user_input == "/undo":
        return _do_undo(state)
    conv = list(state.get("unit_conversation") or [])
    conv.append({"role": "user", "content": state.get("last_user_input") or ""})
    return {
        "unit_conversation": conv,
        "unit_turns": (state.get("unit_turns") or 0) + 1,
        "undo_applied": False,
    }


def route_after_user(state: dict, cfg) -> str:
    """用户输入后的路由：命令 / 评审触发 / 继续访谈。"""
    user_input = (state.get("last_user_input") or "").strip()
    if user_input == "/finish":
        return "interview_done"
    if user_input == "/skip":
        return "transcribe"
    if user_input == "/undo":
        return "interview"
    if state.get("interview_complete") and not (state.get("unit_conversation") or []):
        return "transcribe"
    if state.get("interview_complete") or (state.get("unit_turns") or 0) >= cfg.unit_max_turns:
        return "unit_review"
    return "interview"
