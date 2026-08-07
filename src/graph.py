from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

try:
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:  # pragma: no cover
    from langgraph.checkpoint.memory import MemorySaver as InMemorySaver  # type: ignore

from langgraph.types import Command

from src.nodes.draft import draft_prd
from src.nodes.finalize import finalize_prd
from src.nodes.review import review_prd
from src.state.prd_state import PRDState
from src.tools.template_loader import load_template

ROOT = Path(__file__).resolve().parent.parent
MAX_ITER = int(os.getenv("MAX_ITERATIONS", "3"))


def _after_draft(state: PRDState) -> str:
    return "review" if state.get("draft_done") else "draft"


def _after_review(state: PRDState) -> str:
    if state.get("review_passed"):
        return "finalize"
    if state.get("iteration_count", 0) >= MAX_ITER:
        return "finalize"
    return "draft"


def build_graph():
    g = StateGraph(PRDState)
    g.add_node("draft", draft_prd)
    g.add_node("review", review_prd)
    g.add_node("finalize", finalize_prd)
    g.add_edge(START, "draft")
    g.add_conditional_edges("draft", _after_draft, {"review": "review", "draft": "draft"})
    g.add_conditional_edges("review", _after_review, {"finalize": "finalize", "draft": "draft"})
    g.add_edge("finalize", END)
    return g.compile(checkpointer=InMemorySaver())


def _read_answer() -> str:
    print("  （输完后按回车，再输入一个空行结束）")
    lines: list = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            break
        lines.append(line)
    return "\n".join(lines)


def _extract_prompt(payload) -> str:
    if isinstance(payload, dict):
        return payload.get("prompt", str(payload))
    return str(payload)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass

    load_dotenv(ROOT / ".env")
    template = load_template(str(ROOT / "prompts" / "template_schema.yaml"))
    graph = build_graph()

    print("=" * 60)
    print("欢迎使用 YesPM —— AI 驱动的 PRD 文档生成器")
    print("=" * 60)
    brief = input("请输入产品初始简述（一句话描述你要做的产品）：\n> ").strip()

    init: PRDState = {
        "initial_brief": brief,
        "template": template,
        "prd_draft": None,
        "iteration_count": 0,
        "draft_done": False,
        "reask_mode": False,
        "pending_paths": [],
        "filled_paths": [],
    }
    config = {"configurable": {"thread_id": "yespm-1"}}

    result = graph.invoke(init, config)
    while True:
        interrupts = result.get("__interrupt__") or ()
        if not interrupts:
            break
        payload = interrupts[0].value
        print("\n" + "-" * 60)
        print(_extract_prompt(payload))
        print("-" * 60)
        ans = _read_answer()
        result = graph.invoke(Command(resume=ans), config)

    print("\n" + "=" * 60)
    print("PRD 生成完成")
    print("=" * 60)
    final = result.get("final_prd", "")
    print(final)

    out_path = ROOT / "prd_output.md"
    out_path.write_text(final, encoding="utf-8")
    print(f"\n已保存至：{out_path}")


if __name__ == "__main__":
    main()
