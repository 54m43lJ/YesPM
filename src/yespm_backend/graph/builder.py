"""主图构建：访谈阶段单元循环（图 ①）。

interview ↔ await_input(interrupt) → after_input → unit_review/transcribe 循环；
以产出完整结构化取值树为终点（interview_done 置 phase=review → END）。
全文档审核回灌与渲染润色（图 ②③）为引擎编排的轻量逻辑，见 polish/。
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..state import PRDState
from .nodes import (
    after_input_node,
    interview_done_node,
    make_await_input_node,
    make_interview_node,
    make_transcribe_node,
    make_unit_review_node,
    pick_next_unit_node,
    route_after_input,
    route_review,
    start_node,
)


def build_interview_graph(cfg, llm, checkpointer, emit):
    """编译访谈主图。`emit` 为引擎注入的事件发布回调。"""
    g = StateGraph(PRDState)
    g.add_node("start", start_node)
    g.add_node("pick_next_unit", pick_next_unit_node)
    g.add_node("interview", make_interview_node(llm, cfg, emit))
    g.add_node("await_input", make_await_input_node())
    g.add_node("after_input", after_input_node)
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
    g.add_edge("await_input", "after_input")
    g.add_conditional_edges(
        "after_input",
        lambda s: route_after_input(s, cfg),
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
