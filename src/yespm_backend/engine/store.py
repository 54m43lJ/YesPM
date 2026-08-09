"""会话元数据注册表（session 清单）。

设计原则（backend/ARCHITECTURE §3）：单一状态源——状态本体在 LangGraph checkpoint，
本表只维护会话清单（创建时间、模板、状态摘要），不复制状态。stage/status 仅为提示，
权威值以 query/status（读 checkpoint）为准。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SessionStore:
    """JSON 文件持久化的会话清单。线程安全（进程内多连接订阅场景）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}
        else:
            self._cache = {}

    def _persist(self) -> None:
        self.path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def register(self, session_id: str, template_title: str, stage: str = "interview") -> None:
        with self._lock:
            self._cache[session_id] = {
                "session_id": session_id,
                "template_title": template_title,
                "status": "active",
                "stage": stage,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._persist()

    def touch(self, session_id: str, stage: str | None = None, status: str | None = None) -> None:
        with self._lock:
            entry = self._cache.get(session_id)
            if entry is None:
                return
            entry["updated_at"] = _now()
            if stage is not None:
                entry["stage"] = stage
            if status is not None:
                entry["status"] = status
            self._persist()

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._cache.get(session_id)
            return dict(entry) if entry else None

    def exists(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._cache

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._cache:
                return False
            del self._cache[session_id]
            self._persist()
            return True

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = [dict(v) for v in self._cache.values()]
        entries.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
        return entries
