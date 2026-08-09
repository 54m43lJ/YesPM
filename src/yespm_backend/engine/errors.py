"""引擎层错误：携带协议四位状态码（PROTOCOL §11）。

引擎抛出 ProtocolError → 协议层（dispatch/jsonrpc）捕获并映射为 JSON-RPC error 响应。
错误分级与码表见 PROTOCOL §11。
"""
from __future__ import annotations


class ProtocolError(Exception):
    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}


class FatalEngineError(ProtocolError):
    """致命错误（5xxx）：引擎无法继续。"""
