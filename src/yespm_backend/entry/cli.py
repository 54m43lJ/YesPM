"""CLI 前端（yespm）：参考实现，进程内传输（frontends/CLI.md）。

- 与后端同包，命令 `yespm`；通过 InProcessTransport 走同一 JSON-RPC 消息（不绕过协议）。
- 职责：交互解释与包装——`/` 前缀本地命令解析，其余 → input/send；事件渲染为终端输出。
- 零业务逻辑：操作可用性、输入暂存、中断都在引擎侧，CLI 直接展示后端错误（4103 等）。
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from ..config import load_config
from ..engine.backend import Backend
from ..engine.errors import ProtocolError
from ..protocol.transport import InProcessTransport
from ..template.loader import TemplateValidationError

HELP_TEXT = """可用命令（命令语法属前端，后端只提供结构化方法）：
  /help            显示命令列表
  /status          当前单元、已完成单元、取值树摘要
  /view [路径]      查看取值树（全部或指定路径）
  /skip            跳过当前单元（强制转录，进入下一单元）
  /finish          结束访谈阶段，进入全文档审核
  /y /n            润色阶段确认 / 拒绝高风险提案
  /undo            回退最近一次转录
  /paste           粘贴模式（多行输入，空行结束）
  /gaps            查看缺口清单（审核阶段）
  /conversions     查看转换日志（润色阶段）
  /prd             查看最终文档
  /quit            退出（保存 checkpoint，可恢复续聊）"""


class CliFrontend:
    """CLI 渲染与输入路由。状态由 session/status 事件驱动，零本地推断。"""

    def __init__(self, transport: InProcessTransport):
        self.transport = transport
        self.session_id: str | None = None
        self.state: dict[str, Any] = {}
        self.ended = False
        self._message_buf: dict[str, list[str]] = {}
        transport.on_notification(self._on_notification)

    # ---------------------------------------------------------------- 事件

    def _on_notification(self, event: str, params: dict) -> None:
        if event == "session/message":
            self._on_message(params)
        elif event == "session/status":
            self._on_status(params)
        elif event == "session/await_input":
            pass  # 由已收到的 status.waiting 推断可输入
        elif event == "log":
            sc = params.get("status_code", 0)
            if sc // 1000 >= 4:
                print(f"[错误 {sc}] {params.get('message', '')}", file=sys.stderr)
        # tree/changed, gaps/changed, prd/changed, conversions/changed：仅提示，按需查询

    def _on_message(self, params: dict) -> None:
        msg_id = params.get("message_id")
        sc = params.get("status_code")
        if sc == 2031:  # chunk
            delta = params.get("delta") or ""
            self._message_buf.setdefault(msg_id, []).append(delta)
            sys.stdout.write(delta)
            sys.stdout.flush()
        elif sc == 2032:  # complete
            text = params.get("text")
            if text is None:
                text = "".join(self._message_buf.pop(msg_id, []))
            else:
                self._message_buf.pop(msg_id, None)
            if not self._message_buf and msg_id is not None:
                pass
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _on_status(self, params: dict) -> None:
        self.state.update({k: v for k, v in params.items() if k not in ("session_id", "status_code", "fields", "revision")})
        if "ended" in params and params["ended"]:
            self.ended = True

    # ---------------------------------------------------------------- 渲染

    def _prompt(self) -> str:
        unit = self.state.get("current_unit")
        waiting = self.state.get("waiting") or {}
        stage = self.state.get("stage", "interview")
        if waiting.get("kind") == "proposal":
            return "PRD [润色确认] y/n> "
        if stage == "interview" and unit:
            title = unit.get("title") or ""
            ctx = title.strip()
            return f"PRD [{ctx}]> " if ctx else "PRD> "
        return f"PRD [{stage}]> "

    def _print_error(self, e: ProtocolError) -> None:
        print(f"[{e.code}] {e.message}", file=sys.stderr)

    # ---------------------------------------------------------------- 命令

    def _cmd_status(self) -> None:
        try:
            res = self.transport.request("query/status", {"session_id": self.session_id})
        except ProtocolError as e:
            return self._print_error(e)
        stage = res.get("stage")
        unit = res.get("current_unit") or {}
        done = res.get("units_done") or []
        print(f"阶段: {stage} | 当前单元: {unit.get('title', '-')} | 已完成: {', '.join(done) or '-'}")
        try:
            tree = self.transport.request("query/tree", {"session_id": self.session_id})["tree"]
            filled = sum(1 for n in self._walk(tree) if n.get("kind") == "field" and n.get("value"))
            total = sum(1 for n in self._walk(tree) if n.get("kind") == "field")
            print(f"取值树: 字段 {filled}/{total} 已填")
        except ProtocolError:
            pass

    def _cmd_view(self, path: str) -> None:
        try:
            res = self.transport.request("query/tree", {"session_id": self.session_id, "path": path or None})
        except ProtocolError as e:
            return self._print_error(e)
        tree = res.get("tree")
        if not tree:
            print("（无取值）" if not path else f"路径 {path} 不存在")
            return
        from ..tree.value_tree import tree_to_text
        print(tree_to_text([tree] if isinstance(tree, dict) else tree))

    def _cmd_gaps(self) -> None:
        try:
            res = self.transport.request("query/gaps", {"session_id": self.session_id})
        except ProtocolError as e:
            return self._print_error(e)
        gaps = res.get("gap_list") or []
        if not gaps:
            print("（无缺口）")
            return
        for g in gaps:
            print(f"- {g.get('path')} [{g.get('dimension')}] {g.get('reason')}")

    def _cmd_conversions(self) -> None:
        try:
            res = self.transport.request("query/conversions", {"session_id": self.session_id})
        except ProtocolError as e:
            return self._print_error(e)
        log = res.get("conversion_log") or []
        if not log:
            print("（无转换记录）")
            return
        for e in log:
            print(f"- [{e.get('scenario')}/{e.get('risk')}] {e.get('position')} → {e.get('status')}")

    def _cmd_prd(self) -> None:
        try:
            res = self.transport.request("query/prd", {"session_id": self.session_id})
        except ProtocolError as e:
            return self._print_error(e)
        md = res.get("markdown") or ""
        print(md)

    def _paste_mode(self) -> str:
        print("（粘贴模式：逐行输入，空行结束）")
        lines: list[str] = []
        while True:
            try:
                line = input("...> ")
            except EOFError:
                return ""
            if line.strip() == "":
                break
            lines.append(line)
        return "\n".join(lines)

    def _handle_command(self, line: str) -> bool:
        """处理 / 命令。返回 False 表示应退出主循环。"""
        cmd, _, arg = line.partition(" ")
        cmd = cmd.lower()
        if cmd == "/help":
            print(HELP_TEXT)
        elif cmd == "/status":
            self._cmd_status()
        elif cmd == "/view":
            self._cmd_view(arg.strip())
        elif cmd == "/gaps":
            self._cmd_gaps()
        elif cmd == "/conversions":
            self._cmd_conversions()
        elif cmd == "/prd":
            self._cmd_prd()
        elif cmd == "/paste":
            text = self._paste_mode()
            if text:
                self._send_input(text)
        elif cmd == "/skip":
            self._call_command("command/skip")
        elif cmd == "/finish":
            self._call_command("command/finish")
        elif cmd == "/undo":
            self._call_command("command/undo")
        elif cmd in ("/y", "/n", "/yes", "/no"):
            self._respond_proposal(cmd in ("/y", "/yes"))
        elif cmd == "/quit":
            try:
                self.transport.request("session/quit", {"session_id": self.session_id})
            except ProtocolError as e:
                self._print_error(e)
            return False
        else:
            print(f"未知命令 {cmd}（输入 /help 查看可用命令）")
        return True

    def _call_command(self, method: str) -> None:
        try:
            self.transport.request(method, {"session_id": self.session_id})
        except ProtocolError as e:
            self._print_error(e)

    def _send_input(self, text: str) -> None:
        try:
            self.transport.request("input/send", {"session_id": self.session_id, "text": text})
        except ProtocolError as e:
            self._print_error(e)

    def _respond_proposal(self, accept: bool) -> None:
        waiting = self.state.get("waiting") or {}
        ids = waiting.get("proposal_ids") or []
        if not ids:
            print("（当前无待确认提案）")
            return
        try:
            self.transport.request("proposal/respond", {
                "session_id": self.session_id,
                "proposal_ids": ids,
                "action": "apply" if accept else "reject",
            })
        except ProtocolError as e:
            self._print_error(e)

    @staticmethod
    def _walk(nodes: list | dict):
        if isinstance(nodes, dict):
            nodes = nodes.get("children") or nodes.get("instances") or []
        for n in nodes or []:
            yield n
            yield from CliFrontend._walk(n)

    # ---------------------------------------------------------------- 主循环

    def run(self) -> int:
        while not self.ended:
            try:
                line = input(self._prompt())
            except (EOFError, KeyboardInterrupt):
                print()
                try:
                    self.transport.request("session/quit", {"session_id": self.session_id})
                except ProtocolError:
                    pass
                break
            if line.strip() == "":
                continue
            if line.startswith("/"):
                if not self._handle_command(line):
                    break
            else:
                waiting = self.state.get("waiting") or {}
                if waiting.get("kind") == "proposal":
                    # 提案确认面板下，非命令输入按 y/n 处理
                    self._respond_proposal(line.strip().lower() in ("y", "yes", "应用", "apply"))
                else:
                    self._send_input(line)
        return 0


def _create_and_run(backend: Backend, brief: str) -> int:
    transport = InProcessTransport(backend)
    cli = CliFrontend(transport)
    try:
        result = transport.request("session/create", {"brief": brief})
    except ProtocolError as e:
        print(f"[{e.code}] {e.message}", file=sys.stderr)
        return 1
    cli.session_id = result["session_id"]
    print(f"YesPM 会话 {cli.session_id}（模型：{backend.cfg.model}）已就绪。输入 /help 查看命令。")
    return cli.run()


def _resume_and_run(backend: Backend, session_id: str) -> int:
    transport = InProcessTransport(backend)
    cli = CliFrontend(transport)
    try:
        result = transport.request("session/resume", {"session_id": session_id})
    except ProtocolError as e:
        print(f"[{e.code}] {e.message}", file=sys.stderr)
        return 1
    cli.session_id = session_id
    print(f"恢复会话 {session_id}。输入 /help 查看命令。")
    return cli.run()


def _list_sessions(backend: Backend) -> int:
    transport = InProcessTransport(backend)
    try:
        result = transport.request("session/list", {})
    except ProtocolError as e:
        print(f"[{e.code}] {e.message}", file=sys.stderr)
        return 1
    sessions = result.get("sessions") or []
    if not sessions:
        print("（无会话）")
        return 0
    for s in sessions:
        print(f"{s['session_id']}  [{s.get('stage', '?')}]  {s.get('created_at', '')}  {s.get('template_title', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yespm", description="YesPM - AI 驱动的 PRD 文档生成器")
    parser.add_argument("brief", nargs="*", help="产品设想简述（省略则新建空会话进入访谈）")
    parser.add_argument("--resume", metavar="ID", help="恢复历史会话")
    parser.add_argument("--list", action="store_true", help="列出全部会话")
    parser.add_argument("--template", metavar="PATH", help="自定义模板 YAML 路径")
    parser.add_argument("--db", metavar="PATH", help="SQLite checkpoint 路径")
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.template:
        import os
        os.environ["YESPM_TEMPLATE"] = args.template
        cfg = load_config()
    if args.db:
        import os
        os.environ["YESPM_DB_PATH"] = args.db
        cfg = load_config()

    try:
        backend = Backend(cfg)
    except TemplateValidationError as e:
        print(f"模板错误：{e}", file=sys.stderr)
        return 2

    try:
        if args.list:
            return _list_sessions(backend)
        if args.resume:
            return _resume_and_run(backend, args.resume)
        return _create_and_run(backend, brief=" ".join(args.brief))
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
