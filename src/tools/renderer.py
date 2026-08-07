from __future__ import annotations

from src.state.prd_state import is_empty


def _depth(path: str) -> int:
    return path.count(".") + 1


def _heading(path: str, title: str) -> str:
    level = min(_depth(path), 6)
    return f"{'#' * level} {path} {title}".rstrip()


def _render_table(columns: list, rows: list) -> str:
    columns = columns or []
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows or []:
        if isinstance(row, dict):
            lines.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
        else:
            lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def _render_field(vn: dict) -> list:
    out = [_heading(vn["path"], vn["title"])]
    if not is_empty(vn):
        value = vn["value"]
        if vn.get("field_type") == "table":
            out.append(_render_table(vn.get("columns"), value if isinstance(value, list) else []))
        elif vn.get("field_type") == "enum":
            out.append(f"**{value}**")
        else:
            out.append(str(value))
    elif vn.get("required"):
        out.append("> _（待补充）_")
    return [o for o in out if o]


def _walk(nodes: list, out: list) -> None:
    for vn in nodes:
        if vn["node_type"] == "field":
            out.extend(_render_field(vn))
            out.append("")
        else:
            out.append(_heading(vn["path"], vn["title"]))
            out.append("")
            _walk(vn.get("children") or [], out)


def render(tree: list) -> str:
    out: list[str] = []
    _walk(tree, out)
    return "\n".join(out).strip() + "\n"
