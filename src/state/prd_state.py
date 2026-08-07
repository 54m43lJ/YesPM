from __future__ import annotations

from typing import Optional, TypedDict


class PRDState(TypedDict, total=False):
    messages: list
    initial_brief: str
    template: list
    prd_draft: Optional[list]
    filled_paths: list
    pending_paths: list
    review_result: dict
    review_passed: bool
    failed_fields: list
    final_prd: str
    iteration_count: int
    draft_done: bool
    reask_mode: bool


def is_empty(vn: dict) -> bool:
    return (not vn.get("filled")) or vn.get("value") in (None, "", [], {})
