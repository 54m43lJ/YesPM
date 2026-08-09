"""WebSocket 传输适配（WEBSOCKET.md）：JSON-RPC 主通道 + /healthz。

- `/healthz` GET → 健康检查；`/ws` WebSocket → JSON 文本帧，一帧一消息。
- 一个连接可承载多个会话（消息内 session_id）；连接断开不销毁会话（checkpoint 持续）。
- 用标准库 + `websockets` 实现（无新增依赖）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets
from websockets import Response, serve

from ..engine.backend import Backend
from . import jsonrpc
from .transport import serve_request


class WSBackend:
    """WebSocket 服务端：每个连接独立处理请求，事件回流到该连接。"""

    def __init__(self, backend: Backend):
        self.backend = backend

    async def handler(self, ws) -> None:
        loop = asyncio.get_running_loop()

        def emit(event: str, params: dict) -> None:
            # 后端在工作线程中调用 emit；投递回事件循环异步发送
            payload = json.dumps(jsonrpc.make_notification(event, params), ensure_ascii=False)
            asyncio.run_coroutine_threadsafe(ws.send(payload), loop)

        async for raw in ws:
            try:
                request = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                await ws.send(json.dumps(jsonrpc.make_error(None, 4601, "JSON 解析错误")))
                continue
            if not isinstance(request, dict):
                await ws.send(json.dumps(jsonrpc.make_error(None, 4602, "无效请求")))
                continue
            # 后端 dispatch 同步阻塞（含 LLM 调用）→ 线程池执行，避免阻塞事件循环
            try:
                response = await loop.run_in_executor(
                    None, lambda: serve_request(self.backend, request, emit)
                )
            except Exception as e:
                response = jsonrpc.make_error(request.get("id"), 5901, f"内部错误：{e}")
            if response is not None:
                await ws.send(json.dumps(response, ensure_ascii=False))


def _make_process_request(backend: Backend):
    def process_request(connection, request):
        path = getattr(request, "path", "/")
        if path == "/healthz":
            body = json.dumps({"status": "ok", "version": backend.cfg.version}).encode("utf-8")
            return Response(
                200,
                "OK",
                websockets.Headers({"content-type": "application/json"}),
                body,
            )
        return None
    return process_request


def run_ws(backend: Backend, host: str = "127.0.0.1", port: int = 8765) -> None:
    """启动 WebSocket 服务（阻塞）。"""
    ws_backend = WSBackend(backend)

    async def main() -> None:
        async with serve(
            ws_backend.handler,
            host,
            port,
            process_request=_make_process_request(backend),
        ):
            await asyncio.Future()  # run forever

    asyncio.run(main())
