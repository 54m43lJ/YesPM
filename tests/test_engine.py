"""引擎核心流程测试：访谈 / 审核 / 润色 / 命令 / 恢复（节点级 + 端到端）。"""
from __future__ import annotations

import pytest

from yespm_backend.engine.errors import ProtocolError
from _helpers import drive_interview


def create_session(transport, brief="做一个团队任务看板"):
    r = transport.request("session/create", {"brief": brief})
    return r["session_id"], r["snapshot"]


# ---------------------------------------------------------------- 创建与首轮访谈

def test_session_create_reaches_first_await(transport):
    sid, snap = create_session(transport)
    assert sid.startswith("s-")
    assert snap["stage"] == "interview"
    st = transport.request("query/status", {"session_id": sid})
    assert st["stage"] == "interview"
    assert (st["waiting"] or {})["kind"] == "interview"
    unit = st["current_unit"]
    assert unit is not None and unit["path"]


def test_create_emits_full_status_and_await(backend):
    from yespm_backend.protocol.transport import InProcessTransport

    events = []
    tp = InProcessTransport(backend)
    tp.on_notification(lambda e, p: events.append((e, p.get("status_code"))))
    sid, _ = create_session(tp)
    statuses = [e for e in events if e[0] == "session/status"]
    awaits = [e for e in events if e[0] == "session/await_input"]
    assert statuses and any(sc == 2001 for _, sc in statuses)
    assert awaits and awaits[0][1] == 2002


# ---------------------------------------------------------------- input/send 与转录

def test_input_send_advances_to_next_unit(transport):
    sid, _ = create_session(transport)
    transport.request("input/send", {"session_id": sid, "text": "回答一"})
    st = transport.request("query/status", {"session_id": sid})
    assert st["stage"] == "interview"
    assert len(st["units_done"]) == 1


def test_input_is_transcribed_into_value_tree(transport):
    sid, _ = create_session(transport)
    transport.request("input/send", {"session_id": sid, "text": "回答一"})
    tree = transport.request("query/tree", {"session_id": sid})["tree"]
    from yespm_backend.tree.value_tree import find_value_node

    node = find_value_node(tree, "1.2")
    assert node and node["value"] == "mock:产品代号"


# ---------------------------------------------------------------- command/skip

def test_command_skip_forces_transcribe_and_continues(transport):
    sid, _ = create_session(transport)
    r = transport.request("command/skip", {"session_id": sid})
    assert r["accepted"] is True
    st = transport.request("query/status", {"session_id": sid})
    assert st["stage"] == "interview"
    assert len(st["units_done"]) == 1


def test_command_skip_only_in_interview(transport):
    sid, _ = create_session(transport)
    drive_interview(transport, sid)
    with pytest.raises(ProtocolError) as ei:
        transport.request("command/skip", {"session_id": sid})
    assert ei.value.code == 4103


# ---------------------------------------------------------------- command/finish

def test_command_finish_ends_interview(transport):
    sid, _ = create_session(transport)
    transport.request("input/send", {"session_id": sid, "text": "回答一"})
    transport.request("command/finish", {"session_id": sid})
    st = transport.request("query/status", {"session_id": sid})
    assert st["stage"] in ("document_review", "polish", "finished")


# ---------------------------------------------------------------- command/undo

def test_command_undo_reverts_last_transcription(transport):
    sid, _ = create_session(transport)
    transport.request("input/send", {"session_id": sid, "text": "回答一"})
    before = transport.request("query/status", {"session_id": sid})
    assert len(before["units_done"]) == 1
    transport.request("command/undo", {"session_id": sid})
    after = transport.request("query/status", {"session_id": sid})
    assert len(after["units_done"]) == 0
    assert after["current_unit"]["path"] == "1.2"


def test_command_undo_without_history_errors(transport):
    sid, _ = create_session(transport)
    with pytest.raises(ProtocolError) as ei:
        transport.request("command/undo", {"session_id": sid})
    assert ei.value.code == 4103


# ---------------------------------------------------------------- 全流程

def test_full_flow_finishes_with_prd(transport):
    sid, _ = create_session(transport)
    stage = drive_interview(transport, sid)
    assert stage == "finished"
    st = transport.request("query/status", {"session_id": sid})
    assert st["stage"] == "finished"
    prd = transport.request("query/prd", {"session_id": sid})
    assert prd["markdown"]
    assert "转换日志" in prd["markdown"]
    assert "mock:产品名称" in prd["markdown"]


# ---------------------------------------------------------------- 全文档审核回灌

def test_document_review_reentry(backend):
    """审核不通过 → 缺口回灌 → 重新审核通过。"""
    from yespm_backend.protocol.transport import InProcessTransport

    tp = InProcessTransport(backend)
    sid, _ = create_session(tp)
    session = backend._sessions[sid]
    session.llm.behavior["document_review"] = [
        {"passed": False, "gaps": [{"path": "2.1", "dimension": "完整性", "reason": "一句话定位缺失"}]},
        {"passed": True, "gaps": []},
    ]
    drive_interview(tp, sid)
    st = tp.request("query/status", {"session_id": sid})
    assert st["iterations"]["document_review"] == 1
    assert st["stage"] == "finished"
    prd = tp.request("query/prd", {"session_id": sid})
    assert "审核未决清单" not in prd["markdown"]


def test_document_review_unresolved_attached(backend):
    """审核始终不通过 → 超限附未决清单。"""
    from yespm_backend.protocol.transport import InProcessTransport

    tp = InProcessTransport(backend)
    sid, _ = create_session(tp)
    session = backend._sessions[sid]
    session.llm.behavior["document_review"] = [
        {"passed": False, "gaps": [{"path": "2.1", "dimension": "完整性", "reason": "一直缺失"}]}
    ] * 6
    drive_interview(tp, sid)
    st = tp.request("query/status", {"session_id": sid})
    assert st["iterations"]["document_review"] == 3
    assert st["stage"] == "finished"
    prd = tp.request("query/prd", {"session_id": sid})
    assert "审核未决清单" in prd["markdown"]
    assert "2.1" in prd["markdown"].split("审核未决清单")[1]


# ---------------------------------------------------------------- 润色提案确认

def _drive_to_polish_proposal(tp, sid):
    stage = None
    n = 0
    while n < 80:
        st = tp.request("query/status", {"session_id": sid})
        if st["stage"] == "finished" or st.get("ended"):
            stage = st["stage"]
            break
        waiting = (st.get("waiting") or {}).get("kind")
        if waiting == "interview":
            tp.request("input/send", {"session_id": sid, "text": "ok"})
            n += 1
            continue
        if waiting == "proposal":
            stage = "polish-proposal"
            break
        n += 1
    return stage


def test_polish_high_risk_proposal_apply_and_reject(backend):
    """高风险提案逐条确认：apply 生效、reject 不生效。"""
    from yespm_backend.protocol.transport import InProcessTransport

    tp = InProcessTransport(backend)
    sid, _ = create_session(tp)
    session = backend._sessions[sid]
    session.llm.behavior["polish_generator"] = [{
        "proposals": [
            {"id": "p1", "location": "2.1", "scenario": "B2",
             "original": "原文1", "target": "```mermaid\ngraph TD; A-->B\n```", "reason": "流程图化"},
            {"id": "p2", "location": "2.2", "scenario": "D2",
             "original": "原文2长文本冗余", "target": "精简文本", "reason": "删减冗余"},
        ]
    }]
    assert _drive_to_polish_proposal(tp, sid) == "polish-proposal"
    st = tp.request("query/status", {"session_id": sid})
    ids = (st["waiting"] or {})["proposal_ids"]
    assert set(ids) == {"p1", "p2"}

    tp.request("proposal/respond", {"session_id": sid, "proposal_ids": ["p1"], "action": "reject"})
    tp.request("proposal/respond", {"session_id": sid, "proposal_ids": ["p2"], "action": "apply"})

    st = tp.request("query/status", {"session_id": sid})
    assert st["stage"] == "finished"
    log = tp.request("query/conversions", {"session_id": sid})["conversion_log"]
    statuses = {e["id"]: e["status"] for e in log}
    assert statuses["p1"] == "rejected"
    assert statuses["p2"] == "applied"


def test_polish_preserve_field_not_proposed(backend):
    """preserve 字段不出现在提案中。"""
    from yespm_backend.protocol.transport import InProcessTransport

    tp = InProcessTransport(backend)
    sid, _ = create_session(tp)
    session = backend._sessions[sid]
    session.llm.behavior["polish_generator"] = [{
        "proposals": [
            {"id": "px", "location": "4.2", "scenario": "A2", "original": "x", "target": "y", "reason": "r"}
        ]
    }]
    drive_interview(tp, sid)
    log = tp.request("query/conversions", {"session_id": sid})["conversion_log"]
    assert all(e["position"] != "4.2" for e in log)


# ---------------------------------------------------------------- 恢复（checkpoint）

def test_quit_and_resume(backend):
    from yespm_backend.protocol.transport import InProcessTransport

    tp1 = InProcessTransport(backend)
    sid, _ = create_session(tp1)
    tp1.request("input/send", {"session_id": sid, "text": "回答一"})
    tp1.request("input/send", {"session_id": sid, "text": "回答二"})
    tp1.request("session/quit", {"session_id": sid})

    tp2 = InProcessTransport(backend)
    tp2.on_notification(lambda e, p: None)
    r = tp2.request("session/resume", {"session_id": sid})
    st = r["snapshot"]
    assert st["stage"] == "interview"
    assert len(st["units_done"]) == 2
    drive_interview(tp2, sid)
    st = tp2.request("query/status", {"session_id": sid})
    assert st["stage"] == "finished"


# ---------------------------------------------------------------- 错误路径

def test_error_session_not_found(transport):
    with pytest.raises(ProtocolError) as ei:
        transport.request("session/resume", {"session_id": "nonexistent"})
    assert ei.value.code == 4001


def test_error_input_after_finished(transport):
    sid, _ = create_session(transport)
    drive_interview(transport, sid)
    with pytest.raises(ProtocolError) as ei:
        transport.request("input/send", {"session_id": sid, "text": "x"})
    assert ei.value.code == 4102


def test_error_method_unknown(transport):
    with pytest.raises(ProtocolError) as ei:
        transport.request("bogus/method", {})
    assert ei.value.code == 4603


def test_error_proposal_when_none_pending(transport):
    sid, _ = create_session(transport)
    with pytest.raises(ProtocolError) as ei:
        transport.request("proposal/respond", {"session_id": sid, "proposal_ids": ["nope"], "action": "apply"})
    assert ei.value.code == 4103
