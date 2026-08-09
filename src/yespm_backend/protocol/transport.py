"""协议客户端/服务端核心：Transport 抽象 + 请求分发 + 事件回流。

- `serve_request(backend, request, emit)`：处理一条 JSON-RPC 请求 → 返回响应对象。
- `InProcessTransport`：进程内传输（CLI 用）——走同一 JSON-RPC 消息，不绕过协议。
- stdio / websocket 传输见各自模块。

依赖方向：protocol → engine（protocol 调用 backend.dispatch）。
"""
from __future__ import annotations

import json
from typing import Any, Callable
from uuid import uuid4

from ..engine.backend import Backend
from ..engine.errors import ProtocolError
from . import jsonrpc

Emit = Callable[[str, dict], None]


def serve_request(backend: Backend, request: dict, emit: Emit) -> dict | None:
    """处理一条 JSON-RPC 请求；返回响应 dict（带 id）。"""
    msg_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    if not isinstance(method, str):
        return jsonrpc.make_error(msg_id, 4602, "无效请求：缺少 method")

    try:
        result = backend.dispatch(method, params, emit)
        return jsonrpc.make_response(msg_id, result)
    except ProtocolError as e:
        return jsonrpc.make_error(msg_id, e.code, e.message, e.data or None)
    except Exception as e:  # 引擎内部故障
        return jsonrpc.make_error(msg_id, 5901, f"内部错误：{e}")


def encode_frame(msg: dict) -> str:
    return json.dumps(msg, ensure_ascii=False)


def decode_frame(text: str) -> dict:
    return json.loads(text)


class InProcessTransport:
    """进程内传输：CLI 与后端同进程，但**走同一 JSON-RPC 消息**（不绕过协议）。

    request() 同步派发；期间后端经 emit 发布的通知实时投递到 notification_handler。
    """

    def __init__(self, backend: Backend):
        self.backend = backend
        self.notification_handler: Callable[[str, dict], None] | None = None

    def on_notification(self, handler: Callable[[str, dict], None]) -> None:
        self.notification_handler = handler

    def _emit(self, event: str, params: dict) -> None:
        if self.notification_handler:
            self.notification_handler(event, params)

    def request(self, method: str, params: dict | None = None) -> Any:
        msg_id = uuid4().hex[:8]
        request = jsonrpc.make_request(msg_id, method, params or {})
        response = serve_request(self.backend, request, self._emit)
        if response is None:
            return None
        if "error" in response:
            err = response["error"]
            raise ProtocolError(err.get("code", 5901), err.get("message", "错误"), err.get("data"))
        return response.get("result")

