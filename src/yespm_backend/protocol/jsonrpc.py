"""JSON-RPC 2.0 信封编解码 + 错误码映射（PROTOCOL §2 / §11）。

错误以四位状态码表达；ProtocolError.code → JSON-RPC error.response。
"""
from __future__ import annotations

from typing import Any

from ..engine.errors import ProtocolError

JSONRPC_VERSION = "2.0"


def make_request(msg_id: Any, method: str, params: dict) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "method": method, "params": params}


def make_response(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}


def make_notification(method: str, params: dict) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}


def make_error(msg_id: Any, code: int, message: str, data: dict | None = None) -> dict:
    err = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "error": err}


def error_from_exception(msg_id: Any, e: Exception) -> dict:
    if isinstance(e, ProtocolError):
        return make_error(msg_id, e.code, e.message, e.data or None)
    # 未知异常归为引擎内部错误
    return make_error(msg_id, 5901, f"内部错误：{e}")


def is_request(msg: dict) -> bool:
    return "method" in msg and "id" in msg


def is_notification(msg: dict) -> bool:
    return "method" in msg and "id" not in msg
