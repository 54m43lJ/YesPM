from __future__ import annotations

import os
from functools import lru_cache

from langchain_openai import ChatOpenAI


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    base_url = os.getenv("OPENAI_BASE_URL")
    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=base_url if base_url else None,
        temperature=float(os.getenv("TEMPERATURE", "0.3")),
    )
