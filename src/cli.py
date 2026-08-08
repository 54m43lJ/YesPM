"""CLI 入口：python -m src.cli new [brief] | resume [id] | list

- new：新建会话并进入 REPL（brief 可省略，进入后补填）；
- resume [id]：从 checkpoint 恢复会话续聊（默认最近一次）；
- list：列出全部会话。
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime

from langgraph.checkpoint.sqlite import SqliteSaver

from .config import load_config
from .graph import build_graph
from .repl import REPL
from .state import default_state
from .tools.llm import build_llm
from .tools.template_loader import TemplateValidationError, load_default_template, load_template
from .tools.value_tree import instantiate_value_tree


def _load_sessions(cfg) -> dict:
    path = cfg.sessions_json_path
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sessions(cfg, sessions: dict) -> None:
    cfg.sessions_json_path.write_text(
        json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_template(cfg) -> list[dict]:
    if cfg.template_path:
        return load_template(cfg.template_path)
    return load_default_template()


def _init_session(graph, config, repl, brief: str, template: list[dict]) -> None:
    """初始化会话状态：brief + 实例化取值树。"""
    if not brief:
        brief = repl.ask_brief()
    tree = instantiate_value_tree(template)
    graph.update_state(
        config,
        default_state(template, brief) | {"prd_draft": tree, "brief": brief},
    )


def cmd_new(cfg, args) -> int:
    template = _load_template(cfg)
    llm = build_llm(cfg)
    brief = " ".join(args.brief or [])
    thread_id = uuid.uuid4().hex[:12]
    sessions = _load_sessions(cfg)
    sessions[thread_id] = {
        "brief": brief,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "active",
    }
    _save_sessions(cfg, sessions)

    with SqliteSaver.from_conn_string(str(cfg.db_path)) as saver:
        graph = build_graph(cfg, llm, saver)
        repl = REPL(graph, thread_id, cfg, llm)
        print(f"YesPM 新会话 {thread_id}（模板：{'自定义' if cfg.template_path else '默认 PRD'}，模型：{cfg.model}）")
        print("输入 /help 查看可用命令")
        _init_session(graph, repl.config, repl, brief, template)
        outcome = repl.run()
        sessions[thread_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        sessions[thread_id]["status"] = "done" if outcome == "done" else "active"
        _save_sessions(cfg, sessions)
    return 0


def cmd_resume(cfg, args) -> int:
    sessions = _load_sessions(cfg)
    if args.id:
        thread_id = args.id
        if thread_id not in sessions:
            print(f"会话 {thread_id} 不存在（用 list 查看）")
            return 1
    elif sessions:
        thread_id = max(sessions, key=lambda k: sessions[k].get("updated_at", ""))
    else:
        print("没有可恢复的会话（用 new 创建）")
        return 1

    llm = build_llm(cfg)
    with SqliteSaver.from_conn_string(str(cfg.db_path)) as saver:
        graph = build_graph(cfg, llm, saver)
        repl = REPL(graph, thread_id, cfg, llm)
        st = graph.get_state(repl.config).values
        if not st:
            print(f"会话 {thread_id} 没有可恢复的 checkpoint")
            return 1
        brief = st.get("brief") or ""
        print(f"恢复会话 {thread_id}（{sessions[thread_id].get('created_at', '')}）")
        if brief:
            print(f"产品设想：{brief}")
        outcome = repl.run()
        sessions[thread_id]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        sessions[thread_id]["status"] = "done" if outcome == "done" else "active"
        _save_sessions(cfg, sessions)
    return 0


def cmd_list(cfg) -> int:
    sessions = _load_sessions(cfg)
    if not sessions:
        print("（无会话）")
        return 0
    for tid, info in sessions.items():
        status = info.get("status", "?")
        brief = (info.get("brief") or "")[:40]
        print(f"{tid}  [{status}]  {info.get('created_at', '')}  {brief}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yespm", description="YesPM - AI 驱动的 PRD 文档生成器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_new = sub.add_parser("new", help="新建会话并开始访谈")
    p_new.add_argument("brief", nargs="*", help="产品设想简述（可省略）")
    p_resume = sub.add_parser("resume", help="恢复会话续聊")
    p_resume.add_argument("id", nargs="?", help="会话 id（默认最近一次）")
    sub.add_parser("list", help="列出会话")
    args = parser.parse_args(argv)

    cfg = load_config()
    try:
        if args.cmd == "new":
            return cmd_new(cfg, args)
        if args.cmd == "resume":
            return cmd_resume(cfg, args)
        if args.cmd == "list":
            return cmd_list(cfg)
    except TemplateValidationError as e:
        print(f"模板错误：{e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
