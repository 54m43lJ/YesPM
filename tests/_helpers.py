"""测试辅助函数。"""
from __future__ import annotations


def drive_interview(transport, session_id, text="ok", max_turns=60):
    """驱动访谈至非 interview 等待态（审核/润色/完成），返回到达的 stage。

    遇到润色提案确认自动 apply。
    """
    n = 0
    while n < max_turns:
        st = transport.request("query/status", {"session_id": session_id})
        stage = st.get("stage")
        waiting = (st.get("waiting") or {}).get("kind")
        if stage == "finished" or st.get("ended"):
            return stage
        if waiting == "interview":
            transport.request("input/send", {"session_id": session_id, "text": text})
            n += 1
            continue
        if waiting == "proposal":
            ids = (st.get("waiting") or {}).get("proposal_ids") or []
            transport.request("proposal/respond", {
                "session_id": session_id, "proposal_ids": ids, "action": "apply",
            })
            continue
        return stage  # document_review 运行中等
    raise RuntimeError("drive_interview 超过最大轮数")
