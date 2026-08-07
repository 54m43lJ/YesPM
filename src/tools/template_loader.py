from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, model_validator

ROOT = Path(__file__).resolve().parent.parent.parent


class Column(BaseModel):
    title: str
    enum_values: Optional[list[str]] = None


class TemplateNode(BaseModel):
    model_config = {"extra": "ignore"}

    title: str
    tier: Optional[str] = None
    field_type: str = "text"
    enum_values: Optional[list[str]] = None
    columns: Optional[list[Union[str, Column]]] = None
    required: bool = False
    question: Optional[str] = None
    example: Optional[str] = None
    description: Optional[str] = None
    default: Any = None
    min_items: Optional[int] = None
    item_label: Optional[str] = None
    children: Optional[list["TemplateNode"]] = None

    @property
    def node_type(self) -> str:
        if self.item_label:
            return "repeat"
        if self.children:
            return "group"
        return "field"

    @model_validator(mode="after")
    def _check(self) -> "TemplateNode":
        if self.tier is not None and self.tier not in ("P0", "P1", "P2"):
            raise ValueError(f"tier 必须为 P0/P1/P2，得到 {self.tier}（节点：{self.title}）")
        if self.field_type not in ("text", "enum", "table"):
            raise ValueError(f"field_type 非法：{self.field_type}（节点：{self.title}）")
        nt = self.node_type
        if nt == "repeat" and not self.children:
            raise ValueError(f"repeat 节点必须含 children（节点：{self.title}）")
        if nt == "group" and not self.children:
            raise ValueError(f"group 节点必须含 children（节点：{self.title}）")
        if self.field_type == "enum" and not self.enum_values:
            raise ValueError(f"enum 字段必须提供 enum_values（节点：{self.title}）")
        if self.field_type == "table" and not self.columns:
            raise ValueError(f"table 字段必须提供 columns（节点：{self.title}）")
        if nt != "field" and self.field_type != "text":
            raise ValueError(f"非叶子节点不应设置 field_type（节点：{self.title}）")
        return self


TemplateNode.model_rebuild()


def normalize_columns(columns) -> list[str]:
    out: list[str] = []
    for c in columns or []:
        if isinstance(c, str):
            out.append(c)
        else:
            out.append(c.title)
    return out


def _to_template_dict(tn: TemplateNode) -> dict:
    d = tn.model_dump()
    d["node_type"] = tn.node_type
    d["columns"] = normalize_columns(tn.columns)
    if tn.children:
        d["children"] = [_to_template_dict(c) for c in tn.children]
    return d


def load_template(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        raise ValueError("模板根必须为节点列表（数组）")
    return [_to_template_dict(TemplateNode(**node)) for node in data]


def build_skeleton(tnodes: list[dict], parent_path: str = "", parent_tier: str = "P1") -> list[dict]:
    out: list[dict] = []
    for idx, tn in enumerate(tnodes, start=1):
        path = f"{parent_path}.{idx}" if parent_path else str(idx)
        eff = tn.get("tier") or parent_tier or "P1"
        if eff not in ("P0", "P1", "P2"):
            eff = "P1"
        nt = tn.get("node_type") or ("repeat" if tn.get("item_label") else ("group" if tn.get("children") else "field"))
        node: dict = {
            "title": tn["title"],
            "path": path,
            "node_type": nt,
            "tier": eff,
            "value": None,
            "filled": False,
            "field_type": tn.get("field_type", "text"),
            "enum_values": tn.get("enum_values"),
            "columns": tn.get("columns"),
            "required": tn.get("required", False),
            "question": tn.get("question"),
            "example": tn.get("example"),
            "description": tn.get("description"),
            "default": tn.get("default"),
            "children": [],
            "item_label": tn.get("item_label"),
            "count": 0,
            "count_known": False,
            "min_items": tn.get("min_items"),
            "instance_no": None,
            "template_children": tn.get("children"),
        }
        if nt == "group":
            node["children"] = build_skeleton(tn.get("children") or [], path, eff)
        out.append(node)
    return out


def expand_instances(rv: dict, count: int) -> None:
    tmpl = rv.get("template_children") or []
    old = rv.get("children") or []
    rv["count"] = count
    rv["count_known"] = True
    new: list[dict] = []
    for i in range(1, count + 1):
        ipath = f"{rv['path']}.{i}"
        if i <= len(old):
            children = old[i - 1].get("children") or []
        else:
            children = build_skeleton(tmpl, ipath, rv["tier"])
        new.append({
            "title": f"{rv.get('item_label') or '项'} {i}",
            "path": ipath,
            "node_type": "instance",
            "tier": rv["tier"],
            "instance_no": i,
            "children": children,
        })
    rv["children"] = new


def load_prompt(name: str) -> str:
    p = ROOT / "prompts" / name
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""
