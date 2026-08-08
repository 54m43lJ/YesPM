"""交互层：REPL 会话模式（图外事件循环）。

- 输入路由：以 / 开头 → 命令分发；其余 → 转发给当前活跃节点；
- 提示符带上下文：`PRD [3.2 核心功能详述]>`；
- 非等待期输入由后台线程暂存到下一个中断点，不丢弃；
- Ctrl+C 两级语义：第一次中断流式输出/给出提示，再次按强制退出；
- 自动 checkpoint：所有状态经 LangGraph SqliteSaver 持久化，/quit 后可恢复续聊。
"""
from __future__ import annotations

import queue
import sys
import threading

from langgraph.types import Command

from .pipeline import run_pipeline
from .tools.value_tree import find_value_node, summarize_tree, tree_to_text

HELP_TEXT = """可用命令：
  /help            显示命令列表与当前可用操作
  /status          当前单元、已完成单元、取值树摘要
  /view [路径]      查看取值树（全部或指定路径）
  /skip            跳过当前单元（强制转录，进入下一单元）
  /finish          结束访谈阶段，进入全文档审核
  /y /n /confirm   润色阶段确认/拒绝高风险提案（支持 ya=全部应用 na=全部拒绝）
  /undo            回退最近一次转录/转换
  /paste           粘贴模式（多行输入，空行结束）
  /quit            退出（保存 checkpoint，可恢复续聊）"""


class REPL:
    def __init__(self, graph, thread_id: str, cfg, llm, inputs=None):
        self.graph = graph
        self.thread_id = thread_id
        self.cfg = cfg
        self.llm = llm
        self.config = {"configurable": {"thread_id": thread_id}}
        self.quit = False
        self._queue: queue.Queue | None = None
        if inputs is not None:
            self._iter = iter(inputs)
        else:
            self._queue = queue.Queue()
            threading.Thread(target=self._reader, daemon=True).start()

    # ---------------------------------------------------------------- 输入

    def _reader(self):
        try:
            for line in sys.stdin:
                self._queue.put(line.rstrip("\n"))
        except Exception:
            pass
        self._queue.put(None)

    def _read(self, prompt: str) -> str | None:
        """读一行输入；None 表示退出。Ctrl+C 两级语义。"""
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            try:
                if self._queue is not None:
                    line = self._queue.get()
                else:
                    line = next(self._iter)
                return None if line is None else line
            except StopIteration:
                return None
            except KeyboardInterrupt:
                print("\n（再按一次 Ctrl+C 强制退出；或输入 /quit 保存退出）", flush=True)
                try:
                    if self._queue is not None:
                        line = self._queue.get()
                    else:
                        line = next(self._iter)
                    return None if line is None else line
                except (KeyboardInterrupt, StopIteration):
                    return None

    def _paste_mode(self) -> str:
        print("（粘贴模式：逐行输入，空行结束）", flush=True)
        lines = []
        while True:
            line = self._read("...> ")
            if line is None:
                self.quit = True
                return ""
            if line.strip() == "":
                break
            lines.append(line)
        return "\n".join(lines)

    # ---------------------------------------------------------------- 命令

    def _state(self) -> dict:
        return self.graph.get_state(self.config).values

    def _cmd_status(self):
        st = self._state()
        unit = st.get("current_unit")
        label = unit.get("label") if unit else "-"
        done = st.get("units_done") or []
        gaps = st.get("interview_gaps") or []
        print(
            f"阶段: {st.get('phase')} | 当前单元: {label} | 已完成: {', '.join(done) if done else '-'}"
        )
        if gaps:
            print("待澄清缺口:")
            for g in gaps:
                print(f"  - {g.get('path')} [{g.get('dimension')}] {g.get('reason')}")
        tree = st.get("prd_draft")
        if tree:
            s = summarize_tree(tree)
            print(
                f"取值树: 字段 {s['filled']}/{s['total']} 已填；"
                f"必填 {s['required_filled']}/{s['required_total']} 已填"
            )

    def _cmd_view(self, arg: str):
        tree = self._state().get("prd_draft") or []
        if not tree:
            print("（取值树为空）")
            return
        if arg:
            node = find_value_node(tree, arg)
            if node is None:
                print(f"路径 {arg} 不存在")
                return
            print(tree_to_text([node]))
        else:
            print(tree_to_text(tree))

    def _handle_command(self, cmd: str, arg: str) -> str | None:
        """REPL 本地命令；返回 None 表示已处理，否则返回交给图的命令串。"""
        if cmd == "/help":
            print(HELP_TEXT)
            return None
        if cmd == "/status":
            self._cmd_status()
            return None
        if cmd == "/view":
            self._cmd_view(arg.strip())
            return None
        if cmd == "/paste":
            return self._paste_mode()
        if cmd == "/quit":
            self.quit = True
            return None
        if cmd in ("/skip", "/finish", "/undo"):
            return cmd
        if cmd in ("/y", "/n", "/confirm", "/ya", "/na"):
            print("（/y /n /ya /na 仅润色阶段确认高风险提案时可用）")
            return None
        print(f"未知命令 {cmd}（输入 /help 查看可用命令）")
        return None

    # ---------------------------------------------------------------- 交互点

    def handle_interrupt(self, state: dict, iv: dict) -> str | None:
        """访谈中断点：打印提示符读取输入。返回 resume 值或 None（退出）。"""
        unit = state.get("current_unit") or {}
        label = unit.get("label") if isinstance(unit, dict) else ""
        while True:
            line = self._read(f"PRD [{label}]> ")
            if line is None:
                return None
            if line.strip() == "":
                continue
            if line.startswith("/"):
                cmd, _, arg = line.partition(" ")
                handled = self._handle_command(cmd, arg)
                if handled is not None:
                    return handled
                continue
            return line

    def confirm_proposal(self, proposal: dict, idx: int, total: int) -> str:
        """高风险提案确认。返回 y/n/ya/na。"""
        scenario = proposal.get("scenario")
        reason = proposal.get("reason") or ""
        location = proposal.get("location")
        while True:
            line = self._read(
                f"PRD [润色]> 提案 {idx}/{total} [{scenario}] 位置 {location}"
                f"（{reason}）—— 应用? [y=应用 n=拒绝 ya=全部应用 na=全部拒绝]> "
            )
            if line is None:
                return "n"
            answer = line.strip().lower()
            if answer in ("y", "n", "ya", "na"):
                return answer
            print("（请输入 y / n / ya / na）")

    def ask_brief(self) -> str:
        line = self._read("请简单描述你的产品设想（可回车直接开始访谈）> ")
        return (line or "").strip()

    # ---------------------------------------------------------------- 会话驱动

    def _consume(self, stream):
        for _ in stream:
            pass

    def run(self) -> str:
        """驱动主循环：图中断点 ↔ 输入路由 ↔ 审核/润色循环。返回 "done" / "quit"。"""
        while True:
            snap = self.graph.get_state(self.config)
            state = snap.values
            pending = [t.interrupts[0] for t in snap.tasks if t.interrupts]
            if pending:
                resp = self.handle_interrupt(state, pending[0].value)
                if resp is None:
                    return "quit"
                try:
                    self._consume(self.graph.stream(Command(resume=resp), self.config))
                except KeyboardInterrupt:
                    if self._read("（再按一次 Ctrl+C 强制退出，任意键继续）> ") is None:
                        return "quit"
                continue
            phase = state.get("phase", "interview")
            if phase in ("review", "render"):
                if state.get("final_prd"):
                    self.show_final(state)
                    return "done"
                outcome = run_pipeline(self.graph, self.cfg, self.llm, self)
                if outcome == "done":
                    st = self.graph.get_state(self.config).values
                    self.show_final(st)
                    return "done"
                continue
            # 访谈阶段：启动 / 回灌重入（空输入重跑：已完成的图不会因 None 重入）
            try:
                self._consume(self.graph.stream({}, self.config))
            except KeyboardInterrupt:
                if self._read("（再按一次 Ctrl+C 强制退出，任意键继续）> ") is None:
                    return "quit"

    def show_final(self, state: dict):
        final = state.get("final_prd")
        if not final:
            return
        print("\n" + "=" * 60)
        print("最终 PRD 已生成（prd_output.md）：")
        print("=" * 60)
        print(final)
