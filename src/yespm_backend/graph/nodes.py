"""主图节点：访谈阶段单元循环（interview ↔ unit_review → transcribe）。

与 evolution-1 的关键差异（命令结构化原则）：
- 节点不解析任何命令文本（`/skip` `/finish` `/undo` 不再出现在图里）；
- skip / finish / undo 由引擎写入结构化状态标志（skip_requested /
  finish_requested / undo_applied），图路由读取标志决定走向；
- LLM 流式输出经 `emit` 回调发布领域事件（不直接 print），由引擎 owning 传输。

所有节点为工厂函数：make_xxx_node(llm, cfg, emit) → node。
"""
from __future__ import annotations

import copy
import json
import re
from typing import Callable
from uuid import uuid4

from langgraph.types import interrupt

from ..prompts.loader import load_prompt
from ..tree.value_tree import (
    apply_field_unit_value,
    apply_subtree,
    find_value_node,
    replace_node,
    template_node_at,
)
from ..tree.units import build_gap_units, build_units
from .common import (
    conversation_text,
    json_block,
    unit_description_text,
    unit_existing_text,
)

Emit = Callable[[str, dict], None]


# ---------------------------------------------------------------- 单元编排

def start_node(state: dict) -> dict:
    """初始化访谈单元队列：正常模式或缺口驱动模式（回灌）。"""
    units_queue = state.get("units_queue") or []
    current = state.get("current_unit")
    if units_queue or current:
        return {}  # 恢复续聊 / 进行中：沿用既有队列
    template = state.get("template") or []
    value_tree = state.get("prd_draft") or []
    gaps = state.get("gap_list") or []
    if gaps:
        units = build_gap_units(template, value_tree, [g.get("path") for g in gaps if g.get("path")])
    else:
        units = build_units(template, value_tree)
    return {"units_queue": units, "gap_list": []}


def pick_next_unit_node(state: dict) -> dict:
    """从队列取出下一个单元，重置单元内状态。"""
    if state.get("current_unit"):
        return {}
    queue = list(state.get("units_queue") or [])
    if not queue:
        return {}
    unit = queue.pop(0)
    return {
        "units_queue": queue,
        "current_unit": unit,
        "unit_conversation": [],
        "unit_turns": 0,
        "interview_complete": False,
        "interview_gaps": [],
        "pending_question": "",
    }


def interview_done_node(state: dict) -> dict:
    return {
        "phase": "review",
        "current_unit": None,
        "units_queue": [],
        "unit_conversation": [],
        "finish_requested": False,
    }


# ---------------------------------------------------------------- interview

def make_interview_node(llm, cfg, emit: Emit):
    """访谈提问节点：LLM 生成提问（一次），流式 emit，结果暂存状态交由 await_input 中断。"""
    system_tpl = load_prompt("interview")

    def node(state: dict) -> dict:
        unit = state.get("current_unit") or {}
        template = state.get("template") or []
        value_tree = state.get("prd_draft") or []
        conv = state.get("unit_conversation") or []
        gaps = state.get("interview_gaps") or []
        gap_text = (
            "\n".join(
                f"- {g.get('path')} [{g.get('dimension')}] {g.get('reason')}" for g in gaps
            )
            or "(无)"
        )

        user = (
            f"单元：{unit.get('label', '')}\n\n"
            f"单元描述：\n{unit_description_text(template, unit)}\n\n"
            f"现有取值：\n{unit_existing_text(value_tree, unit)}\n\n"
            f"对话历史：\n{conversation_text(conv)}"
        )
        system = system_tpl.replace("{GAP_TARGETS}", gap_text)

        msg_id = f"m-{uuid4().hex[:8]}"
        text = ""
        pending = ""  # 末尾 holdback：可能属于 [COVERED]/[CONTINUE] 标记的部分不流式发出

        def _flush(delta: str) -> None:
            if delta:
                emit(
                    "session/message",
                    {"message_id": msg_id, "status_code": 2031, "delta": delta},
                )

        def on_chunk(delta: str) -> None:
            nonlocal text, pending
            pending += delta
            text += delta
            # 计算可安全 flush 的前缀长度（末尾若为标记前缀则 holdback）
            safe = len(pending)
            for marker in ("[COVERED]", "[CONTINUE]"):
                for k in range(1, len(marker) + 1):
                    if pending.endswith(marker[:k]):
                        safe = min(safe, len(pending) - k)
            if safe > 0:
                _flush(pending[:safe])
                pending = pending[safe:]

        try:
            llm.stream(system, user, on_chunk=on_chunk)
        except Exception as e:  # LLM 调用失败：emit 错误日志，提问降级为兜底
            emit(
                "log",
                {"status_code": 4803, "message": f"interview_agent LLM 调用失败：{e}"},
            )
            text = text or "（LLM 调用失败，请重试或补充本单元信息）"
        # pending 中残留的是标记前缀，丢弃（不流式发出）

        question = text.strip()
        complete = "[COVERED]" in question
        question = re.sub(r"\[(?:COVERED|CONTINUE)\]\s*$", "", question).strip()
        if not question:
            question = "请继续补充本单元信息。"
        emit(
            "session/message",
            {"message_id": msg_id, "status_code": 2032, "text": question},
        )
        return {
            "pending_question": question,
            "interview_complete": complete,
            "undo_applied": False,  # 清除 undo 标志（进入正常提问）
        }

    return node


def make_await_input_node():
    """中断等待节点：interrupt 等待用户输入；恢复后返回输入值（或引擎哨兵）。

    与 LLM 调用分离，避免 langgraph resume 语义下 LLM 重复调用。
    中断载荷供引擎推断等待上下文（但协议要求上下文一律从 session/status 推断，
    故此处的 question/unit 仅供引擎内部使用，不直接对外）。
    """

    def node(state: dict) -> dict:
        unit = state.get("current_unit") or {}
        question = state.get("pending_question") or "请继续补充本单元信息。"
        payload = {
            "kind": "interview",
            "unit": unit.get("label", ""),
            "question": question,
        }
        reply = interrupt(payload)
        return {
            "last_user_input": reply if isinstance(reply, str) else "",
            "pending_question": "",
        }

    return node


def after_input_node(state: dict) -> dict:
    """用户输入后处理：命令路径不追加对话；普通输入追加并计数。

    命令标志（skip/finish/undo）由引擎写入；此处仅判断是否命令路径，
    真正的路由与标志清除见 route_after_input 与各目标节点。
    """
    if (
        state.get("skip_requested")
        or state.get("finish_requested")
        or state.get("undo_applied")
    ):
        return {}  # 命令路径，不追加对话
    text = state.get("last_user_input") or ""
    conv = list(state.get("unit_conversation") or [])
    conv.append({"role": "user", "content": text})
    return {
        "unit_conversation": conv,
        "unit_turns": (state.get("unit_turns") or 0) + 1,
    }


def route_after_input(state: dict, cfg) -> str:
    """用户输入后的路由：命令 / 评审触发 / 继续访谈。"""
    if state.get("finish_requested"):
        return "interview_done"
    if state.get("skip_requested"):
        return "transcribe"
    if state.get("undo_applied"):
        return "interview"
    if state.get("interview_complete") and not (state.get("unit_conversation") or []):
        return "transcribe"
    if state.get("interview_complete") or (state.get("unit_turns") or 0) >= cfg.unit_max_turns:
        return "unit_review"
    return "interview"


# ---------------------------------------------------------------- unit_review

def make_unit_review_node(llm):
    """单元成熟度评审：对话 + 取值树 + 单元描述 → 成熟/缺口清单。"""
    system_tpl = load_prompt("unit_review")

    def node(state: dict) -> dict:
        unit = state.get("current_unit") or {}
        conv = state.get("unit_conversation") or []
        if not conv:
            return {"interview_gaps": []}  # 无对话不评审（直接转录）
        template = state.get("template") or []
        value_tree = state.get("prd_draft") or []
        covered = "\n".join(unit.get("covered") or []) or "(无)"

        user = (
            f"单元：{unit.get('label', '')}\n\n"
            f"单元描述：\n{unit_description_text(template, unit)}\n\n"
            f"现有取值：\n{unit_existing_text(value_tree, unit)}\n\n"
            f"对话：\n{conversation_text(conv)}"
        )
        system = system_tpl.replace("{COVERED_PATHS}", covered)
        try:
            result = llm.chat_json(system, user) or {}
        except Exception:
            result = {}
        mature = bool(result.get("mature"))
        gaps: list[dict] = []
        if not mature:
            covered_set = set(unit.get("covered") or [])
            for g in result.get("gaps") or []:
                path = g.get("path") if isinstance(g, dict) else None
                if path in covered_set:
                    gaps.append(
                        {
                            "path": path,
                            "dimension": g.get("dimension") or "完整性",
                            "reason": g.get("reason") or "",
                        }
                    )
        return {"interview_gaps": gaps}

    return node


def route_review(state: dict) -> str:
    """评审路由：有缺口 → 回到访谈针对性追问；成熟/无对话 → 转录。"""
    if state.get("interview_gaps"):
        return "interview"
    return "transcribe"


# ---------------------------------------------------------------- transcribe

def _with_template_hints(template: list[dict], node: dict) -> dict:
    """在 repeat 节点上附带 children_template（实例模板），供转录参考。"""
    n = copy.deepcopy(node)
    if n["kind"] == "repeat":
        tpl = template_node_at(template, n["path"]) if n.get("path") else None
        if tpl:
            n["children_template"] = tpl.get("children") or []
        for inst in n.get("instances") or []:
            for c in inst.get("children") or []:
                _with_template_hints(template, c)
    elif n["kind"] == "group":
        for c in n.get("children") or []:
            _with_template_hints(template, c)
    return n


def make_transcribe_node(llm):
    """转录：对话 → 取值树子树（修订式转录）。"""
    system_tpl = load_prompt("transcribe")

    def node(state: dict) -> dict:
        unit = state.get("current_unit") or {}
        template = state.get("template") or []
        value_tree = copy.deepcopy(state.get("prd_draft") or [])
        conv = state.get("unit_conversation") or []
        unit_desc = unit_description_text(template, unit)

        if unit.get("kind") == "chapter":
            root = find_value_node(value_tree, unit.get("template_path") or "")
            tree_block = (
                json_block("TREE_JSON", _with_template_hints(template, root))
                if root
                else "TREE_JSON\n```json\n{}\n```"
            )
            user = (
                f"单元：{unit.get('label', '')}\n\n"
                f"单元描述：\n{unit_desc}\n\n"
                f"现有取值树（在其基础上修订）：\n{tree_block}\n\n"
                f"对话：\n{conversation_text(conv)}"
            )
            system = system_tpl.replace("{UNIT_DESCRIPTION}", unit_desc).replace("{TREE_BLOCK}", tree_block)
            try:
                raw = llm.chat_json(system, user)
            except Exception:
                raw = {}
            subtree = raw.get("subtree") if isinstance(raw, dict) else None
            if isinstance(subtree, list):  # 兼容直接给出子节点列表
                subtree = {"children": subtree}
            if root is not None and isinstance(subtree, dict):
                new_root = apply_subtree(template, root, subtree)
                replace_node(value_tree, root["path"], new_root)
        else:
            covered_nodes = [find_value_node(value_tree, p) for p in unit.get("covered") or []]
            tree_block = json_block("TREE_JSON", [n for n in covered_nodes if n])
            user = (
                f"单元：{unit.get('label', '')}\n\n"
                f"单元描述：\n{unit_desc}\n\n"
                f"现有取值：\n{tree_block}\n\n"
                f"对话：\n{conversation_text(conv)}"
            )
            system = system_tpl.replace("{UNIT_DESCRIPTION}", unit_desc).replace("{TREE_BLOCK}", tree_block)
            try:
                raw = llm.chat_json(system, user)
            except Exception:
                raw = {}
            fields = raw.get("fields") if isinstance(raw, dict) else None
            covered_set = set(unit.get("covered") or [])
            if isinstance(fields, dict):
                for path, value in fields.items():
                    if path in covered_set:
                        apply_field_unit_value(value_tree, path, value)

        stack = list(state.get("undo_stack") or [])
        stack.append(
            {
                "kind": "transcribe",
                "unit": copy.deepcopy(unit),
                "snapshot_tree": copy.deepcopy(state.get("prd_draft") or []),
                "snapshot_units_done": list(state.get("units_done") or []),
            }
        )
        units_done = list(state.get("units_done") or []) + [unit.get("id")]
        return {
            "prd_draft": value_tree,
            "unit_conversation": [],
            "unit_turns": 0,
            "current_unit": None,
            "units_done": units_done,
            "undo_stack": stack,
            "skip_requested": False,  # 清除 skip 标志
            "interview_gaps": [],
            "last_user_input": None,
        }

    return node
