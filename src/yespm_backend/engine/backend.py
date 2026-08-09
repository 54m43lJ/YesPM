"""Backend：引擎容器 + 协议方法分发器。

- 持有 cfg / SessionStore / SqliteSaver（共享 checkpointer，单一状态源）；
- 每个 session 自带一份 graph（其节点捕获该会话的 emit + llm），checkpointer 共享；
- dispatch(method, params, emit) → result：协议层唯一入口，方法 → 引擎操作（无命令文本路由）。

依赖方向（backend/ARCHITECTURE §7）：engine → graph/polish；protocol/entry → engine。
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver

from ..config import Config
from ..graph.builder import build_interview_graph
from ..llm.client import build_llm
from ..template.loader import TemplateValidationError, load_default_template, load_template
from .errors import ProtocolError
from .session import (
    Session,
    _load_template_for,
    rehydrate_session,
    seed_new_session,
)
from .store import SessionStore

Emit = Callable[[str, dict], None]


class Backend:
    """进程级引擎容器。一个 Backend 服务多个会话（跨会话并行）。"""

    def __init__(self, cfg: Config, db_path: str | None = None):
        self.cfg = cfg
        self.db_path = db_path or str(cfg.db_path)
        self.store = SessionStore(cfg.sessions_json_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.checkpointer = SqliteSaver(self._conn)
        self.checkpointer.setup()
        self._sessions: dict[str, Session] = {}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def _build_graph(self, emit: Emit):
        llm = build_llm(self.cfg)
        graph = build_interview_graph(self.cfg, llm, self.checkpointer, emit)
        return graph, llm

    def _get_or_resume(self, session_id: str, emit: Emit) -> Session:
        if session_id in self._sessions:
            return self._sessions[session_id]
        if not self.store.exists(session_id):
            raise ProtocolError(4001, "会话不存在")
        graph, llm = self._build_graph(emit)
        session = rehydrate_session(session_id, self.cfg, graph, llm, emit, self.store)
        self._sessions[session_id] = session
        return session

    # ---------------------------------------------------------------- dispatch

    def dispatch(self, method: str, params: dict, emit: Emit) -> Any:
        """协议方法 → 引擎操作（唯一入口）。返回 result 对象（不含 error）。"""
        handler = self._METHODS.get(method)
        if handler is None:
            raise ProtocolError(4603, f"方法不存在：{method}")
        return handler(self, params, emit)

    # ---------------------------------------------------------------- 生命周期

    def _session_create(self, params: dict, emit: Emit) -> dict:
        template_arg = params.get("template")
        if template_arg:
            try:
                template = load_template(template_arg)
            except TemplateValidationError as e:
                raise ProtocolError(5701, str(e))
            except OSError as e:
                raise ProtocolError(5701, f"模板读取失败：{e}")
        else:
            template = _load_template_for(self.cfg)
        brief = params.get("brief", "")
        session_id = params.get("session_id") or f"s-{uuid.uuid4().hex[:10]}"
        graph, llm = self._build_graph(emit)
        session = seed_new_session(
            session_id, self.cfg, graph, llm, emit, self.store, brief=brief, template=template
        )
        self._sessions[session_id] = session
        session.start()
        snapshot = session.get_status()
        return {"session_id": session_id, "snapshot": snapshot}

    def _session_resume(self, params: dict, emit: Emit) -> dict:
        session_id = params.get("session_id")
        if not session_id:
            raise ProtocolError(4604, "缺少 session_id")
        session = self._get_or_resume(session_id, emit)
        session.rehydrate()
        return {"snapshot": session.get_status()}

    def _session_list(self, params: dict, emit: Emit) -> dict:
        return {"sessions": self.store.list()}

    def _session_delete(self, params: dict, emit: Emit) -> dict:
        session_id = params.get("session_id")
        if not session_id or not self.store.exists(session_id):
            raise ProtocolError(4001, "会话不存在")
        self.store.delete(session_id)
        self._sessions.pop(session_id, None)
        return {"deleted": True}

    def _session_quit(self, params: dict, emit: Emit) -> dict:
        session_id = params.get("session_id")
        session = self._get_or_resume(session_id, emit)
        session.quit(reason="quit")
        return {"ended": True}

    # ---------------------------------------------------------------- 交互

    def _input_send(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        accepted, queued = session.send_input(params.get("text", ""))
        return {"accepted": accepted, "queued": queued}

    def _command_skip(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return {"accepted": session.skip()}

    def _command_finish(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return {"accepted": session.finish()}

    def _command_undo(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return {"accepted": session.undo()}

    def _proposal_respond(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        ids = params.get("proposal_ids") or []
        action = params.get("action", "")
        return {"accepted": session.respond_proposal(ids, action)}

    # ---------------------------------------------------------------- 查询

    def _query_status(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return session.get_status(params.get("fields"))

    def _query_tree(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return session.get_tree(params.get("path"))

    def _query_commands(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return session.get_commands()

    def _query_template(self, params: dict, emit: Emit) -> dict:
        tpl_arg = params.get("template")
        try:
            tpl = load_template(tpl_arg) if tpl_arg else load_default_template()
        except (TemplateValidationError, OSError) as e:
            raise ProtocolError(5701, str(e))
        return {"template": tpl}

    def _query_prd(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return session.get_prd()

    def _query_gaps(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return session.get_gaps()

    def _query_conversions(self, params: dict, emit: Emit) -> dict:
        session = self._get_or_resume(params.get("session_id", ""), emit)
        return session.get_conversions()

    # ---------------------------------------------------------------- 配置

    def _config_get(self, params: dict, emit: Emit) -> dict:
        return {
            "provider": "openai-compatible",
            "model": self.cfg.model,
            "template": str(self.cfg.template_path) if self.cfg.template_path else "default",
            "db_path": self.db_path,
            "version": self.cfg.version,
        }

    _METHODS = {
        "session/create": _session_create,
        "session/resume": _session_resume,
        "session/list": _session_list,
        "session/delete": _session_delete,
        "session/quit": _session_quit,
        "input/send": _input_send,
        "command/skip": _command_skip,
        "command/finish": _command_finish,
        "command/undo": _command_undo,
        "proposal/respond": _proposal_respond,
        "query/status": _query_status,
        "query/tree": _query_tree,
        "query/commands": _query_commands,
        "query/template": _query_template,
        "query/prd": _query_prd,
        "query/gaps": _query_gaps,
        "query/conversions": _query_conversions,
        "config/get": _config_get,
    }
