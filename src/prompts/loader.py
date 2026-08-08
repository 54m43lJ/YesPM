"""prompt 加载器。"""
from __future__ import annotations

from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent
_CACHE: dict[str, str] = {}


def load_prompt(name: str) -> str:
    if name not in _CACHE:
        _CACHE[name] = (_PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")
    return _CACHE[name]
