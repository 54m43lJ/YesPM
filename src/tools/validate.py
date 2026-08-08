from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


def validate_value(vn: dict, value: Any) -> list[str]:
    """确定性校验器（唯一判官）：判定 value 是否符合字段格式要求。返回错误清单，空表示合法。"""
    errors: list[str] = []
    ft = vn.get("field_type", "text")

    if value in (None, "", [], {}):
        return ["取值为空"]

    if ft == "enum":
        allowed = vn.get("enum_values") or []
        if value not in allowed:
            errors.append(f"取值「{value}」不在枚举范围 {allowed} 内")
        return errors

    if ft == "table":
        if not isinstance(value, list):
            return ["table 字段值必须为「行对象列表」"]
        columns = vn.get("columns") or []
        col_defs = {
            c.get("title"): c.get("enum_values")
            for c in (vn.get("column_defs") or [])
            if isinstance(c, dict) and c.get("enum_values")
        }
        for i, row in enumerate(value, start=1):
            if not isinstance(row, dict):
                errors.append(f"第 {i} 行不是对象（应为列名→值的映射）")
                continue
            for c in columns:
                if c not in row or row[c] in (None, ""):
                    errors.append(f"第 {i} 行缺少列「{c}」")
                elif c in col_defs and row[c] not in col_defs[c]:
                    errors.append(f"第 {i} 行列「{c}」取值「{row[c]}」不在枚举范围 {col_defs[c]} 内")
        mi = vn.get("min_items")
        if mi and len(value) < mi:
            errors.append(f"至少需要 {mi} 行，现有 {len(value)} 行")
        return errors

    if ft == "text":
        if str(value).strip() == "":
            errors.append("text 字段值不能为空")
        return errors

    errors.append(f"未知 field_type：{ft}")
    return errors


class EnumRepair(BaseModel):
    value: str = Field(description="从枚举中选出的取值")


class TableRepair(BaseModel):
    rows: list[dict[str, Any]] = Field(description="行对象列表，每行键为列名")


_REPAIR_SCHEMAS: dict[str, type[BaseModel]] = {
    "enum": EnumRepair,
    "table": TableRepair,
}


def parse_raw(vn: dict, raw: str) -> Any:
    """确定性初解析：enum 匹配枚举项；table 解析竖线分隔文本；text 原样返回。"""
    raw = (raw or "").strip()
    ft = vn.get("field_type", "text")
    if ft == "enum":
        ev = vn.get("enum_values") or []
        if raw in ev:
            return raw
        for e in ev:
            if raw and e in raw:
                return e
        return raw
    if ft == "table":
        cols = vn.get("columns") or []
        rows: list = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("|"):
                line = line[1:]
            if line.endswith("|"):
                line = line[:-1]
            cells = [c.strip() for c in line.split("|")]
            if cells and all(set(c) <= set("-: ") for c in cells):
                continue
            row = {}
            for i, col in enumerate(cols):
                row[col] = cells[i] if i < len(cells) else ""
            rows.append(row)
        return rows
    return raw


def parse_and_repair(vn: dict, raw: str, llm=None, max_retries: int = 2):
    """解析 → validate_value 校验 → 有错回喂 LLM 按 per-type 模型修正 → 再校验。

    返回 (value, errors)：errors 为空表示通过校验。"""
    value = parse_raw(vn, raw)
    errors = validate_value(vn, value)
    attempt = 0
    while errors and llm is not None and attempt < max_retries:
        v = _repair_attempt(vn, raw, errors, llm)
        if v is None:
            break
        value = v
        errors = validate_value(vn, value)
        attempt += 1
    return value, errors


def repair_value(vn: dict, value: Any, llm=None, max_retries: int = 2):
    """从已结构化取值开始：确定性校验 → 有错回喂 LLM 修正 → 再校验。

    供章节抽取等「LLM 直接产出取值」的环节使用。返回 (value, errors)。"""
    errors = validate_value(vn, value)
    attempt = 0
    while errors and llm is not None and attempt < max_retries:
        v = _repair_attempt(vn, str(value), errors, llm)
        if v is None:
            break
        value = v
        errors = validate_value(vn, value)
        attempt += 1
    return value, errors


def _repair_attempt(vn: dict, raw: str, errors: list, llm):
    schema = _REPAIR_SCHEMAS.get(vn.get("field_type", "text"))
    if schema is None:
        return None
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from src.llm import get_llm, llm_structured

        target = {
            "text": "字符串",
            "enum": f"枚举之一：{vn.get('enum_values')}",
            "table": f"行对象列表，键为列名：{vn.get('columns')}",
        }.get(vn.get("field_type", "text"))
        res = llm_structured(
            llm or get_llm(),
            schema,
            [
                SystemMessage(content="你是结构化数据解析器，负责把用户的原始回答修正为指定格式。"),
                HumanMessage(content=(
                    f"字段「{vn.get('title')}」目标格式：{target}\n"
                    f"待修正内容：\n{raw}\n\n"
                    f"校验失败原因：\n" + "\n".join(f"- {e}" for e in errors) +
                    "\n请修正后重新输出。"
                )),
            ],
        )
        return res.value if isinstance(res, EnumRepair) else res.rows
    except Exception:
        return None
