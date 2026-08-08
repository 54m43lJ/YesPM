"""端到端全流程测试（MockLLM + 注入输入，无需 API Key）。"""
import contextlib

from langgraph.checkpoint.sqlite import SqliteSaver

from src.graph import build_graph
from src.repl import REPL
from src.state import default_state
from src.tools.llm import MockLLM, build_llm
from src.tools.value_tree import instantiate_value_tree

# 默认模板的访谈单元数：field:1.2, chapter:1, field:2.1..2.4, field:3.1,
# chapter:3.2, field:3.3, field:4.1, field:4.2, field:4.3, field:5 = 13
N_UNITS = 13


@contextlib.contextmanager
def make_session(cfg, tmp_path, template, inputs, behavior=None):
    llm = build_llm(cfg)
    assert isinstance(llm, MockLLM)
    if behavior:
        llm.behavior.update(behavior)
    with SqliteSaver.from_conn_string(str(tmp_path / "s.sqlite")) as saver:
        graph = build_graph(cfg, llm, saver)
        repl = REPL(graph, "test-session", cfg, llm, inputs=iter(inputs))
        yield repl, graph, llm


def init_state(graph, config, template, brief="做一个记账软件"):
    tree = instantiate_value_tree(template)
    graph.update_state(config, default_state(template, brief) | {"prd_draft": tree, "brief": brief})


def test_full_flow_done(cfg, template, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with make_session(cfg, tmp_path, template, ["好的"] * (N_UNITS + 2)) as (repl, graph, llm):
        init_state(graph, repl.config, template)
        outcome = repl.run()
        assert outcome == "done"

        st = graph.get_state(repl.config).values
        assert st["phase"] == "render"
        assert st["final_prd"]
        final = st["final_prd"]
        assert "mock:产品名称" in final          # 章节转录
        assert "mock:产品代号" in final          # 字段转录
        assert "mock:功能名称" in final          # repeat 实例（children_template）
        assert "## 转换日志" in final
        assert (tmp_path / "prd_output.md").exists()


def test_skip_command(cfg, template, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with make_session(cfg, tmp_path, template, ["/skip"] + ["好的"] * (N_UNITS + 2)) as (repl, graph, _):
        init_state(graph, repl.config, template)
        assert repl.run() == "done"
        st = graph.get_state(repl.config).values
        assert "mock:产品代号" in st["final_prd"]  # /skip 强制转录仍产生值


def test_finish_command_ends_interview(cfg, template, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with make_session(cfg, tmp_path, template, ["好的", "好的", "/finish"]) as (repl, graph, _):
        init_state(graph, repl.config, template)
        assert repl.run() == "done"
        st = graph.get_state(repl.config).values
        assert len(st["units_done"]) == 2          # /finish 截断访谈
        assert st["doc_review_iterations"] == 0
        assert st["final_prd"]


def test_undo_retranscribes_unit(cfg, template, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inputs = ["好的", "/undo", "好的"] + ["好的"] * (N_UNITS + 2)
    with make_session(cfg, tmp_path, template, inputs) as (repl, graph, _):
        init_state(graph, repl.config, template)
        assert repl.run() == "done"
        st = graph.get_state(repl.config).values
        assert st["units_done"].count("field:1.2") == 1  # 回退后重新访谈，最终只完成一次
        assert "mock:产品代号" in st["final_prd"]


def test_quit_and_resume(cfg, template, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with make_session(cfg, tmp_path, template, ["好的", "好的"]) as (repl, graph, _):
        init_state(graph, repl.config, template)
        assert repl.run() == "quit"          # 输入耗尽 → 退出（checkpoint 保留）

    with make_session(cfg, tmp_path, template, ["好的"] * (N_UNITS + 4)) as (repl2, graph2, _):
        st = graph2.get_state(repl2.config).values
        assert st["phase"] == "interview"
        assert len(st["units_done"]) == 2
        assert repl2.run() == "done"
        st2 = graph2.get_state(repl2.config).values
        assert st2["phase"] == "render"
        assert "mock:产品名称" in st2["final_prd"]


def test_document_review_reentry(cfg, template, tmp_path, monkeypatch):
    """全文档审核不通过 → 缺口回灌访谈 → 重新审核通过。"""
    monkeypatch.chdir(tmp_path)
    behavior = {
        "document_review": [
            {"passed": False, "gaps": [{"path": "2.1", "dimension": "完整性", "reason": "一句话定位缺失"}]},
            {"passed": True, "gaps": []},
        ]
    }
    with make_session(cfg, tmp_path, template, ["好的"] * (N_UNITS + 3), behavior) as (repl, graph, _):
        init_state(graph, repl.config, template)
        assert repl.run() == "done"
        st = graph.get_state(repl.config).values
        assert st["doc_review_iterations"] == 1
        assert st["phase"] == "render"
        assert "mock:一句话定位" in st["final_prd"]
        assert "审核未决清单" not in st["final_prd"]


def test_document_review_unresolved_gap(cfg, template, tmp_path, monkeypatch):
    """审核始终不通过 → 超限后附未决清单进入渲染。"""
    monkeypatch.chdir(tmp_path)
    behavior = {
        "document_review": [
            {"passed": False, "gaps": [{"path": "2.1", "dimension": "完整性", "reason": "一直缺失"}]}
        ]
        * 4,
    }
    with make_session(cfg, tmp_path, template, ["好的"] * (N_UNITS + 5), behavior) as (repl, graph, _):
        init_state(graph, repl.config, template)
        assert repl.run() == "done"
        st = graph.get_state(repl.config).values
        assert st["doc_review_iterations"] == 3
        assert "审核未决清单" in st["final_prd"]
        assert "2.1" in st["final_prd"].split("审核未决清单")[1]


def test_repl_commands(cfg, template, tmp_path, monkeypatch, capsys):
    """/status /view /help 在中断点可用。"""
    monkeypatch.chdir(tmp_path)
    with make_session(cfg, tmp_path, template, ["/help", "/status", "/view", "好的", "/quit"]) as (repl, graph, _):
        init_state(graph, repl.config, template)
        assert repl.run() == "quit"
        out = capsys.readouterr().out
        assert "可用命令" in out
        assert "取值树" in out
