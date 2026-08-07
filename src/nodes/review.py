from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from src.state.prd_state import PRDState, is_empty
from src.tools.template_loader import load_prompt


class ReviewItem(BaseModel):
    path: str
    dimension: str
    reason: str


class ReviewResult(BaseModel):
    failed_fields: list[ReviewItem]
    summary: str


def _check_completeness(tree: list, failed: list) -> None:
    def walk(nodes):
        for vn in nodes:
            nt = vn["node_type"]
            if nt == "field":
                if vn.get("required") and is_empty(vn):
                    failed.append({
                        "path": vn["path"],
                        "dimension": "completeness",
                        "reason": f"必填字段「{vn['title']}」未填写",
                    })
                elif vn.get("field_type") == "table" and vn.get("min_items") and not is_empty(vn):
                    rows = vn["value"] if isinstance(vn["value"], list) else []
                    if len(rows) < vn["min_items"]:
                        failed.append({
                            "path": vn["path"],
                            "dimension": "completeness",
                            "reason": f"至少需要 {vn['min_items']} 行，现有 {len(rows)} 行",
                        })
            elif nt == "repeat":
                if vn.get("min_items") and vn.get("count", 0) < vn["min_items"]:
                    failed.append({
                        "path": vn["path"],
                        "dimension": "completeness",
                        "reason": f"至少需要 {vn['min_items']} 个「{vn.get('item_label') or '项'}」，现有 {vn.get('count', 0)}",
                    })
                if vn.get("count_known"):
                    walk(vn.get("children") or [])
            elif nt in ("group", "instance"):
                walk(vn.get("children") or [])

    walk(tree)


def _serialize(tree: list) -> str:
    lines: list = []

    def walk(nodes):
        for vn in nodes:
            nt = vn["node_type"]
            if nt == "field":
                if not is_empty(vn):
                    v = vn["value"]
                    if isinstance(v, list):
                        v = "; ".join(str(r) for r in v)
                    lines.append(f"{vn['path']} {vn['title']}: {v}")
                elif vn.get("required"):
                    lines.append(f"{vn['path']} {vn['title']}: <空/必填>")
            elif nt == "repeat":
                if vn.get("count_known"):
                    walk(vn.get("children") or [])
            elif nt in ("group", "instance"):
                walk(vn.get("children") or [])

    walk(tree)
    return "\n".join(lines)


def review_prd(state: PRDState) -> PRDState:
    state = dict(state)
    tree = state["prd_draft"]

    failed: list = []
    _check_completeness(tree, failed)

    summary = "完整性校验完成。"
    try:
        from src.llm import get_llm

        llm = get_llm().with_structured_output(ReviewResult)
        res = llm.invoke([
            SystemMessage(content=load_prompt("review.txt")),
            HumanMessage(content=(
                f"产品简述：{state.get('initial_brief', '')}\n\n"
                f"取值树（path title: value）：\n{_serialize(tree)}"
            )),
        ])
        for it in res.failed_fields:
            failed.append({"path": it.path, "dimension": it.dimension, "reason": it.reason})
        summary = res.summary or summary
    except Exception as e:
        summary = f"LLM 一致性/可行性审核跳过（{e}），仅完成完整性校验。"

    state["failed_fields"] = failed
    state["review_passed"] = len(failed) == 0
    state["review_result"] = {"summary": summary, "failed_count": len(failed)}
    state["iteration_count"] = state.get("iteration_count", 0) + 1
    state["draft_done"] = False

    if failed:
        state["reask_mode"] = True
        state["pending_paths"] = [f["path"] for f in failed]
    else:
        state["reask_mode"] = False
        state["pending_paths"] = []
    return state
