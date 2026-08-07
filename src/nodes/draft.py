from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from src.state.prd_state import PRDState
from src.tools.template_loader import build_skeleton, expand_instances, load_prompt


def _question_text(vn: dict) -> str:
    q = vn.get("question") or "请说明{title}："
    return q.replace("{title}", vn["title"]).replace("{n}", str(vn.get("instance_no") or ""))


def _field_prompt(vn: dict) -> str:
    lines = [f"【{vn['path']} {vn['title']}】", _question_text(vn)]
    if vn.get("description"):
        lines.append(f"  说明：{vn['description']}")
    if vn.get("example"):
        lines.append(f"  示例：{vn['example']}")
    if vn.get("field_type") == "enum":
        lines.append(f"  可选：{vn.get('enum_values')}")
    if vn.get("field_type") == "table":
        lines.append(f"  表格列（按此顺序，用 | 分隔，每行一条，可省略表头）：{vn.get('columns')}")
    return "\n".join(lines)


def _find_action(nodes: list, pending: set, reask: bool):
    for vn in nodes:
        nt = vn["node_type"]
        if nt == "repeat":
            if not vn["count_known"]:
                if (not reask) or (vn["path"] in pending):
                    return {"kind": "count", "node": vn}
            else:
                r = _find_action(vn.get("children") or [], pending, reask)
                if r:
                    return r
        elif nt in ("group", "instance"):
            r = _find_action(vn.get("children") or [], pending, reask)
            if r:
                return r
        elif nt == "field":
            if reask:
                if vn["path"] in pending:
                    return {"kind": "field", "node": vn}
            elif vn["tier"] in ("P0", "P1") and not vn["filled"]:
                return {"kind": "field", "node": vn}
    return None


def _parse_value(vn: dict, ans: str) -> Any:
    ans = (ans or "").strip()
    ft = vn.get("field_type", "text")
    if ft == "enum":
        ev = vn.get("enum_values") or []
        if ans in ev:
            return ans
        for e in ev:
            if ans and e in ans:
                return e
        return ans
    if ft == "table":
        cols = vn.get("columns") or []
        rows: list = []
        for line in ans.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("|"):
                line = line[1:]
            if line.endswith("|"):
                line = line[:-1]
            cells = [c.strip() for c in line.split("|")]
            if cells and all(set(c) <= set("-: ") for c in cells):
                continue
            row = {}
            for i, col in enumerate(cols):
                row[col] = cells[i] if i < len(cells) else ""
            rows.append(row)
        return rows
    return ans


def _collect_filled(nodes: list, out: list = None) -> list:
    if out is None:
        out = []
    for vn in nodes:
        if vn["node_type"] == "field":
            if vn["filled"]:
                out.append(vn["path"])
        elif vn["node_type"] == "repeat":
            if vn["count_known"]:
                _collect_filled(vn["children"], out)
        else:
            _collect_filled(vn.get("children") or [], out)
    return out


def _p2_fields(nodes: list, out: list = None) -> list:
    if out is None:
        out = []
    for vn in nodes:
        if vn["node_type"] == "field":
            if vn["tier"] == "P2" and not vn["filled"]:
                out.append(vn)
        elif vn["node_type"] == "repeat":
            if vn["count_known"]:
                _p2_fields(vn["children"], out)
        else:
            _p2_fields(vn.get("children") or [], out)
    return out


def _context_lines(nodes: list, lines: list) -> None:
    for vn in nodes:
        if vn["node_type"] == "field":
            if vn["filled"] and vn.get("value") not in (None, "", [], {}):
                v = vn["value"]
                if isinstance(v, list):
                    v = "; ".join(str(r) for r in v)
                lines.append(f"{vn['path']} {vn['title']}: {v}")
        elif vn["node_type"] == "repeat":
            if vn["count_known"]:
                _context_lines(vn["children"], lines)
        else:
            _context_lines(vn.get("children") or [], lines)


def _infer_p2(state: PRDState, tree: list) -> None:
    p2 = _p2_fields(tree)
    if not p2:
        return
    try:
        from pydantic import BaseModel, Field

        from src.llm import get_llm
        from langchain_core.messages import HumanMessage, SystemMessage

        class P2Out(BaseModel):
            values: dict = Field(description="字段路径到推断值的映射")

        ctx: list = []
        _context_lines(tree, ctx)
        specs = "\n".join(f"- {v['path']} {v['title']} (type={v.get('field_type')})" for v in p2)
        llm = get_llm().with_structured_output(P2Out)
        res = llm.invoke([
            SystemMessage(content=load_prompt("draft.txt")),
            HumanMessage(content=(
                f"产品简述：{state.get('initial_brief', '')}\n\n"
                f"已收集信息：\n" + "\n".join(ctx) + "\n\n"
                f"请推断以下 P2 字段取值（无法推断的不要包含）：\n{specs}"
            )),
        ])
        idx = {v["path"]: v for v in p2}
        for path, val in (res.values or {}).items():
            node = idx.get(path)
            if node is not None:
                node["value"] = val
                node["filled"] = True
    except Exception:
        for v in p2:
            if not v["filled"] and v.get("default") is not None:
                v["value"] = v["default"]
                v["filled"] = True


def draft_prd(state: PRDState) -> PRDState:
    state = dict(state)
    tree = state.get("prd_draft")
    if not tree:
        tree = build_skeleton(state["template"])
        state["prd_draft"] = tree
        state["filled_paths"] = []

    pending = set(state.get("pending_paths") or [])
    reask = bool(pending) or state.get("reask_mode", False)

    action = _find_action(tree, pending, reask)

    if action is None:
        if not reask:
            _infer_p2(state, tree)
        state["draft_done"] = True
        state["reask_mode"] = False
        state["pending_paths"] = []
        state["filled_paths"] = _collect_filled(tree)
        return state

    node = action["node"]

    if action["kind"] == "count":
        mi = node.get("min_items")
        prompt = (
            f"【{node['path']} {node['title']}】\n"
            f"需要几个「{node.get('item_label') or '项'}」？"
            + (f"（不少于 {mi}）" if mi else "")
            + "\n请输入数字。"
        )
        ans = interrupt({"prompt": prompt, "kind": "count"})
        try:
            count = max(int(str(ans).strip()), mi or 1)
        except Exception:
            count = mi or 1
        expand_instances(node, count)
        state["filled_paths"] = _collect_filled(tree)
        return state

    prompt = _field_prompt(node)
    ans = interrupt({"prompt": prompt, "kind": "field", "path": node["path"]})

    if (ans or "").strip() == "":
        node["value"] = None
        node["filled"] = True
    else:
        val = _parse_value(node, ans)
        node["value"] = val
        node["filled"] = True

    if reask:
        pending.discard(node["path"])
        state["pending_paths"] = sorted(pending)
    state["filled_paths"] = _collect_filled(tree)
    return state
