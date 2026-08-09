"""YesPM 后端：无头会话引擎 + 协议层 + 三前端入口。

模块层次（依赖单向：engine → graph/polish；protocol/entry → engine）：
  - config / state       配置与贯穿全流程的 LangGraph 状态通道
  - template             模板加载与校验（TEMPLATE_SPEC 的 Pydantic 实现）
  - tree                 取值树（与模板同构的中间态）+ 访谈单元划分
  - render               确定性基线渲染器
  - llm                  LLM 封装（OpenAI 兼容 + 确定性 mock）
  - prompts              各 agent 提示词
  - graph                主图：访谈阶段单元循环（interview ↔ unit_review → transcribe）
  - polish               轻量图逻辑：全文档审核回灌 + 渲染润色（polish ↔ fidelity）
  - engine               无头会话引擎（API 方法分发 / 中断 / 输入暂存 / checkpoint）
  - protocol             协议层（JSON-RPC 2.0 + in-process/stdio/websocket 传输）
  - entry                进程入口（yespm / yespm-server / yespm-ws 三个壳）
"""

__version__ = "0.3.0"
