"""PRDState：贯穿全流程的 LangGraph 状态通道。

引擎与图节点共享该状态；命令（skip/finish/undo）以结构化标志位表达，不在图中
解析任何命令文本（命令结构化原则）。
"""
from __future__ import annotations

from typing import Any, TypedDict


class PRDState(TypedDict, total=False):
    # 会话与上下文
    brief: str                       # 用户初始简述
    template: list[dict]             # 归一化模板树（含 path）
    prd_draft: list[dict]            # 模板同构取值树（唯一中间态）
    phase: str                       # interview | review | polish | finished

    # 访谈阶段（图 ①）
    units_queue: list[dict]          # 待访谈单元
    current_unit: dict | None        # 当前单元
    units_done: list[str]            # 已完成单元 id
    unit_conversation: list[dict]    # 当前单元对话（转录后即弃）
    unit_turns: int                  # 当前单元对话轮数
    interview_complete: bool         # interview_agent 声明覆盖完毕
    pending_question: str            # interview 节点产出的提问（await_input 中断前暂存）
    last_user_input: str | None      # 最近一次用户输入（中断恢复值）
    interview_gaps: list[dict]       # 当前单元评审缺口（针对性追问）

    # 结构化命令标志（引擎写入，图路由读取；非命令文本）
    skip_requested: bool             # command/skip
    finish_requested: bool           # command/finish
    undo_applied: bool               # command/undo 已生效（路由用）

    # 全文档审核（阶段 ②）
    gap_list: list[dict]             # 全文档审核缺口（也承载回灌时的目标）
    doc_review_iterations: int       # 审核往返计数
    unresolved_gaps: list[dict]      # 超限未决缺口（渲染时附未决清单）

    # 渲染与润色（阶段 ③）
    polish_proposals: list[dict]     # 转换提案（含分级与保真评估结果）
    pending_proposals: list[dict]    # 待用户确认的高风险提案
    polish_blocks: list[dict]        # 渲染块（润色期间跨中断保持，已含已应用转换）
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
        "pending_question": "",
        "last_user_input": None,
        "interview_gaps": [],
        "skip_requested": False,
        "finish_requested": False,
        "undo_applied": False,
        "gap_list": [],
        "doc_review_iterations": 0,
        "unresolved_gaps": [],
        "polish_proposals": [],
        "pending_proposals": [],
        "polish_blocks": [],
        "conversion_log": [],
        "final_prd": None,
        "undo_stack": [],
    }
