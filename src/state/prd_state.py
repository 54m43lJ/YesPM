from __future__ import annotations

from typing import Optional, TypedDict


class PRDState(TypedDict, total=False):
    messages: list
    initial_brief: str
    template: list
    prd_draft: Optional[list]
    filled_paths: list
    pending_paths: list
    pending_attempts: dict
    audit_result: dict
    audit_passed: bool
    preview_pending: list
    preview_items: list
    review_result: dict
    review_passed: bool
    failed_fields: list
    final_prd: str
    iteration_count: int
    draft_done: bool
    reask_mode: bool
    await_audit: bool
    audit_path: str
    draft_rounds: int
    chapter_path: str
    session_active: bool
    session_kind: str
    session_rounds: int
    need_input: bool
    input_kind: str
    ui_payload: dict
    user_answer: object


def is_empty(vn: dict) -> bool:
    return (not vn.get("filled")) or vn.get("value") in (None, "", [], {})
