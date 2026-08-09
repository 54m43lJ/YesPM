"""yespm-ws：WebSocket 桥（Web 前端用）。

启动：`yespm-ws [--host 127.0.0.1] [--port 8765] [--db <path>] [--template <path>]`。
"""
from __future__ import annotations

import argparse

from ..config import load_config
from ..engine.backend import Backend
from ..protocol.websocket import run_ws
from ..template.loader import TemplateValidationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yespm-ws", description="YesPM WebSocket 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--db", metavar="PATH", help="SQLite checkpoint 路径")
    parser.add_argument("--template", metavar="PATH", help="模板 YAML 路径")
    parser.add_argument("--config", metavar="PATH", help="配置文件（.env）")
    parser.add_argument("--version", action="store_true", help="打印版本后退出")
    args = parser.parse_args(argv)

    if args.version:
        from .. import __version__
        print(__version__)
        return 0

    import os
    if args.db:
        os.environ["YESPM_DB_PATH"] = args.db
    if args.template:
        os.environ["YESPM_TEMPLATE"] = args.template

    cfg = load_config()
    try:
        backend = Backend(cfg)
    except TemplateValidationError as e:
        print(f"模板错误：{e}", file=sys.stderr)
        return 2

    print(f"yespm-ws 监听 {args.host}:{args.port}（健康检查 /healthz，主通道 /ws）")
    try:
        run_ws(backend, host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass
    finally:
        backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
