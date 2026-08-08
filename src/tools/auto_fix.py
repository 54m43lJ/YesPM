from __future__ import annotations


def auto_fix_hook(failed_fields: list, tree: list):
    """演进钩子（当前 no-op）：由 LLM 尝试自动修正失败字段。

    能修的应从 failed_fields 移除并直接 patch 进取值树；无法解决的保留供回问用户。
    返回 (剩余失败清单, 自动修复数量)。"""
    return failed_fields, 0
