"""PRDState：贯穿全流程的 LangGraph 状态通道。"""
from __future__ import annotations

from typing import Any, TypedDict


class PRDState(TypedDict, total=False):
    # 会话与上下文
    brief: str                       # 用户初始简述
    template: list[dict]             # 归一化模板树（含 path）
    prd_draft: list[dict]            # 模板同构取值树（唯一中间态）
    phase: str                       # interview | review | render

    # 访谈阶段（Node ①）
    units_queue: list[dict]          # 待访谈单元
    current_unit: dict | None        # 当前单元
    units_done: list[str]            # 已完成单元 id
    unit_conversation: list[dict]    # 当前单元对话（转录后即弃）
    unit_turns: int                  # 当前单元对话轮数
    interview_complete: bool         # interview_agent 声明覆盖完毕
    last_user_input: str | None      # 最近一次用户输入（中断恢复值）
    interview_gaps: list[dict]       # 当前单元评审缺口（针对性追问）
    undo_applied: bool               # /undo 是否生效（路由用）

    # 全文档审核（Node ②）
    gap_list: list[dict]             # 全文档审核缺口
    doc_review_iterations: int       # 审核往返计数
    unresolved_gaps: list[dict]      # 超限未决缺口（渲染时附未决清单）

    # 渲染与润色（Node ③）
    polish_proposals: list[dict]     # 转换提案
    conversion_log: list[dict]       # 已应用/已丢弃转换记录
    final_prd: str | None            # 最终输出

    # 回退
    undo_stack: list[dict]           # 最近转录/转换快照


def default_state(template: list[dict], brief: str = "") -> dict[str, Any]:
    return {
        "brief": brief,
        "template": template,
        "prd_draft": [],
        "phase": "interview",
        "units_queue": [],
        "current_unit": None,
        "units_done": [],
        "unit_conversation": [],
        "unit_turns": 0,
        "interview_complete": False,
        "last_user_input": None,
        "interview_gaps": [],
        "undo_applied": False,
        "gap_list": [],
        "doc_review_iterations": 0,
        "unresolved_gaps": [],
        "polish_proposals": [],
        "conversion_log": [],
        "final_prd": None,
        "undo_stack": [],
    }
