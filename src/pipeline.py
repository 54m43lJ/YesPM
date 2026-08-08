"""Node ②③ 图外循环：P2 兜底推断 → 全文档审核回灌 → 确定性渲染 → 生成器-评估器润色。

入口 `run_pipeline`：
- 返回 "reentry"：审核不通过已回灌访谈阶段（gap 驱动重访），等待主循环再次进入主图；
- 返回 "done"：最终 PRD 产出完毕（已写入 checkpoint 与 prd_output.md）。
"""
from __future__ import annotations

import copy

from .nodes.document_review import run_document_review
from .nodes.polish import (
    classify_risk,
    evaluate_fidelity,
    filter_proposals,
    generate_proposals,
    revise_proposal,
)
from .prompts.loader import load_prompt
from .tools.renderer import join_blocks, render_blocks
from .tools.value_tree import (
    apply_field_unit_value,
    apply_subtree,
    find_value_node,
    match_value_paths,
    replace_node,
    template_fields_with_tier,
    tree_to_text,
)

OUTPUT_FILE = "prd_output.md"


def _p2_targets(template: list[dict], value_tree: list[dict], gap_paths: list[str]) -> tuple[list[str], list[str]]:
    """P2 兜底目标：(空字段路径, 无实例的 P2 repeat 路径)。"""
    gap_set = set(gap_paths)
    field_paths: list[str] = []
    repeat_paths: list[str] = []
    for tpl, eff in template_fields_with_tier(template):
        if eff != "P2":
            continue
        if tpl["kind"] == "field":
            for p in match_value_paths(value_tree, tpl["path"]):
                node = find_value_node(value_tree, p)
                if node and (node.get("status") != "filled" or node.get("value") in (None, "")):
                    field_paths.append(p)
                if p in gap_set:
                    field_paths.append(p)
        elif tpl["kind"] == "repeat":
            node = find_value_node(value_tree, tpl["path"])
            if node is not None and not node.get("instances"):
                repeat_paths.append(tpl["path"])
            if tpl["path"] in gap_set:
                repeat_paths.append(tpl["path"])
    return list(dict.fromkeys(field_paths)), list(dict.fromkeys(repeat_paths))


def _run_p2_infer(
    llm, template: list[dict], value_tree: list[dict], brief: str,
    field_paths: list[str], repeat_paths: list[str],
) -> None:
    if not field_paths and not repeat_paths:
        return
    system = load_prompt("p2_infer")
    system = (
        system.replace("{paths}", ", ".join(field_paths))
        .replace("{repeat_paths}", ", ".join(repeat_paths))
        .replace("{brief}", brief)
        .replace("{TREE_JSON}", "见用户输入 TREE_JSON 块")
    )
    user = (
        "目标字段：\n" + "\n".join(f"- {p}" for p in field_paths) + "\n\n"
        + "目标 repeat：\n" + "\n".join(f"- {p}" for p in repeat_paths) + "\n\n"
        + f"用户初始简述：\n{brief}\n\n"
        + f"完整取值树：\n{tree_to_text(value_tree)}"
    )
    try:
        result = llm.chat_json(system, user) or {}
    except Exception:
        return
    if isinstance(result, dict):
        values = result.get("values")
        if isinstance(values, dict):
            for path, value in values.items():
                if path in field_paths:
                    apply_field_unit_value(value_tree, path, value)
        subtrees = result.get("subtree")
        if isinstance(subtrees, dict):
            for path, subtree in subtrees.items():
                if path in repeat_paths and isinstance(subtree, dict):
                    existing = find_value_node(value_tree, path)
                    if existing is not None:
                        new_node = apply_subtree(template, existing, subtree)
                        replace_node(value_tree, path, new_node)


def _polish_document(llm, cfg, repl, template: list[dict], value_tree: list[dict]) -> tuple[list[dict], str, list[dict], str]:
    """润色管线：基线渲染 → 提案 → 分级/确认 → 保真评估 → 应用 → 转换日志。

    返回 (blocks, final_doc, log, log_markdown)。
    """
    blocks = render_blocks(value_tree)
    baseline = join_blocks(blocks)
    log: list[dict] = []
    applied_guard = set()

    try:
        proposals = filter_proposals(generate_proposals(llm, baseline), template)
    except Exception as e:
        print(f"\n[润色] 提案生成失败：{e}")
        proposals = []

    batch_mode = ""
    for idx, p in enumerate(proposals, start=1):
        path = p["location"]
        target = None
        for b in blocks:
            if b["path"] == path and b["kind"] == "field":
                target = b
                break
        if target is None:
            continue
        guard_key = (path, p["target"][:40])
        if guard_key in applied_guard:
            continue

        risk = classify_risk(p)
        if risk == "high":
            if batch_mode == "ya":
                decision = "y"
            elif batch_mode == "na":
                decision = "n"
            else:
                decision = repl.confirm_proposal(p, idx, len(proposals))
                if decision in ("ya", "na"):
                    batch_mode = decision
                    decision = "y" if decision == "ya" else "n"
        else:
            decision = "y"

        if decision != "y":
            log.append({**p, "risk": risk, "status": "rejected", "evaluation": "用户拒绝"})
            continue

        # 保真评估（生成器-评估器迭代，最多 N 轮）
        proposal = p
        passed = False
        feedback = ""
        for _round in range(cfg.polish_max_rounds + 1):
            try:
                passed, feedback = evaluate_fidelity(llm, proposal, target.get("content") or "")
            except Exception as e:
                feedback = f"评估失败：{e}"
                break
            if passed:
                break
            if _round < cfg.polish_max_rounds:
                proposal = revise_proposal(llm, proposal, feedback)
            else:
                break
        if not passed:
            log.append({**proposal, "risk": risk, "status": "discarded", "evaluation": feedback or "保真评估不通过"})
            continue

        target["content"] = proposal["target"]
        applied_guard.add(guard_key)
        log.append(
            {
                **proposal,
                "risk": risk,
                "status": "applied",
                "evaluation": "通过",
            }
        )

    final_doc = join_blocks(blocks)
    log_lines = [
        "| # | 位置 | 场景 | 风险 | 状态 | 评估 |",
        "|---|------|------|------|------|------|",
    ]
    for i, e in enumerate(log, start=1):
        location = e.get("location") or "-"
        scenario = e.get("scenario") or "-"
        risk = e.get("risk") or "-"
        status = e.get("status") or "-"
        evaluation = (e.get("evaluation") or e.get("reason") or "-").replace("|", "\\|").replace("\n", " ")
        log_lines.append(f"| {i} | {location} | {scenario} | {risk} | {status} | {evaluation} |")
    log_markdown = "## 转换日志\n\n" + "\n".join(log_lines)
    if not log:
        log_markdown += "\n\n（无转换提案）"
    return blocks, final_doc, log, log_markdown


def run_pipeline(graph, cfg, llm, repl) -> str:
    """审核回灌 + 渲染润色驱动。返回 "reentry" / "done"。"""
    snap = graph.get_state(repl.config)
    state = snap.values
    template = state.get("template") or []
    value_tree = copy.deepcopy(state.get("prd_draft") or [])
    brief = state.get("brief") or ""
    gap_list = state.get("gap_list") or []

    # ---- 1. P2 兜底推断（无访谈单元字段）
    field_paths, repeat_paths = _p2_targets(template, value_tree, [g.get("path") for g in gap_list])
    if field_paths or repeat_paths:
        print(f"\n[收尾] 推断 P2 取值（字段 {len(field_paths)} 个，repeat {len(repeat_paths)} 个）：")
        _run_p2_infer(llm, template, value_tree, brief, field_paths, repeat_paths)
        graph.update_state(repl.config, {"prd_draft": value_tree})

    # ---- 2. 全文档审核回灌循环
    iters = state.get("doc_review_iterations") or 0
    unresolved: list[dict] = []
    while True:
        passed, gaps = run_document_review(llm, template, value_tree, brief)
        print(f"\n[审核] 第 {iters + 1} 次全文档审核：{'通过' if passed else f'发现 {len(gaps)} 个缺口'}")
        if passed:
            break
        if iters >= cfg.doc_review_max_iters:
            unresolved = gaps
            print(f"[审核] 超过最大往返次数（{cfg.doc_review_max_iters}），进入渲染并附未决清单")
            break
        iters += 1
        print("[审核] 缺口已回灌访谈阶段，重新访谈相关单元……")
        graph.update_state(
            repl.config,
            {
                "gap_list": gaps,
                "doc_review_iterations": iters,
                "phase": "interview",
                "units_queue": [],
                "current_unit": None,
                "unit_conversation": [],
            },
        )
        return "reentry"

    # ---- 3. 渲染与润色
    blocks, final_doc, conversion_log, log_markdown = _polish_document(
        llm, cfg, repl, template, value_tree
    )
    sections = [final_doc, "", log_markdown]
    if unresolved:
        lines = ["## 审核未决清单", ""]
        for g in unresolved:
            lines.append(f"- `{g.get('path')}` [{g.get('dimension')}] {g.get('reason')}")
        sections.append("\n".join(lines))
    final_prd = "\n\n---\n\n".join(sections) + "\n"

    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(final_prd)
    except OSError as e:
        print(f"[渲染] 写入 {OUTPUT_FILE} 失败：{e}")

    graph.update_state(
        repl.config,
        {
            "phase": "render",
            "prd_draft": value_tree,
            "final_prd": final_prd,
            "conversion_log": conversion_log,
            "unresolved_gaps": unresolved,
        },
    )
    return "done"
