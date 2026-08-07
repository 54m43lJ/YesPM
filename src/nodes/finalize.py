from __future__ import annotations

from src.state.prd_state import PRDState
from src.tools.renderer import render


def finalize_prd(state: PRDState) -> PRDState:
    state = dict(state)
    body = render(state["prd_draft"])

    failed = state.get("failed_fields") or []
    if failed:
        note = "\n\n---\n\n> **[审核未决清单]**（已达上限或仍存问题，请人工复核）：\n"
        for f in failed:
            note += f"> - `{f.get('path')}` [{f.get('dimension', '')}] {f.get('reason', '')}\n"
        body = body.rstrip() + "\n" + note

    state["final_prd"] = body
    return state
