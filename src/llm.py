from __future__ import annotations

import os
from functools import lru_cache
from typing import Sequence, Type

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    base_url = os.getenv("OPENAI_BASE_URL")
    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=base_url if base_url else None,
        temperature=float(os.getenv("TEMPERATURE", "0.3")),
    )


def llm_structured(model, schema: Type[BaseModel], messages: Sequence[BaseMessage], retries: int = 2):
    """结构化输出兜底封装：解析抛 OutputParserException / ValidationError 时，
    将错误消息拼回 messages 重试，为不严格执行 schema 的兼容端点提供双保险。"""
    structured = model.with_structured_output(schema)
    current = list(messages)
    for attempt in range(retries + 1):
        try:
            return structured.invoke(current)
        except (OutputParserException, ValidationError) as e:
            if attempt >= retries:
                raise
            current = current + [
                HumanMessage(content=f"上一轮输出未能通过解析：{e}\n请严格按要求的 JSON 结构重新输出。")
            ]
    raise RuntimeError("unreachable")  # pragma: no cover
