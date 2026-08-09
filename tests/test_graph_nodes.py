"""图节点单元测试：命令标志路由、转录修订语义（节点级，TDD 颗粒度）。"""
from __future__ import annotations

from types import SimpleNamespace

from yespm_backend.graph.nodes import (
    after_input_node,
    interview_done_node,
    pick_next_unit_node,
    route_after_input,
    route_review,
    start_node,
)


def _cfg(max_turns=5):
    return SimpleNamespace(unit_max_turns=max_turns)


# ---------------------------------------------------------------- start / pick

def test_start_builds_units_when_empty(template, value_tree):
    state = {"template": template, "prd_draft": value_tree, "units_queue": [], "current_unit": None, "gap_list": []}
    out = start_node(state)
    assert out["units_queue"]  # 默认模板有访谈单元
    assert out["gap_list"] == []


def test_start_gap_driven_mode(template, value_tree):
    state = {
        "template": template, "prd_draft": value_tree,
        "units_queue": [], "current_unit": None,
        "gap_list": [{"path": "2.1", "dimension": "完整性", "reason": "r"}],
    }
    out = start_node(state)
    ids = [u["id"] for u in out["units_queue"]]
    assert ids == ["field:2.1"]  # 缺口驱动：仅 gap 单元
    assert out["gap_list"] == []  # gap_list 清空（已转单元）


def test_start_keeps_existing_queue(template, value_tree):
    state = {
        "template": template, "prd_draft": value_tree,
        "units_queue": [{"id": "x"}], "current_unit": None, "gap_list": [],
    }
    assert start_node(state) == {}  # 已有队列不重建


def test_pick_next_unit_resets_unit_state():
    state = {"units_queue": [{"id": "u1"}], "current_unit": None}
    out = pick_next_unit_node(state)
    assert out["current_unit"]["id"] == "u1"
    assert out["units_queue"] == []
    assert out["unit_turns"] == 0 and out["unit_conversation"] == []


def test_pick_next_unit_noop_when_current_exists():
    state = {"units_queue": [], "current_unit": {"id": "u1"}}
    assert pick_next_unit_node(state) == {}


def test_interview_done_sets_review_phase():
    out = interview_done_node({"finish_requested": True})
    assert out["phase"] == "review"
    assert out["current_unit"] is None
    assert out["finish_requested"] is False  # 清除标志


# ---------------------------------------------------------------- after_input + route（命令结构化）

def test_after_input_appends_normal_user_text():
    state = {"last_user_input": "回答", "unit_conversation": [{"role": "user", "content": "前"}], "unit_turns": 1}
    out = after_input_node(state)
    assert out["unit_conversation"][-1] == {"role": "user", "content": "回答"}
    assert out["unit_turns"] == 2


def test_after_input_skip_path_does_not_append():
    state = {"last_user_input": "", "skip_requested": True, "unit_conversation": [{"role": "user", "content": "x"}]}
    assert after_input_node(state) == {}


def test_after_input_finish_path_does_not_append():
    state = {"last_user_input": "", "finish_requested": True}
    assert after_input_node(state) == {}


def test_after_input_undo_path_does_not_append():
    state = {"last_user_input": "", "undo_applied": True}
    assert after_input_node(state) == {}


def test_route_finish_requested():
    assert route_after_input({"finish_requested": True}, _cfg()) == "interview_done"


def test_route_skip_requested():
    assert route_after_input({"skip_requested": True}, _cfg()) == "transcribe"


def test_route_undo_applied():
    assert route_after_input({"undo_applied": True}, _cfg()) == "interview"


def test_route_interview_complete_to_review():
    assert route_after_input({"interview_complete": True, "unit_conversation": [{"x": 1}]}, _cfg()) == "unit_review"


def test_route_max_turns_force_review():
    assert route_after_input({"unit_turns": 5, "unit_conversation": [{"x": 1}]}, _cfg(5)) == "unit_review"


def test_route_continue_interview():
    assert route_after_input({"unit_turns": 1, "unit_conversation": [{"x": 1}]}, _cfg()) == "interview"


# ---------------------------------------------------------------- unit_review 路由

def test_route_review_gaps_back_to_interview():
    assert route_review({"interview_gaps": [{"path": "x"}]}) == "interview"


def test_route_review_mature_to_transcribe():
    assert route_review({"interview_gaps": []}) == "transcribe"
