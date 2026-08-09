"""stdio 传输适配（STDIO.md）：JSON Lines 帧格式。

- stdin：请求（client → server）；stdout：响应 + 事件通知；stderr：日志。
- 启动握手：引擎就绪后向 stdout 推送 `server/ready`（传输级，非语义层事件）。
- 优雅关闭：stdin EOF / session/quit → 保存 checkpoint 后退出（码 0）。
"""
from __future__ import annotations

import json
import sys
from typing import TextIO

from ..engine.backend import Backend
from . import jsonrpc
from .transport import serve_request


def _write_stdout(stream: TextIO, msg: dict) -> None:
    stream.write(json.dumps(msg, ensure_ascii=False) + "\n")
    stream.flush()


def _write_stderr(stream: TextIO, msg: str) -> None:
    stream.write(msg + "\n")
    stream.flush()


def run_stdio(backend: Backend, stdin: TextIO | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """stdio 主循环。返回退出码。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    def emit(event: str, params: dict) -> None:
        # 事件 → JSON-RPC 通知（stdout）
        _write_stdout(stdout, jsonrpc.make_notification(event, params))
        # log 事件额外落 stderr
        if event == "log":
            sc = params.get("status_code", 0)
            level = {1: "DEBUG", 2: "INFO", 3: "WARN", 4: "ERROR", 5: "FATAL"}.get(sc // 1000, "LOG")
            _write_stderr(stderr, f"[{level}] {params.get('message', '')}")

    # 启动握手
    _write_stdout(stdout, {
        "jsonrpc": "2.0",
        "method": "server/ready",
        "params": {"version": backend.cfg.version, "db": backend.db_path},
    })

    exit_code = 0
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            _write_stdout(stdout, jsonrpc.make_error(None, 4601, "JSON 解析错误"))
            continue
        if not isinstance(request, dict):
            _write_stdout(stdout, jsonrpc.make_error(None, 4602, "无效请求"))
            continue
        response = serve_request(backend, request, emit)
        if response is not None:
            _write_stdout(stdout, response)

    # stdin EOF → 优雅退出
    return exit_code
