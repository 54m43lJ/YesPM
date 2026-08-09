"""领域事件定义（PROTOCOL §5）。

4 个通用模板 + 4 个大载荷变更信号；引擎通过 emit(name, params) 发布，
传输适配层负责序列化为 JSON-RPC 通知。事件名与方法名全局唯一。
"""
from __future__ import annotations

from typing import Any

# 通用模板
LOG = "log"
SESSION_STATUS = "session/status"
SESSION_MESSAGE = "session/message"
SESSION_AWAIT_INPUT = "session/await_input"

# 大载荷变更信号（成对：信号 + 专属查询）
TREE_CHANGED = "tree/changed"
PRD_CHANGED = "prd/changed"
GAPS_CHANGED = "gaps/changed"
CONVERSIONS_CHANGED = "conversions/changed"

# session/status 常规字段白名单（PROTOCOL §5.2）；大载荷禁入
STATUS_FIELDS = (
    "stage",
    "node",
    "status",
    "current_unit",
    "units_done",
    "waiting",
    "document_review",
    "iterations",
    "template_title",
    "ended",
    "revision",
    "created_at",
    "updated_at",
)

LARGE_PAYLOAD_FIELDS = ("tree", "final_prd", "gaps", "conversion_log")


def sanitize_status_fields(fields: list[str]) -> list[str]:
    """过滤掉大载荷字段与未知字段（封闭性条款）。"""
    return [f for f in fields if f in STATUS_FIELDS]
