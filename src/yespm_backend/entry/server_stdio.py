"""yespm-server：stdio 桥（TUI 子进程用），无 UI。

启动：`yespm-server [--db <path>] [--template <path>]`；握手见 STDIO.md §3。
"""
from __future__ import annotations

import argparse
import sys

from ..config import load_config
from ..engine.backend import Backend
from ..protocol.stdio import run_stdio
from ..template.loader import TemplateValidationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yespm-server", description="YesPM stdio 桥")
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

    try:
        return run_stdio(backend)
    except KeyboardInterrupt:
        return 130
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
