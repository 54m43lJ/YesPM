"""LLM 封装：OpenAI 兼容接口 + 流式输出 + JSON 解析修复 + 确定性 mock 模式。

所有 agent 通过本模块调用模型；`YESPM_MOCK=1` 时使用 mock 实现（无需 API Key，
供测试与演示，行为确定可复现）。

接口约定（agent 节点统一使用）：
- stream(system, user, on_chunk)  流式输出，每个 delta 回调 on_chunk
- chat(system, user)               一次性文本返回
- chat_json(system, user)          一次性返回已解析的 JSON 对象
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterator

from ..config import Config


class LLMError(Exception):
    pass


class JSONParseError(LLMError):
    pass


def extract_json(text: str) -> Any:
    """从模型输出中提取并解析 JSON（容忍代码围栏、前后缀杂音）。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise JSONParseError(f"无法从模型输出中解析 JSON：\n{text[:500]}")


class RealLLM:
    """OpenAI 兼容客户端（DeepSeek / OpenAI / 本地网关）。"""

    def __init__(self, config: Config):
        self.config = config
        if not config.api_key:
            raise LLMError(
                "未配置 OPENAI_API_KEY。请在 .env 中配置 API Key，"
                "或设置 YESPM_MOCK=1 使用内置 mock 模型（测试/演示）。"
            )
        from openai import OpenAI

        self.client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    def _messages(self, system: str, user: str) -> list[dict]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def stream(self, system: str, user: str, on_chunk: Callable[[str], None] | None = None) -> str:
        stream = self.client.chat.completions.create(
            model=self.config.model,
            messages=self._messages(system, user),
            temperature=self.config.temperature,
            stream=True,
        )
        text = ""
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                text += delta
                if on_chunk:
                    on_chunk(delta)
        return text

    def chat(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=self._messages(system, user),
            temperature=self.config.temperature,
        )
        return response.choices[0].message.content or ""

    def chat_json(self, system: str, user: str) -> Any:
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=self._messages(system, user),
                temperature=self.config.temperature,
                response_format={"type": "json_object"},
            )
            text = response.choices[0].message.content or ""
        except Exception:
            text = self.chat(system, user)
        return extract_json(text)


class MockLLM:
    """确定性 mock：按系统提示中的 ROLE 标记分发固定行为。

    用于全流程自动化测试与无 Key 演示：
    - interview：单轮问答即声明覆盖；
    - unit_review：成熟；
    - transcribe：按提示中内嵌的树结构 JSON 填确定性值；
    - document_review / polish / fidelity / p2_infer：直接通过。

    测试可通过 `behavior` 注入脚本化行为（每角色一个 dict 列表，按调用次序弹出）。
    """

    def __init__(self, config: Config):
        self.config = config
        self.calls: list[tuple[str, str]] = []
        self.behavior: dict[str, list[dict]] = {}

    def _role(self, system: str) -> str:
        m = re.search(r"# ROLE:\s*(\w+)", system)
        return m.group(1) if m else ""

    def _behavior(self, role: str) -> dict | None:
        queue = self.behavior.get(role)
        if queue:
            return queue.pop(0)
        return None

    def stream(self, system: str, user: str, on_chunk: Callable[[str], None] | None = None) -> str:
        self.calls.append((self._role(system), system + user))
        role = self._role(system)
        if role == "interview":
            m = re.search(r"单元：[^\n]*", system)
            label = m.group(0) if m else "当前单元"
            text = f"{label} 请补充相关信息（mock）。\n[COVERED]"
        else:
            text = self.chat(system, user)
        if on_chunk:
            for i in range(0, len(text), 8):
                on_chunk(text[i : i + 8])
        return text

    def chat(self, system: str, user: str) -> str:
        role = self._role(system)
        if role == "interview":
            m = re.search(r"单元：[^\n]*", system)
            label = m.group(0) if m else "当前单元"
            return f"{label} 请补充相关信息（mock）。\n[COVERED]"
        if role == "unit_review":
            return '{"mature": true, "gaps": []}'
        if role == "transcribe":
            return self._mock_transcribe(system)
        if role == "document_review":
            return '{"passed": true, "gaps": []}'
        if role == "polish_generator":
            return '{"proposals": []}'
        if role == "fidelity":
            return '{"pass": true, "feedback": ""}'
        if role == "p2_infer":
            paths = re.findall(r"P2FIELDS:\[(.*?)\]", system, re.S)
            if not paths:
                return '{"values": {}}'
            fields = [p.strip() for p in paths[0].split(",") if p.strip()]
            values = {p: f"mock:{p}" for p in fields}
            return json.dumps({"values": values}, ensure_ascii=False)
        return '{"ok": true}'

    def chat_json(self, system: str, user: str) -> Any:
        role = self._role(system)
        injected = self._behavior(role)
        if injected is not None:
            return injected
        return extract_json(self.chat(system, user))

    @staticmethod
    def _field_value(node: dict) -> Any:
        if node.get("field_type") == "enum":
            ev = (node.get("enum_values") or [])[:1]
            return ev[0] if ev else None
        if node.get("field_type") == "table":
            cols = [c["title"] for c in (node.get("columns") or [])]
            return [{c: f"mock:{c}" for c in cols}] if cols else None
        return f"mock:{node.get('title', '')}"

    def _mock_transcribe(self, system: str) -> str:
        """从提示中提取 TREE_JSON 块，把所有 field 填上确定性 mock 值。"""
        m = re.search(r"TREE_JSON\n```(?:json)?\s*\n(.*?)\n```", system, re.S)
        if not m:
            return '{"subtree": {}}'
        try:
            tree = json.loads(m.group(1))
        except json.JSONDecodeError:
            return '{"subtree": {}}'
        # 字段单元：TREE_JSON 为 field 节点列表 → 输出 {"fields": {path: value}}
        if isinstance(tree, list) and tree and all(
            isinstance(n, dict) and n.get("kind") == "field" for n in tree
        ):
            fields = {n["path"]: self._field_value(n) for n in tree}
            return json.dumps({"fields": fields}, ensure_ascii=False)

        def fill(node: Any) -> Any:
            if not isinstance(node, dict):
                return node
            if node.get("kind") == "field":
                return {"title": node["title"], "value": MockLLM._field_value(node)}
            if node.get("kind") == "repeat":
                tpl = node.get("children_template") or []
                instances = [{"children": [fill(c) for c in tpl]}] if tpl else []
                return {"title": node["title"], "instances": instances}
            if node.get("kind") == "instance":
                return {"title": node["title"], "children": [fill(c) for c in (node.get("children") or [])]}
            children = node.get("children") or []
            if isinstance(children, dict):
                children = list(children.values())
            return {"title": node.get("title", ""), "children": [fill(c) for c in children]}

        filled = fill(tree)
        return json.dumps({"subtree": filled}, ensure_ascii=False)


def build_llm(config: Config) -> RealLLM | MockLLM:
    if config.mock:
        return MockLLM(config)
    return RealLLM(config)
