"""模板加载与校验：TEMPLATE_SPEC.md 的 Pydantic 实现。

节点类型由字段存在性推断（无显式 id / type）：
  - 含 `item_label`        → repeat（必含 title / item_label / children）
  - 含 `children`          → group（必含 title / children）
  - 二者皆无               → field（必含 title，不得含 children）

不合规模板在启动阶段即被拒绝并报错定位（错误码 5701）。
"""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

TIER_VALUES = ("P0", "P1", "P2")
FIELD_TYPES = ("text", "enum", "table")

_RAW_KEYS = {
    "title",
    "tier",
    "hint",
    "description",
    "default",
    "children",
    "item_label",
    "field_type",
    "enum_values",
    "columns",
    "required",
    "preserve",
}


class ColumnDef(BaseModel):
    """table 列定义：字符串（列名）或 {title, enum_values?}。"""

    title: str = Field(min_length=1)
    enum_values: list[str] | None = None


class TemplateNode(BaseModel):
    """模板树节点（归一化形态，同时承载推断出的 kind 与校验后的元数据）。"""

    title: str = Field(min_length=1)
    kind: Literal["group", "repeat", "field"]
    tier: Literal["P0", "P1", "P2"] | None = None
    hint: str | None = None
    description: str | None = None
    default: str | None = None
    children: list["TemplateNode"] | None = None
    item_label: str | None = None
    field_type: Literal["text", "enum", "table"] = "text"
    enum_values: list[str] | None = None
    columns: list[ColumnDef] | None = None
    required: bool = False
    preserve: bool = False
    path: str = ""  # 模板树位置路径（repeat 实例级用 X 占位，如 "3.2.X.1"）

    @model_validator(mode="before")
    @classmethod
    def _infer_and_validate(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            raise ValueError("节点必须是映射（YAML 对象）")

        unknown = set(raw) - _RAW_KEYS
        if unknown:
            raise ValueError(f"未知字段 {sorted(unknown)}（合法字段：{sorted(_RAW_KEYS)}）")

        title = raw.get("title")
        if not title or not isinstance(title, str) or not title.strip():
            raise ValueError("节点必须含非空 `title`")

        has_item_label = "item_label" in raw
        has_children = "children" in raw

        if has_item_label:
            kind = "repeat"
            if not has_children:
                raise ValueError(f"repeat 节点「{title}」必须含 `children`（单实例子模板）")
            if not isinstance(raw.get("item_label"), str) or not raw["item_label"].strip():
                raise ValueError(f"repeat 节点「{title}」的 `item_label` 必须为非空字符串")
            children = raw.get("children")
            if not isinstance(children, list) or not children:
                raise ValueError(f"repeat 节点「{title}」的 `children` 必须为非空列表")
        elif has_children:
            kind = "group"
            children = raw.get("children")
            if not isinstance(children, list) or not children:
                raise ValueError(f"group 节点「{title}」的 `children` 必须为非空列表")
        else:
            kind = "field"
            if raw.get("item_label") is not None:
                raise ValueError(f"field 节点「{title}」不得含 `item_label`")

        tier = raw.get("tier")
        if tier is not None and tier not in TIER_VALUES:
            raise ValueError(f"节点「{title}」的 `tier` 非法：{tier!r}（应为 {TIER_VALUES} 之一）")

        field_type = raw.get("field_type", "text")
        if field_type not in FIELD_TYPES:
            raise ValueError(f"节点「{title}」的 `field_type` 非法：{field_type!r}（应为 {FIELD_TYPES} 之一）")

        if field_type == "enum":
            values = raw.get("enum_values")
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                raise ValueError(f"enum 字段「{title}」必须提供非空 `enum_values` 字符串列表")
        if field_type == "table":
            columns = raw.get("columns")
            if not isinstance(columns, list) or not columns:
                raise ValueError(f"table 字段「{title}」必须提供非空 `columns`")
            normalized = []
            for idx, col in enumerate(columns):
                if isinstance(col, str) and col.strip():
                    normalized.append({"title": col})
                elif isinstance(col, dict) and isinstance(col.get("title"), str) and col["title"].strip():
                    cev = col.get("enum_values")
                    if cev is not None and (not isinstance(cev, list) or not all(isinstance(v, str) for v in cev)):
                        raise ValueError(f"table 字段「{title}」第 {idx + 1} 列的 enum_values 非法")
                    normalized.append({"title": col["title"], "enum_values": cev})
                else:
                    raise ValueError(
                        f"table 字段「{title}」第 {idx + 1} 列非法：应为字符串或 {{title, enum_values?}}"
                    )
            raw = dict(raw)
            raw["columns"] = normalized

        preserve = raw.get("preserve", False)
        if preserve and field_type != "text":
            raise ValueError(f"`preserve: true` 仅允许出现在 `field_type: text` 的字段上（「{title}」）")

        required = raw.get("required", False)
        if not isinstance(required, bool):
            raise ValueError(f"节点「{title}」的 `required` 必须为布尔值")

        return dict(raw, kind=kind, field_type=field_type, required=required, preserve=bool(preserve))

    @model_validator(mode="after")
    def _check_container_rules(self) -> "TemplateNode":
        # 容器节点（group/repeat）不得承载 field 专用元数据；field 不得含 children
        if self.kind == "field" and self.children is not None:
            raise ValueError(f"field 节点「{self.title}」不得含 `children`")
        return self


class TemplateValidationError(Exception):
    """模板校验失败，message 中带树中定位信息。"""


def load_template(path: Path | str) -> list[dict]:
    """加载并校验模板 YAML，返回归一化模板树（list[dict]，含推断的 kind 与 path）。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise TemplateValidationError(f"无法读取模板文件：{path}")

    try:
        raw_root = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise TemplateValidationError(f"模板 YAML 语法错误：{e}")

    if not isinstance(raw_root, list) or not raw_root:
        raise TemplateValidationError("模板根必须为非空节点列表（章节数组）")

    try:
        root = [TemplateNode.model_validate(n) for n in raw_root]
    except Exception as e:  # pydantic ValidationError 及自定义 ValueError 均在此捕获
        detail = str(e)
        if not detail.startswith("1 validation error"):
            detail = "\n".join(traceback.format_exception_only(type(e), e))
        raise TemplateValidationError(f"模板校验失败：\n{detail}")

    tree = [node.model_dump(exclude_none=False) for node in root]
    from ..tree.value_tree import compute_template_paths

    compute_template_paths(tree)
    return tree


def load_default_template() -> list[dict]:
    """加载内置默认 PRD 模板。"""
    return load_template(Path(__file__).resolve().parent / "default_prd.yaml")
