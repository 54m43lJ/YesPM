"""集成走查（TEST-DRIVEN-DEVELOPMENT.md §2 场景 S01–S12）的可执行对照。

文档定义集成测试为「自然语言脚本」，由开发收尾阶段逐场景走查。本文件以协议层
（InProcessTransport，CLI 同一传输）按各场景的「操作 → 预期」机械执行，作为
走查的可执行佐证；任一断言失败即对应场景未通过。
"""
from __future__ import annotations

import pytest

from yespm_backend.engine.errors import ProtocolError


def _status(tp, sid):
    return tp.request("query/status", {"session_id": sid})


def _await_kind(st):
    return (st.get("waiting") or {}).get("kind")


# S01 新建会话，进入首轮访谈
def test_S01_new_session_first_interview(transport):
    r = transport.request("session/create", {"brief": "做一个团队任务看板，支持任务状态流转"})
    sid = r["session_id"]
    st = _status(transport, sid)
    assert st["stage"] == "interview"
    assert _await_kind(st) == "interview"
    assert st["current_unit"] is not None  # 带当前单元上下文


# S02 逐单元访谈，验证转录写入取值树
def test_S02_unit_interview_transcribes(transport):
    sid = transport.request("session/create", {"brief": "x"})["session_id"]
    transport.request("input/send", {"session_id": sid, "text": "回答"})
    tree = transport.request("query/tree", {"session_id": sid})["tree"]
    from yespm_backend.tree.value_tree import find_value_node

    assert find_value_node(tree, "1.2")["value"]  # 已答字段有值


# S03 /skip 跳过当前单元，流程继续
def test_S03_skip_continues(transport):
    sid = transport.request("session/create", {"brief": "x"})["session_id"]
    transport.request("command/skip", {"session_id": sid})
    st = _status(transport, sid)
    assert st["stage"] == "interview" and len(st["units_done"]) == 1  # 未结束


# S04 /finish 进入全文档审核
def test_S04_finish_enters_review(transport):
    sid = transport.request("session/create", {"brief": "x"})["session_id"]
    transport.request("input/send", {"session_id": sid, "text": "a"})
    transport.request("command/finish", {"session_id": sid})
    st = _status(transport, sid)
    assert st["stage"] in ("document_review", "polish", "finished")


# S05 审核回灌（不通过 → 重访 gap 单元 → 通过）
def test_S05_review_reentry(backend):
    from yespm_backend.protocol.transport import InProcessTransport
    from _helpers import drive_interview

    tp = InProcessTransport(backend)
    sid = tp.request("session/create", {"brief": "x"})["session_id"]
    backend._sessions[sid].llm.behavior["document_review"] = [
        {"passed": False, "gaps": [{"path": "2.1", "dimension": "完整性", "reason": "缺"}]},
        {"passed": True, "gaps": []},
    ]
    drive_interview(tp, sid)
    assert _status(tp, sid)["iterations"]["document_review"] == 1


# S06/S08 润色低风险自动 + 最终产出（mock 默认无提案，直接产出最终文档）
def test_S06_S08_polish_and_final(transport):
    from _helpers import drive_interview

    sid = transport.request("session/create", {"brief": "x"})["session_id"]
    assert drive_interview(transport, sid) == "finished"
    prd = transport.request("query/prd", {"session_id": sid})
    assert prd["markdown"] and "转换日志" in prd["markdown"]
    conv = transport.request("query/conversions", {"session_id": sid})["conversion_log"]
    assert isinstance(conv, list)


# S07 高风险提案逐条确认
def test_S07_high_risk_proposal_confirm(backend):
    from yespm_backend.protocol.transport import InProcessTransport

    tp = InProcessTransport(backend)
    sid = tp.request("session/create", {"brief": "x"})["session_id"]
    backend._sessions[sid].llm.behavior["polish_generator"] = [{
        "proposals": [{"id": "p1", "location": "2.1", "scenario": "B2",
                       "original": "o", "target": "```mermaid\ngraph TD;A-->B\n```", "reason": "r"}]
    }]
    # 驱动到提案确认
    n = 0
    while n < 80:
        st = _status(tp, sid)
        if st["stage"] == "finished":
            pytest.fail("未进入提案确认")
        w = _await_kind(st)
        if w == "interview":
            tp.request("input/send", {"session_id": sid, "text": "ok"}); n += 1; continue
        if w == "proposal":
            break
        n += 1
    # 拒绝该提案 → 进入 finished，且提案记录为 rejected
    tp.request("proposal/respond", {"session_id": sid, "proposal_ids": ["p1"], "action": "reject"})
    assert _status(tp, sid)["stage"] == "finished"
    log = tp.request("query/conversions", {"session_id": sid})["conversion_log"]
    assert log[0]["status"] == "rejected"


# S09 断点续聊
def test_S09_resume(backend):
    from yespm_backend.protocol.transport import InProcessTransport
    from _helpers import drive_interview

    tp = InProcessTransport(backend)
    sid = tp.request("session/create", {"brief": "x"})["session_id"]
    tp.request("input/send", {"session_id": sid, "text": "a"})
    tp.request("input/send", {"session_id": sid, "text": "b"})
    tp.request("session/quit", {"session_id": sid})

    tp2 = InProcessTransport(backend)
    tp2.on_notification(lambda e, p: None)
    snap = tp2.request("session/resume", {"session_id": sid})["snapshot"]
    assert snap["stage"] == "interview" and len(snap["units_done"]) == 2
    drive_interview(tp2, sid)
    assert _status(tp2, sid)["stage"] == "finished"


# S10 错误路径
def test_S10_error_paths(transport):
    sid = transport.request("session/create", {"brief": "x"})["session_id"]
    # 4001 不存在
    with pytest.raises(ProtocolError) as ei:
        transport.request("session/resume", {"session_id": "ghost"})
    assert ei.value.code == 4001
    # 4603 方法不存在
    with pytest.raises(ProtocolError) as ei:
        transport.request("no/such", {})
    assert ei.value.code == 4603


# S11 /undo 回退
def test_S11_undo(transport):
    sid = transport.request("session/create", {"brief": "x"})["session_id"]
    transport.request("input/send", {"session_id": sid, "text": "a"})
    transport.request("command/undo", {"session_id": sid})
    assert len(_status(transport, sid)["units_done"]) == 0
    # 无记录再 undo → 4103
    with pytest.raises(ProtocolError) as ei:
        transport.request("command/undo", {"session_id": sid})
    assert ei.value.code == 4103


# S12 /quit 干净退出 + 可恢复
def test_S12_quit(backend):
    from yespm_backend.protocol.transport import InProcessTransport

    tp = InProcessTransport(backend)
    sid = tp.request("session/create", {"brief": "x"})["session_id"]
    r = tp.request("session/quit", {"session_id": sid})
    assert r["ended"] is True
    # 恢复仍可
    tp2 = InProcessTransport(backend)
    tp2.on_notification(lambda e, p: None)
    snap = tp2.request("session/resume", {"session_id": sid})["snapshot"]
    assert snap["stage"] == "interview"
