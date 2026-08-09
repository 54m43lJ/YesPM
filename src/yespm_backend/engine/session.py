"""无头会话引擎（Session）：持有 LangGraph 图实例、API 方法入口、中断管理、暂存队列。

- 单一状态源：状态本体在 LangGraph SqliteSaver checkpoint；本类只读/写图状态。
- 阶段编排：interview（图 ①）→ document_review（回灌循环）→ polish（提案确认）→ finished。
- 命令结构化：skip/finish/undo 以状态标志位表达，图路由读取，引擎不解析命令文本。
- 事件发布：所有状态变化经 `emit` 发布领域事件（传输适配层序列化为通知）。
- 单会话串行：方法同步执行至下一个等待点；busy 期间的输入暂存（pending_queue）。
"""
from __future__ import annotations

import copy
import json
from collections import deque
from datetime import datetime
from typing import Any, Callable

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from ..config import Config
from ..polish.pipeline import (
    apply_block_proposal,
    finalize_prd,
    p2_targets,
    prepare_polish,
    proposal_description,
    run_document_review,
    run_p2_infer,
)
from ..state import PRDState, default_state
from ..template.loader import load_default_template, load_template
from ..tree.value_tree import find_value_node, instantiate_value_tree
from . import events as E
from .errors import ProtocolError
from .store import SessionStore

Emit = Callable[[str, dict], None]
_SENTINEL = ""  # 命令路径恢复 await_input 用的哨兵（纯数据，非命令文本）


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _tree_signature(tree: Any) -> str:
    return json.dumps(tree, ensure_ascii=False, default=str, sort_keys=True)


class Session:
    """单个会话的引擎实例。一个 Session 同一时刻只跑一个图执行。"""

    def __init__(
        self,
        session_id: str,
        cfg: Config,
        graph: CompiledStateGraph,
        llm,
        emit: Emit,
        store: SessionStore,
        created_at: str | None = None,
    ):
        self.session_id = session_id
        self.cfg = cfg
        self.graph = graph
        self.llm = llm
        self._emit_external = emit
        self.store = store
        self.config = {"configurable": {"thread_id": session_id}}
        self.pending_queue: deque[str] = deque()
        self._revision = 0
        self._last_tree_sig: str = ""
        self.created_at = created_at or _now()
        self._ended: dict | None = None  # 当前激活的结束标记（不入 checkpoint，避免破坏挂起中断）

    # ---------------------------------------------------------------- 事件

    def emit(self, event: str, params: dict) -> None:
        """发布领域事件（补全 session_id）。"""
        payload = {"session_id": self.session_id}
        payload.update(params)
        self._emit_external(event, payload)

    def _next_revision(self) -> int:
        self._revision += 1
        return self._revision

    def _publish_status(self, fields: dict) -> None:
        """发布 session/status（仅带变化/相关字段）。"""
        params = {
            "session_id": self.session_id,
            "status_code": 2001,
            "fields": list(fields.keys()),
            "revision": self._next_revision(),
            "updated_at": _now(),
            "created_at": self.created_at,
        }
        params.update(fields)
        self.emit(E.SESSION_STATUS, params)

    def _publish_full_status(self) -> None:
        """创建/恢复后首条：全字段快照。"""
        state = self._state()
        self._publish_status(self._snapshot_fields(state, full=True))

    def _emit_message(self, text: str, status_code: int = 2032) -> None:
        """发布一条 session/message（非流式，一次性 complete）。"""
        self._message_counter = getattr(self, "_message_counter", 0) + 1
        msg_id = f"m-{self._message_counter}"
        self.emit(
            E.SESSION_MESSAGE,
            {"message_id": msg_id, "status_code": status_code, "text": text},
        )

    # ---------------------------------------------------------------- 状态读写

    def _state(self) -> dict:
        snap = self.graph.get_state(self.config)
        return dict(snap.values) if snap and snap.values else {}

    def _update(self, partial: dict) -> None:
        self.graph.update_state(self.config, partial)
        # tree 变化检测
        tree = partial.get("prd_draft")
        if tree is not None:
            self._maybe_emit_tree_changed(tree)

    def _maybe_emit_tree_changed(self, tree: list[dict] | None) -> None:
        sig = _tree_signature(tree)
        if sig and sig != self._last_tree_sig:
            self._last_tree_sig = sig
            self.emit(E.TREE_CHANGED, {"session_id": self.session_id})

    def _collect_interrupts(self) -> list:
        snap = self.graph.get_state(self.config)
        out = []
        for t in (snap.tasks if snap else []):
            out.extend(t.interrupts or [])
        return out

    # ---------------------------------------------------------------- 快照组装

    def _snapshot_fields(self, state: dict, full: bool = False) -> dict:
        """组装 session/status 字段（不含大载荷）。"""
        phase = state.get("phase", "interview")
        unit = state.get("current_unit") or {}
        fields: dict[str, Any] = {
            "stage": phase,
            "node": self._current_node_name(state),
            "status": self._derive_status(state),
            "current_unit": (
                {"path": unit.get("template_path"), "title": unit.get("label", "")}
                if unit else None
            ),
            "units_done": list(state.get("units_done") or []),
            "waiting": self._derive_waiting(state),
            "document_review": self._derive_doc_review(state),
            "iterations": {"document_review": state.get("doc_review_iterations") or 0},
            "template_title": self._template_title(state),
            "revision": self._revision,
            "created_at": self.created_at,
            "updated_at": _now(),
        }
        if self._ended:
            fields["ended"] = self._ended
        if not full:
            return fields
        return fields

    def _current_node_name(self, state: dict) -> str | None:
        phase = state.get("phase", "interview")
        if phase == "interview":
            return "interview_agent"
        if phase == "review":
            return "document_review_agent"
        if phase == "polish":
            return "polish_agent"
        return None

    def _derive_status(self, state: dict) -> str:
        if self._ended:
            return "idle"
        phase = state.get("phase", "interview")
        if phase == "finished":
            return "idle"
        if phase == "polish" and (state.get("pending_proposals") or []):
            return "waiting"
        # interview at interrupt → waiting
        if phase == "interview" and self._collect_interrupts():
            return "waiting"
        return "running"

    def _derive_waiting(self, state: dict) -> dict:
        phase = state.get("phase", "interview")
        if phase == "interview" and self._collect_interrupts():
            return {"kind": "interview"}
        if phase == "polish":
            pending = state.get("pending_proposals") or []
            if pending:
                return {"kind": "proposal", "proposal_ids": [p["id"] for p in pending]}
        return {"kind": "none"}

    def _derive_doc_review(self, state: dict) -> dict | None:
        gaps = state.get("gap_list") or []
        if gaps or state.get("doc_review_iterations"):
            return {"passed": not gaps, "gap_count": len(gaps)}
        return None

    def _template_title(self, state: dict) -> str:
        tpl = state.get("template") or []
        return tpl[0]["title"] if tpl else "PRD"

    # ---------------------------------------------------------------- 驱动

    def _stream(self, input_value: Any) -> None:
        """驱动图执行；interview 节点经 emit 直接发布流式消息。"""
        try:
            prev_sig = _tree_signature(self._state().get("prd_draft"))
            for chunk in self.graph.stream(input_value, self.config, stream_mode="values"):
                tree = chunk.get("prd_draft")
                if tree is not None:
                    sig = _tree_signature(tree)
                    if sig != prev_sig:
                        prev_sig = sig
                        self._last_tree_sig = sig
                        self.emit(E.TREE_CHANGED, {"session_id": self.session_id})
        except Exception as e:  # 引擎内部故障
            self.emit(E.LOG, {"status_code": 5901, "message": f"引擎执行异常：{e}"})
            raise ProtocolError(5901, f"引擎执行异常：{e}")

    def _resume_and_pump(self, value: Any) -> None:
        """恢复 await_input 中断并继续推进到下一个等待点。"""
        self._publish_status({"status": "running", "waiting": {"kind": "none"}})
        self._stream(Command(resume=value))
        self._pump()

    def _pump(self) -> None:
        """推进会话直到下一个等待点（中断 / 提案确认）或终态。"""
        while True:
            state = self._state()
            phase = state.get("phase", "interview")
            if phase == "interview":
                if self._collect_interrupts():
                    self._handle_interview_await()
                    return
                # 无中断：推进图（首次启动或回灌重入）
                self._publish_status({
                    "stage": "interview",
                    "node": "interview_agent",
                    "status": "running",
                    "waiting": {"kind": "none"},
                })
                self._stream({})
                continue
            if phase == "review":
                self._run_review()
                continue
            if phase == "polish":
                self._run_polish()
                return
            # finished
            self._handle_finished()
            return

    # ---------------------------------------------------------------- 阶段处理

    def _handle_interview_await(self) -> None:
        state = self._state()
        unit = state.get("current_unit") or {}
        self._publish_status({
            "stage": "interview",
            "node": "interview_agent",
            "status": "waiting",
            "current_unit": {
                "path": unit.get("template_path"),
                "title": unit.get("label", ""),
            },
            "waiting": {"kind": "interview"},
        })
        self.emit(E.SESSION_AWAIT_INPUT, {"session_id": self.session_id, "status_code": 2002})

    def _run_review(self) -> None:
        state = self._state()
        template = state.get("template") or []
        value_tree = copy.deepcopy(state.get("prd_draft") or [])
        brief = state.get("brief") or ""
        iters = state.get("doc_review_iterations") or 0
        gap_paths = [g.get("path") for g in (state.get("gap_list") or []) if g.get("path")]

        self._publish_status({
            "stage": "document_review",
            "node": "document_review_agent",
            "status": "running",
            "current_unit": None,
            "waiting": {"kind": "none"},
        })

        # P2 兜底（无访谈单元字段；含 gap 中的 P2 字段）
        field_paths, repeat_paths = p2_targets(template, value_tree, gap_paths)
        if field_paths or repeat_paths:
            run_p2_infer(self.llm, template, value_tree, brief, field_paths, repeat_paths)
            self.graph.update_state(self.config, {"prd_draft": value_tree})
            self._maybe_emit_tree_changed(value_tree)

        passed, gaps = run_document_review(self.llm, template, value_tree, brief)
        self.graph.update_state(self.config, {"gap_list": gaps})
        self.emit(E.GAPS_CHANGED, {"session_id": self.session_id})
        conclusion = self._review_conclusion(passed, gaps)
        self._emit_message(conclusion)

        if passed:
            self.graph.update_state(self.config, {
                "phase": "polish", "gap_list": [], "polish_blocks": [],
            })
            self._publish_status({
                "stage": "polish",
                "document_review": {"passed": True, "gap_count": 0},
                "current_unit": None,
            })
            return
        if iters >= self.cfg.doc_review_max_iters:
            self.graph.update_state(self.config, {
                "phase": "polish",
                "gap_list": gaps,
                "unresolved_gaps": gaps,
            })
            self._publish_status({
                "stage": "polish",
                "document_review": {"passed": False, "gap_count": len(gaps)},
                "current_unit": None,
            })
            self.emit(E.LOG, {"status_code": 3501, "message": "全文档审核超限，附未决清单进入渲染"})
            return

        iters += 1
        self.graph.update_state(self.config, {
            "gap_list": gaps,
            "doc_review_iterations": iters,
            "phase": "interview",
            "units_queue": [],
            "current_unit": None,
            "unit_conversation": [],
        })
        self._publish_status({
            "stage": "interview",
            "document_review": {"passed": False, "gap_count": len(gaps)},
            "iterations": {"document_review": iters},
        })

    def _review_conclusion(self, passed: bool, gaps: list[dict]) -> str:
        if passed:
            return "全文档审核通过，进入润色阶段。"
        lines = [f"全文档审核未通过，发现 {len(gaps)} 个缺口，回到访谈补充："]
        for g in gaps:
            lines.append(f"- {g.get('path')} [{g.get('dimension')}] {g.get('reason')}")
        return "\n".join(lines)

    def _run_polish(self) -> None:
        state = self._state()
        pending = state.get("pending_proposals") or []
        if pending:
            # 恢复时仍在等待提案确认 → 重新发布等待
            self._publish_status({
                "stage": "polish",
                "node": "polish_agent",
                "status": "waiting",
                "waiting": {"kind": "proposal", "proposal_ids": [p["id"] for p in pending]},
            })
            self.emit(E.SESSION_AWAIT_INPUT, {"session_id": self.session_id, "status_code": 2002})
            return

        template = state.get("template") or []
        value_tree = copy.deepcopy(state.get("prd_draft") or [])
        self._publish_status({
            "stage": "polish",
            "node": "polish_agent",
            "status": "running",
            "waiting": {"kind": "none"},
        })
        result = prepare_polish(self.llm, self.cfg, template, value_tree)
        blocks = result["blocks"]
        conversion_log = list(result["low_applied"]) + list(result["discarded"])
        high_pending = result["high_pending"]
        self.graph.update_state(self.config, {
            "polish_blocks": blocks,
            "conversion_log": conversion_log,
            "pending_proposals": high_pending,
        })
        if conversion_log:
            self.emit(E.CONVERSIONS_CHANGED, {"session_id": self.session_id})

        if not high_pending:
            self._finalize()
            return

        # 高风险提案：逐条描述 + 等待确认
        total = len(high_pending)
        for i, p in enumerate(high_pending, start=1):
            self._emit_message(proposal_description(p, i, total))
        self._publish_status({
            "stage": "polish",
            "node": "polish_agent",
            "status": "waiting",
            "waiting": {"kind": "proposal", "proposal_ids": [p["id"] for p in high_pending]},
        })
        self.emit(E.SESSION_AWAIT_INPUT, {"session_id": self.session_id, "status_code": 2002})

    def _finalize(self) -> None:
        state = self._state()
        blocks = state.get("polish_blocks") or []
        conversion_log = list(state.get("conversion_log") or [])
        unresolved = state.get("unresolved_gaps") or []
        final_prd = finalize_prd(blocks, conversion_log, unresolved)
        self.graph.update_state(self.config, {
            "phase": "finished",
            "final_prd": final_prd,
            "conversion_log": conversion_log,
            "pending_proposals": [],
        })
        self.emit(E.PRD_CHANGED, {"session_id": self.session_id})
        self._publish_status({
            "stage": "finished",
            "node": None,
            "status": "idle",
            "waiting": {"kind": "none"},
            "current_unit": None,
        })

    def _handle_finished(self) -> None:
        self._publish_status({
            "stage": "finished",
            "status": "idle",
            "waiting": {"kind": "none"},
        })

    # ---------------------------------------------------------------- 公共 API（dispatch 调用）

    def start(self) -> None:
        """首次推进（session/create 后）。"""
        self._publish_full_status()
        self._pump()

    def rehydrate(self) -> None:
        """恢复会话（session/resume）：发布全字段快照，若有挂起中断补发 await_input。

        resume 即「重开会话」：上一激活的 ended 标记在此清除，新激活可继续操作。
        不调用 update_state（避免破坏挂起中断）。
        """
        self._ended = None
        self._publish_full_status()
        state = self._state()
        self._last_tree_sig = _tree_signature(state.get("prd_draft"))
        phase = state.get("phase", "interview")
        if phase == "interview" and self._collect_interrupts():
            self._handle_interview_await()
        elif phase == "polish" and (state.get("pending_proposals") or []):
            pending = state.get("pending_proposals") or []
            self._publish_status({
                "stage": "polish",
                "status": "waiting",
                "waiting": {"kind": "proposal", "proposal_ids": [p["id"] for p in pending]},
            })
            self.emit(E.SESSION_AWAIT_INPUT, {"session_id": self.session_id, "status_code": 2002})

    def send_input(self, text: str) -> tuple[bool, bool]:
        """input/send：访谈回复原样转发给当前活跃节点。"""
        state = self._state()
        phase = state.get("phase", "interview")
        if self._ended:
            raise ProtocolError(4102, "当前状态不接受输入", {"stage": "ended"})
        if phase == "finished":
            raise ProtocolError(4102, "当前状态不接受输入", {"stage": "finished"})
        if phase != "interview" or not self._collect_interrupts():
            # 非访谈等待态：在同步模型下视为忙/不可接受
            raise ProtocolError(4102, "当前状态不接受输入", {"stage": phase})
        self._resume_and_pump(text)
        return True, False

    def skip(self) -> bool:
        """command/skip：跳过当前访谈单元（强制转录，进入下一单元）。"""
        state = self._state()
        if state.get("phase") != "interview" or not self._collect_interrupts():
            raise ProtocolError(4103, "操作当前不可用")
        self.graph.update_state(self.config, {"skip_requested": True})
        self._resume_and_pump(_SENTINEL)
        return True

    def finish(self) -> bool:
        """command/finish：结束访谈阶段，进入全文档审核。"""
        state = self._state()
        if state.get("phase") != "interview" or not self._collect_interrupts():
            raise ProtocolError(4103, "操作当前不可用")
        self.graph.update_state(self.config, {"finish_requested": True})
        self._resume_and_pump(_SENTINEL)
        return True

    def undo(self) -> bool:
        """command/undo：回退最近一次转录。"""
        state = self._state()
        if state.get("phase") != "interview" or not self._collect_interrupts():
            raise ProtocolError(4103, "操作当前不可用")
        stack = list(state.get("undo_stack") or [])
        while stack:
            entry = stack.pop()
            if entry.get("kind") == "transcribe":
                self.graph.update_state(self.config, {
                    "prd_draft": copy.deepcopy(entry.get("snapshot_tree") or []),
                    "units_done": list(entry.get("snapshot_units_done") or []),
                    "current_unit": copy.deepcopy(entry.get("unit")),
                    "unit_conversation": [],
                    "unit_turns": 0,
                    "interview_complete": False,
                    "interview_gaps": [],
                    "undo_stack": stack,
                    "undo_applied": True,
                })
                self._last_tree_sig = _tree_signature(entry.get("snapshot_tree") or [])
                self.emit(E.TREE_CHANGED, {"session_id": self.session_id})
                self._resume_and_pump(_SENTINEL)
                return True
        raise ProtocolError(4103, "操作当前不可用")

    def respond_proposal(self, proposal_ids: list[str], action: str) -> bool:
        """proposal/respond：确认/拒绝高风险润色提案。"""
        if action not in ("apply", "reject"):
            raise ProtocolError(4604, "参数无效：action 必须为 apply/reject")
        state = self._state()
        pending = state.get("pending_proposals") or []
        if not pending:
            raise ProtocolError(4103, "操作当前不可用")
        pending_by_id = {p["id"]: p for p in pending}
        for pid in proposal_ids:
            if pid not in pending_by_id:
                raise ProtocolError(4404, f"提案不存在或状态已变：{pid}")
        blocks = list(state.get("polish_blocks") or [])
        conversion_log = list(state.get("conversion_log") or [])
        for pid in proposal_ids:
            p = pending_by_id[pid]
            if action == "apply":
                apply_block_proposal(blocks, p["position"], p["after"])
                conversion_log.append({**p, "status": "applied"})
            else:
                conversion_log.append({**p, "status": "rejected", "evaluation": "用户拒绝"})
        remaining = [p for p in pending if p["id"] not in proposal_ids]
        self.graph.update_state(self.config, {
            "polish_blocks": blocks,
            "conversion_log": conversion_log,
            "pending_proposals": remaining,
        })
        self.emit(E.CONVERSIONS_CHANGED, {"session_id": self.session_id})
        if remaining:
            self._publish_status({
                "stage": "polish",
                "status": "waiting",
                "waiting": {"kind": "proposal", "proposal_ids": [p["id"] for p in remaining]},
            })
            self.emit(E.SESSION_AWAIT_INPUT, {"session_id": self.session_id, "status_code": 2002})
            return True
        self._finalize()
        return True

    def quit(self, reason: str = "quit") -> None:
        """session/quit：保存 checkpoint 后结束会话。

        ended 标记为当前激活的内存状态（不入 checkpoint），避免 update_state 破坏
        挂起的 interrupt——这样后续 session/resume 可无缝续聊（含中断补发）。
        """
        self._ended = {"reason": reason}
        self._publish_status({"ended": self._ended, "status": "idle", "waiting": {"kind": "none"}})
        self.store.touch(self.session_id, status="quit")

    # ---------------------------------------------------------------- 查询

    def get_status(self, fields: list[str] | None = None) -> dict:
        state = self._state()
        snap = self._snapshot_fields(state, full=True)
        # 刷新运行时派生字段（status/waiting 依赖实时中断判断）
        snap["status"] = self._derive_status(state)
        snap["waiting"] = self._derive_waiting(state)
        if fields:
            wanted = E.sanitize_status_fields(fields)
            result = {f: snap.get(f) for f in wanted}
            result["session_id"] = self.session_id
            result["fields"] = wanted
            result["revision"] = self._revision
            return result
        snap["session_id"] = self.session_id
        snap["fields"] = list(E.STATUS_FIELDS)
        return snap

    def get_tree(self, path: str | None = None) -> dict:
        tree = self._state().get("prd_draft") or []
        if path:
            node = find_value_node(tree, path)
            return {"tree": node} if node else {"tree": None}
        return {"tree": tree}

    def get_gaps(self) -> dict:
        return {"gap_list": self._state().get("gap_list") or []}

    def get_conversions(self) -> dict:
        return {"conversion_log": self._state().get("conversion_log") or []}

    def get_prd(self) -> dict:
        state = self._state()
        return {
            "markdown": state.get("final_prd") or "",
            "unresolved_gaps": state.get("unresolved_gaps") or None,
        }

    def get_commands(self) -> dict:
        state = self._state()
        phase = state.get("phase", "interview")
        at_await = bool(self._collect_interrupts())
        cmds = [
            {"command": "help", "description": "显示命令列表", "available": True},
            {"command": "status", "description": "查看会话状态", "available": True},
            {"command": "view", "description": "查看取值树", "available": True},
            {"command": "skip", "description": "跳过当前单元", "available": phase == "interview" and at_await},
            {"command": "finish", "description": "结束访谈进入审核", "available": phase == "interview" and at_await},
            {"command": "undo", "description": "回退最近一次转录", "available": phase == "interview" and at_await},
            {"command": "quit", "description": "退出（保存 checkpoint）", "available": True},
        ]
        return {"commands": cmds}


# ---------------------------------------------------------------- 工厂

def seed_new_session(
    session_id: str,
    cfg: Config,
    graph: CompiledStateGraph,
    llm,
    emit: Emit,
    store: SessionStore,
    brief: str = "",
    template: list[dict] | None = None,
) -> Session:
    """新建会话：实例化取值树 + 种入初始状态（graph 与 llm 由 Backend 一致构建）。"""
    if template is None:
        template = _load_template_for(cfg)
    tree = instantiate_value_tree(template)
    created_at = _now()
    graph.update_state(
        {"configurable": {"thread_id": session_id}},
        default_state(template, brief) | {
            "prd_draft": tree,
            "brief": brief,
            "polish_blocks": [],
            "created_at": created_at,
        },
    )
    session = Session(session_id, cfg, graph, llm, emit, store, created_at=created_at)
    session._last_tree_sig = _tree_signature(tree)
    tpl_title = template[0]["title"] if template else "PRD"
    store.register(session_id, tpl_title, stage="interview")
    return session


def rehydrate_session(
    session_id: str,
    cfg: Config,
    graph: CompiledStateGraph,
    llm,
    emit: Emit,
    store: SessionStore,
) -> Session:
    """恢复会话：从 checkpoint 读取状态重建 Session。"""
    snap = graph.get_state({"configurable": {"thread_id": session_id}})
    state = dict(snap.values) if snap and snap.values else {}
    if not state:
        raise ProtocolError(4001, "会话不存在")
    created_at = state.get("created_at") or _now()
    session = Session(session_id, cfg, graph, llm, emit, store, created_at=created_at)
    session._last_tree_sig = _tree_signature(state.get("prd_draft"))
    return session


def _load_template_for(cfg: Config) -> list[dict]:
    if cfg.template_path:
        return load_template(cfg.template_path)
    return load_default_template()
