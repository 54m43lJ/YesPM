"""主图（LangGraph）：访谈阶段（Node ①）——interview → unit_review → transcribe 单元循环。

- 含缺口驱动模式：带 gap_list 进入时按 gap 归并构造访谈单元（跳过无 gap 单元）；
- 以产出完整结构化取值树为终点（interview_done 置 phase=review）；
- 全文档审核回灌循环与渲染润色（Node ②③）为图外简单循环，见 pipeline.py。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes.interview import (
    make_await_input_node,
    make_interview_node,
    route_after_user,
    route_user_node,
)
from .nodes.unit_review import make_unit_review_node, route_review
from .nodes.transcribe import make_transcribe_node
from .state import PRDState
from .tools.units import build_gap_units, build_units


def start_node(state: dict) -> dict:
    units_queue = state.get("units_queue") or []
    current = state.get("current_unit")
    if units_queue or current:
        return {}  # 恢复续聊 / 进行中：沿用既有队列
    template = state.get("template") or []
    value_tree = state.get("prd_draft") or []
    gaps = state.get("gap_list") or []
    if gaps:
        units = build_gap_units(template, value_tree, [g.get("path") for g in gaps])
    else:
        units = build_units(template, value_tree)
    return {"units_queue": units, "gap_list": []}


def pick_next_unit_node(state: dict) -> dict:
    if state.get("current_unit"):
        return {}
    queue = list(state.get("units_queue") or [])
    if not queue:
        return {}
    unit = queue.pop(0)
    return {
        "units_queue": queue,
        "current_unit": unit,
        "unit_conversation": [],
        "unit_turns": 0,
        "interview_complete": False,
        "interview_gaps": [],
    }


def interview_done_node(state: dict) -> dict:
    return {
        "phase": "review",
        "current_unit": None,
        "units_queue": [],
        "unit_conversation": [],
    }


def build_graph(cfg, llm, checkpointer):
    g = StateGraph(PRDState)
    g.add_node("start", start_node)
    g.add_node("pick_next_unit", pick_next_unit_node)
    g.add_node("interview", make_interview_node(llm, cfg))
    g.add_node("await_input", make_await_input_node())
    g.add_node("route_user", route_user_node)
    g.add_node("unit_review", make_unit_review_node(llm))
    g.add_node("transcribe", make_transcribe_node(llm))
    g.add_node("interview_done", interview_done_node)

    g.add_edge(START, "start")
    g.add_conditional_edges(
        "start",
        lambda s: "pick_next_unit" if (s.get("units_queue") or s.get("current_unit")) else "interview_done",
        {"pick_next_unit": "pick_next_unit", "interview_done": "interview_done"},
    )
    g.add_conditional_edges(
        "pick_next_unit",
        lambda s: "interview" if s.get("current_unit") else "interview_done",
        {"interview": "interview", "interview_done": "interview_done"},
    )
    g.add_edge("interview", "await_input")
    g.add_edge("await_input", "route_user")
    g.add_conditional_edges(
        "route_user",
        lambda s: route_after_user(s, cfg),
        {
            "interview": "interview",
            "unit_review": "unit_review",
            "transcribe": "transcribe",
            "interview_done": "interview_done",
        },
    )
    g.add_conditional_edges(
        "unit_review",
        route_review,
        {"interview": "interview", "transcribe": "transcribe"},
    )
    g.add_edge("transcribe", "pick_next_unit")
    g.add_edge("interview_done", END)

    return g.compile(checkpointer=checkpointer)
