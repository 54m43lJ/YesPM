from __future__ import annotations

import os
from typing import Any, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from src.state.prd_state import PRDState
from src.tools.template_loader import build_skeleton, expand_instances, load_prompt
from src.tools.validate import repair_value, validate_value

CTRL_C_MARKER = "__YESPM_CTRL_C_END_CHAPTER__"

MAX_ROUNDS = int(os.getenv("DRAFT_MAX_ROUNDS", "50"))
MAX_EXTRACT_ATTEMPTS = 2


class TurnOut(BaseModel):
    reply: str
    mature: bool = False


class CountsOut(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict, description="repeat 节点路径 → 实例数量")


class ValuesOut(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict, description="字段路径 → 取值")


# ---------- 树 / 消息工具 ----------


def _content(m) -> str:
    c = getattr(m, "content", "")
    return c if isinstance(c, str) else str(c)


def _dialogue_lines(messages) -> list[str]:
    lines: list = []
    for m in messages or []:
        if isinstance(m, AIMessage):
            lines.append(f"助手：{_content(m)}")
        elif isinstance(m, HumanMessage):
            lines.append(f"用户：{_content(m)}")
    return lines


def _find_node(nodes: list, path: str) -> Optional[dict]:
    if not path:
        return None
    for vn in nodes:
        if vn.get("path") == path:
            return vn
        r = _find_node(vn.get("children") or [], path)
        if r:
            return r
    return None


def _top_node(tree: list, path: str) -> Optional[dict]:
    for vn in tree or []:
        if vn["path"] == path:
            return vn
    return None


def _collect_filled(nodes: list, out: list = None) -> list:
    if out is None:
        out = []
    for vn in nodes:
        nt = vn["node_type"]
        if nt == "field":
            if vn["filled"]:
                out.append(vn["path"])
        elif nt == "repeat":
            if vn["count_known"]:
                _collect_filled(vn["children"], out)
        else:
            _collect_filled(vn.get("children") or [], out)
    return out


def _context_lines(nodes: list, lines: list) -> None:
    for vn in nodes:
        nt = vn["node_type"]
        if nt == "field":
            if vn["filled"] and vn.get("value") not in (None, "", [], {}):
                v = vn["value"]
                if isinstance(v, list):
                    v = "; ".join(str(r) for r in v)
                lines.append(f"{vn['path']} {vn['title']}: {v}")
        elif nt == "repeat":
            if vn["count_known"]:
                _context_lines(vn["children"], lines)
        else:
            _context_lines(vn.get("children") or [], lines)


def _field_spec(vn: dict) -> str:
    line = f"- {vn['path']} {vn['title']}（tier={vn.get('tier')}，type={vn.get('field_type')}，必填={'是' if vn.get('required') else '否'}）"
    if vn.get("question"):
        line += f" 引导话术参考：{vn['question']}"
    if vn.get("description"):
        line += f" 说明：{vn['description']}"
    if vn.get("example"):
        line += f" 示例：{vn['example']}"
    if vn.get("field_type") == "enum":
        line += f" 可选：{vn.get('enum_values')}"
    if vn.get("field_type") == "table":
        line += f" 列：{vn.get('columns')}"
    return line


def _meta_lines(chapter: dict, out: list) -> None:
    """章节模板引导元数据（喂给会话 LLM 组织自然提问，不直接展示给用户）。"""

    def walk(vn: dict, with_path: bool):
        nt = vn["node_type"]
        if nt == "field":
            out.append(_field_spec(vn) if with_path else f"- {vn['title']}（字段）")
        elif nt == "repeat":
            if with_path:
                out.append(f"- {vn['path']} {vn['title']}（可重复「{vn.get('item_label') or '项'}」：数量与内容可在对话中自然确定）")
            else:
                out.append(f"- {vn['title']}（可重复「{vn.get('item_label') or '项'}」）")
            if vn.get("count_known"):
                for inst in vn.get("children") or []:
                    out.append(f"- {inst['path']} {inst['title']}（实例）")
                    walk(inst, True)
            else:
                for t in (vn.get("template_children") or vn.get("children") or []):
                    walk(t, False)
        else:
            if with_path:
                out.append(f"- {vn['path']} {vn['title']}（分组）")
            else:
                out.append(f"- {vn['title']}（分组）")
            for c in vn.get("children") or []:
                walk(c, with_path)

    nt = chapter["node_type"]
    if nt == "field":
        out.append(_field_spec(chapter))
        return
    if nt == "repeat":
        if chapter.get("count_known"):
            for inst in chapter.get("children") or []:
                out.append(f"- {inst['path']} {inst['title']}（实例）")
                walk(inst, True)
        else:
            out.append(f"- {chapter['path']} {chapter['title']}（可重复「{chapter.get('item_label') or '项'}」）")
            for t in (chapter.get("template_children") or chapter.get("children") or []):
                walk(t, False)
        return
    for c in chapter.get("children") or []:
        walk(c, True)


def _node_has_open(vn: dict) -> bool:
    """节点（或其子树）是否仍有需要对话讨论的内容（未填 P0/P1 字段或数量未知的 P0/P1 repeat）。"""
    nt = vn["node_type"]
    if nt == "field":
        return not vn.get("filled") and vn.get("tier") in ("P0", "P1")
    if nt == "repeat":
        if not vn.get("count_known"):
            return vn.get("tier") in ("P0", "P1")
        return _nodes_has_open(vn.get("children") or [])
    return _nodes_has_open(vn.get("children") or [])


def _nodes_has_open(nodes: list) -> bool:
    return any(_node_has_open(vn) for vn in nodes)


def _node_has_unfilled(vn: dict) -> bool:
    nt = vn["node_type"]
    if nt == "field":
        return not vn.get("filled")
    if nt == "repeat":
        if not vn.get("count_known"):
            return True
        return _nodes_has_unfilled(vn.get("children") or [])
    return _nodes_has_unfilled(vn.get("children") or [])


def _nodes_has_unfilled(nodes: list) -> bool:
    return any(_node_has_unfilled(vn) for vn in nodes)


def _next_open_chapter(tree: list, current: str) -> Optional[dict]:
    idx = 0
    if current:
        paths = [c["path"] for c in tree or []]
        if current in paths:
            idx = paths.index(current) + 1
    for chap in (tree or [])[idx:]:
        if _node_has_open(chap) or _node_has_unfilled(chap):
            return chap
    return None


def _node_extract_scope(vn: dict, fields: list, repeats: list) -> None:
    """单个节点的抽取范围：未填字段 + 数量未知的 repeat（不进入未知 repeat 的子树）。"""
    nt = vn["node_type"]
    if nt == "field":
        if not vn.get("filled"):
            fields.append(vn)
    elif nt == "repeat":
        if vn.get("count_known"):
            _walk_extract_scope(vn.get("children") or [], fields, repeats)
        else:
            repeats.append(vn)
    else:
        _walk_extract_scope(vn.get("children") or [], fields, repeats)


def _walk_extract_scope(nodes: list, fields: list, repeats: list) -> None:
    for vn in nodes:
        _node_extract_scope(vn, fields, repeats)


def _chapter_scope(chapter: dict):
    """章节抽取范围：(未填字段列表, 数量未知 repeat 列表)。"""
    fields: list = []
    repeats: list = []
    _node_extract_scope(chapter, fields, repeats)
    return fields, repeats


def _get_llm():
    try:
        from src.llm import get_llm

        return get_llm()
    except Exception:
        return None


# ---------- 会话回合 ----------


def _llm_turn(state: dict, tree: list, chapter: dict, llm, extra=()) -> tuple:
    """章节会话的一轮：LLM 生成自然对话回复 {reply, mature}。返回 (state, reply, mature)。"""
    msgs = state.get("messages") or []
    meta: list = []
    _meta_lines(chapter, meta)
    ctx: list = []
    _context_lines(tree, ctx)
    reply, mature = "", False
    try:
        from src.llm import llm_structured

        res = llm_structured(
            llm,
            TurnOut,
            [
                SystemMessage(content=load_prompt("draft.txt")),
                HumanMessage(content=(
                    f"产品简述：{state.get('initial_brief', '')}\n\n"
                    f"当前章节：{chapter['path']} {chapter['title']}（tier={chapter['tier']}）\n\n"
                    f"章节模板引导元数据（仅作组织对话参考，不得直接展示给用户）：\n"
                    + ("\n".join(meta) or "（无）") + "\n\n"
                    f"完整对话历史：\n" + ("\n".join(_dialogue_lines(msgs)) or "（尚无）") + "\n\n"
                    f"其它章节已收集信息：\n" + ("\n".join(ctx) or "（暂无）") + "\n\n"
                    + "\n".join(extra)
                )),
            ],
        )
        reply = str(getattr(res, "reply", "") or "")
        mature = bool(getattr(res, "mature", False))
    except Exception:
        reply = f"请介绍一下{chapter['title']}的相关信息。"
        mature = True
    if not reply.strip():
        reply = f"请介绍一下{chapter['title']}的相关信息。"
    if mature and state.get("session_rounds", 0) == 0:
        mature = False
    if state.get("draft_rounds", 0) >= MAX_ROUNDS:
        mature = True
    state["messages"] = msgs + [AIMessage(content=reply)]
    return state, reply, mature


def _session_turn(state: dict, tree: list, llm, extra=(), force_ask: bool = False) -> dict:
    """完成一次会话回合：LLM 自然回复 → 成熟则抽取，否则等待用户输入。

    force_ask=True 时强制追问（阶段内审核未达标后不得直接成熟抽取）。"""
    chapter = _top_node(tree, state.get("chapter_path") or "")
    if chapter is None:
        return _finish_draft(state, tree)
    state, reply, mature = _llm_turn(state, tree, chapter, llm, extra)
    if force_ask:
        mature = False
    if mature:
        return _chapter_extract(state, tree, chapter, llm)
    state["need_input"] = True
    state["input_kind"] = "chat"
    state["ui_payload"] = {"prompt": reply, "kind": "chat", "chapter": chapter["path"]}
    return state


# ---------- 章节成熟后一次性抽取 ----------


def _resolve_counts(state: dict, tree: list, chapter: dict, repeats: list, llm) -> dict:
    """逐层确定并展开 repeat 实例数量（含嵌套）。"""
    msgs = state.get("messages") or []
    while repeats:
        specs: list = []
        for rv in repeats:
            line = f"- {rv['path']} {rv['title']}（item_label={rv.get('item_label') or '项'}"
            if rv.get("min_items"):
                line += f"，最少 {rv['min_items']} 个"
            line += "）"
            subs: list = []
            _meta_lines(rv, subs)
            if subs:
                line += "\n  子结构：\n" + "\n".join("  " + s for s in subs)
            specs.append(line)
        counts: dict = {}
        try:
            from src.llm import llm_structured

            res = llm_structured(
                llm,
                CountsOut,
                [
                    SystemMessage(content=load_prompt("extract.txt")),
                    HumanMessage(content=(
                        f"产品简述：{state.get('initial_brief', '')}\n\n"
                        f"对话历史：\n" + ("\n".join(_dialogue_lines(msgs)) or "（无）") + "\n\n"
                        f"请确定以下 repeat 节点的实例数量（依据对话；不得少于各自最少要求；含必填子字段则至少 1 个；对话完全未涉及则 0）：\n"
                        + "\n".join(specs)
                    )),
                ],
            )
            counts = res.counts or {}
        except Exception:
            counts = {}
        for rv in repeats:
            n = counts.get(rv["path"])
            if n is None:
                n = rv.get("min_items") or (1 if rv.get("tier") in ("P0", "P1") else 0)
            try:
                n = max(int(n or 0), 0)
            except Exception:
                n = rv.get("min_items") or (1 if rv.get("tier") in ("P0", "P1") else 0)
            if rv.get("min_items") and n < rv["min_items"]:
                n = rv["min_items"]
            expand_instances(rv, n)
        _, repeats = _chapter_scope(chapter)
    return state


def _extract_values(state: dict, tree: list, chapter: dict, fields: list, llm) -> dict:
    msgs = state.get("messages") or []
    ctx: list = []
    _context_lines(tree, ctx)
    specs: list = []
    for v in fields:
        line = f"- {v['path']} {v['title']}（type={v.get('field_type')}，必填={'是' if v.get('required') else '否'}）"
        if v.get("description"):
            line += f" 说明：{v['description']}"
        if v.get("example"):
            line += f" 示例：{v['example']}"
        if v.get("field_type") == "enum":
            line += f" 可选：{v.get('enum_values')}"
        if v.get("field_type") == "table":
            line += f" 列：{v.get('columns')}"
        specs.append(line)
    try:
        from src.llm import llm_structured

        res = llm_structured(
            llm,
            ValuesOut,
            [
                SystemMessage(content=load_prompt("extract.txt")),
                HumanMessage(content=(
                    f"产品简述：{state.get('initial_brief', '')}\n\n"
                    f"本章节（{chapter['title']}）对话：\n"
                    + ("\n".join(_dialogue_lines(msgs)) or "（无）") + "\n\n"
                    f"其它章节已收集信息：\n" + ("\n".join(ctx) or "（暂无）") + "\n\n"
                    f"待抽取字段（路径必须严格一致）：\n" + "\n".join(specs) + "\n\n"
                    f"请输出 values（字段路径 → 取值），依据对话抽取，P2 字段基于上下文推断。"
                )),
            ],
        )
        return res.values or {}
    except Exception:
        return {}


def _fmt_value(v) -> str:
    if isinstance(v, list):
        return "\n".join("  - " + _fmt_value(r) for r in v)
    if isinstance(v, dict):
        return "；".join(f"{k}: {val}" for k, val in v.items())
    return str(v)


def _preview_text(state: dict, chapter: dict, items: list) -> str:
    lines = [f"【第 {chapter['path']} 章 · {chapter['title']}】取值抽取完成，请确认：", ""]
    for it in items:
        lines.append(f"[{it['path']} {it['title']}]")
        lines.append(_fmt_value(it["value"]))
        if it["errors"]:
            lines.append("（格式问题：" + "；".join(it["errors"]) + "，仍可接受修复结果）")
        lines.append("")
    lines.append("接受并写入取值树？(y/n)（回车输入 n 可拒绝，拒绝的字段会继续追问）")
    return "\n".join(lines)


def _is_accept(ans) -> bool:
    return str(ans or "").strip().lower() in ("y", "yes", "是", "接受", "确认", "ok", "accept")


def _complete_extract(state: dict, tree: list, chapter: dict) -> dict:
    """章节抽取收尾：无待抽取项或全部未抽取，完成章节/澄清会话。"""
    state["preview_items"] = []
    state["preview_pending"] = []
    if state.get("session_kind") == "reask":
        state["session_active"] = False
        state["session_kind"] = ""
        state["draft_done"] = True
    else:
        state["session_active"] = False
        state["session_kind"] = ""
        state["chapter_path"] = chapter["path"]
    state["filled_paths"] = _collect_filled(tree)
    return state


def _chapter_extract(state: dict, tree: list, chapter: dict, llm) -> dict:
    """章节成熟后一次性抽取：counts → 展开实例 → values → 校验/修复 → 整章批量预览。"""
    pending = sorted(set(state.get("pending_paths") or []))

    if pending:
        fields, repeats = [], []
        for p in pending:
            node = _find_node(tree, p)
            if node is None:
                continue
            if node["node_type"] == "field":
                fields.append(node)
            elif node["node_type"] == "repeat" and not node.get("count_known"):
                repeats.append(node)
        if repeats:
            _resolve_counts(state, tree, chapter, repeats, llm)
        fields = []
        for p in pending:
            node = _find_node(tree, p)
            if node is not None and node["node_type"] == "field" and not node.get("filled"):
                fields.append(node)
    else:
        fields, repeats = _chapter_scope(chapter)
        if repeats:
            _resolve_counts(state, tree, chapter, repeats, llm)
            fields, _ = _chapter_scope(chapter)

    if not fields:
        if pending:
            state["pending_paths"] = []
        return _complete_extract(state, tree, chapter)

    values = _extract_values(state, tree, chapter, fields, llm)
    items: list = []
    for path, val in (values or {}).items():
        node = _find_node(tree, path)
        if node is None:
            continue
        value, errors = repair_value(node, val, llm)
        items.append({"path": path, "title": node["title"], "value": value, "errors": errors})

    if not items:
        if pending:
            state["pending_paths"] = []
        return _complete_extract(state, tree, chapter)

    state["preview_items"] = items
    state["preview_pending"] = items
    state["need_input"] = True
    state["input_kind"] = "preview"
    state["ui_payload"] = {
        "prompt": _preview_text(state, chapter, items),
        "kind": "preview",
        "chapter": chapter["path"],
    }
    return state


# ---------- 用户回执处理 ----------


def _apply_preview(state: dict, tree: list, ans) -> dict:
    items = state.get("preview_items") or []
    state["preview_items"] = []
    state["preview_pending"] = []
    accept = ans != CTRL_C_MARKER and _is_accept(ans)
    pend: list = []
    attempts: dict = state.setdefault("pending_attempts", {})
    for it in items:
        node = _find_node(tree, it["path"])
        if node is None:
            continue
        if accept and not it["errors"] and not validate_value(node, it["value"]):
            node["value"] = it["value"]
            node["filled"] = True
            attempts.pop(it["path"], None)
        else:
            pend.append(it["path"])
    # 同一字段抽取多次仍无法落地的（格式非法或用户反复拒绝），放弃本轮回问，
    # 交由整树审核兜底（review 失败清单 + MAX_ITERATIONS 上限）。
    keep: list = []
    for p in pend:
        attempts[p] = attempts.get(p, 0) + 1
        if attempts[p] <= MAX_EXTRACT_ATTEMPTS:
            keep.append(p)
    if keep:
        state["pending_paths"] = sorted(set((state.get("pending_paths") or []) + keep))
    else:
        state["pending_paths"] = []
    state["filled_paths"] = _collect_filled(tree)
    return state


def _apply_answer(state: dict, tree: list, llm) -> dict:
    ans = state.get("user_answer")
    state["user_answer"] = None
    kind = state.get("input_kind") or "chat"
    state["input_kind"] = ""
    state["need_input"] = False
    state["ui_payload"] = {}

    if kind == "preview":
        return _apply_preview(state, tree, ans)

    if ans == CTRL_C_MARKER:
        chapter = _top_node(tree, state.get("chapter_path") or "")
        if chapter is None:
            return _finish_draft(state, tree)
        return _chapter_extract(state, tree, chapter, llm)

    state["messages"] = (state.get("messages") or []) + [HumanMessage(content=str(ans or ""))]
    state["draft_rounds"] = state.get("draft_rounds", 0) + 1
    state["session_rounds"] = state.get("session_rounds", 0) + 1
    state["await_audit"] = True
    return state


def _finish_draft(state: dict, tree: list) -> dict:
    state["draft_done"] = True
    state["reask_mode"] = False
    state["pending_paths"] = []
    state["pending_attempts"] = {}
    state["session_active"] = False
    state["session_kind"] = ""
    state["need_input"] = False
    state["input_kind"] = ""
    state["ui_payload"] = {}
    state["await_audit"] = False
    state["audit_result"] = {}
    state["audit_passed"] = False
    state["filled_paths"] = _collect_filled(tree)
    return state


# ---------- 主推进 ----------


def _advance(state: dict, tree: list, llm) -> dict:
    """推进工作流：待澄清字段（整树审核回访）→ 澄清会话；否则按章节推进对话/抽取。"""
    pending = sorted(set(state.get("pending_paths") or []))
    if pending:
        if state.get("session_kind") != "reask":
            state["session_active"] = True
            state["session_kind"] = "reask"
            state["chapter_path"] = pending[0].split(".")[0]
            state["draft_rounds"] = 0
            state["session_rounds"] = 0
        return _session_turn(state, tree, llm, (
            f"（整树审核后需澄清的字段：{'、'.join(pending)}。请围绕这些字段自然追问、逐项澄清，不要一次性罗列。）",
        ))

    while True:
        chap = _next_open_chapter(tree, state.get("chapter_path") or "")
        if chap is None:
            return _finish_draft(state, tree)
        if _node_has_open(chap):
            state["session_active"] = True
            state["session_kind"] = "chapter"
            state["chapter_path"] = chap["path"]
            state["draft_rounds"] = 0
            state["session_rounds"] = 0
            return _session_turn(state, tree, llm, ())
        # 仅剩 P2 推断的章节：不开启对话，直接抽取
        state = _chapter_extract(state, tree, chap, llm)
        if state.get("need_input"):
            return state
        state["chapter_path"] = chap["path"]
        state["filled_paths"] = _collect_filled(tree)


def draft_prd(state: PRDState) -> PRDState:
    state = dict(state)
    tree = state.get("prd_draft")
    if not tree:
        tree = build_skeleton(state["template"])
        state["prd_draft"] = tree
        state["filled_paths"] = []
        state["messages"] = []
        state["chapter_path"] = ""
        state["draft_rounds"] = 0
        state["session_rounds"] = 0
        state["session_active"] = False
        state["session_kind"] = ""
        state["need_input"] = False
        state["input_kind"] = ""
        state["ui_payload"] = {}
        state["user_answer"] = None
        state["preview_items"] = []
        state["preview_pending"] = []
        state["pending_attempts"] = {}
        state["draft_done"] = False
        state["reask_mode"] = False
        state["await_audit"] = False
        state["audit_result"] = {}
        state["audit_passed"] = False

    llm = _get_llm()

    if state.get("audit_result"):
        ar = state.get("audit_result") or {}
        state["audit_result"] = {}
        state["audit_passed"] = False
        state["await_audit"] = False
        if not ar.get("passed"):
            extra = (f"（阶段内审核反馈，仅作自然追问参考，不要复述原有提问）{ar.get('feedback') or '请补充更多细节。'}",)
            return _session_turn(state, tree, llm, extra, force_ask=True)
        return _session_turn(state, tree, llm, ())

    if state.get("user_answer") is not None:
        state = _apply_answer(state, tree, llm)
        if state.get("need_input") or state.get("await_audit") or state.get("draft_done"):
            return state
        return _advance(state, tree, llm)

    return _advance(state, tree, llm)


# ---------- ui 节点（interrupt 与 LLM 调用分离，避免节点重执行时重复 LLM 调用） ----------


def ui_node(state: PRDState) -> PRDState:
    state = dict(state)
    payload = state.get("ui_payload") or {"prompt": "请回答：", "kind": "chat"}
    ans = interrupt(payload)
    state["user_answer"] = ans if ans is not None else ""
    state["need_input"] = False
    return state
