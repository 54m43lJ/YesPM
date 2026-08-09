"""全局配置：环境变量加载与默认值。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv 未安装时静默跳过
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    temperature: float
    unit_max_turns: int
    doc_review_max_iters: int
    polish_max_rounds: int
    mock: bool
    session_dir: Path
    db_path_override: Path | None
    template_path: Path | None

    @property
    def db_path(self) -> Path:
        return self.db_path_override or (self.session_dir / "yespm.sqlite")

    @property
    def sessions_json_path(self) -> Path:
        return self.session_dir / "sessions.json"

    @property
    def version(self) -> str:
        from . import __version__

        return __version__


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_config() -> Config:
    session_dir = Path(
        os.environ.get("YESPM_SESSION_DIR", Path.home() / ".yespm")
    ).expanduser()
    session_dir.mkdir(parents=True, exist_ok=True)

    template_path = os.environ.get("YESPM_TEMPLATE")
    db_override = os.environ.get("YESPM_DB_PATH")
    return Config(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=_float_env("YESPM_TEMPERATURE", 0.3),
        unit_max_turns=_int_env("YESPM_UNIT_MAX_TURNS", 5),
        doc_review_max_iters=_int_env("YESPM_DOC_REVIEW_MAX_ITERS", 3),
        polish_max_rounds=_int_env("YESPM_POLISH_MAX_ROUNDS", 2),
        mock=os.environ.get("YESPM_MOCK", "0") == "1",
        session_dir=session_dir,
        db_path_override=Path(db_override) if db_override else None,
        template_path=Path(template_path) if template_path else None,
    )
