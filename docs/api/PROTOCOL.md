# YesPM 后端接口协议（PROTOCOL / V1）

> 本文档定义 Python 后端暴露的统一接口契约：JSON-RPC 2.0 语义层。传输绑定见 [STDIO.md](./STDIO.md)（本地子进程）与 [WEBSOCKET.md](./WEBSOCKET.md)（远端服务）。三种前端——纯 CLI（Python）、TUI（TypeScript）、Web（JS/TS）——通过**同一协议**与后端通信，差异仅在传输。

## 1. 设计原则

1. **协议优先，传输可插拔**：全部语义（方法、事件、错误）与传输无关；传输层只负责信封的收发。新增传输绑定不改动任何语义。
2. **事件驱动**：后端是唯一的状态所有者。任何状态变化（阶段切换、转录、等待输入、提案产生）都以事件（通知）主动推送，前端不做状态推断、不轮询。
3. **会话导向**：一切交互围绕 `session_id`。一个前端进程/连接可承载多个会话。
4. **异步处理**：命令类请求（`input/send`、`command/*`、`proposal/respond`）只返回「已接受」；处理过程与结果全部经事件回流。查询类请求（`query/*`）同步返回。
5. **后端无头且解耦**：后端只理解本协议，不理解任何用户命令与交互形态。命令语法、快捷键、界面表现都是前端对 API 的**重新解释与包装**（各前端交互定义见 [frontends/](../frontends/)），后端不承载。
6. **自由文本只含数据，命令一律结构化**：API 不允许承载「命令执行」语义的自由文本。命令是结构化方法调用（`command/skip`、`command/finish`、`command/undo`、`proposal/respond`、`session/quit`）；`input/send` 的 `text` 是纯数据（访谈回复 / 补充信息），引擎原样转发给当前活跃节点，不做任何命令解析。
7. **单一契约**：CLI 复用同一协议（进程内 transport），不存在第二套接口。协议是全系统唯一通信面。

## 2. 消息信封（JSON-RPC 2.0）

三种消息，均为一帧一个完整 JSON 对象：

**请求**（client → server，带 `id`）：

```json
{"jsonrpc": "2.0", "id": 1, "method": "input/send", "params": {"session_id": "s-abc", "text": "..."}}
```

**响应**（server → client，`id` 对应请求）：

```json
{"jsonrpc": "2.0", "id": 1, "result": {"accepted": true}}
{"jsonrpc": "2.0", "id": 1, "error": {"code": 1002, "message": "当前状态不接受输入", "data": {"stage": "finished"}}}
```

**通知**（server → client，无 `id`，即事件）：

```json
{"jsonrpc": "2.0", "method": "message/chunk", "params": {"session_id": "s-abc", "message_id": "m-1", "delta": "请描述"}}
```

约定：

- 所有字段名 snake_case。
- `result` / `error` 二选一，其余遵循 JSON-RPC 2.0 规范。
- 请求到达顺序即处理顺序（单会话内串行，见 §8 并发语义）。

## 3. 传输与寻址

| 传输 | 用途 | 绑定文档 |
|------|------|---------|
| in-process（进程内直连引擎） | 纯 CLI（Python） | [frontends/CLI.md](../frontends/CLI.md) |
| stdio（JSON Lines） | TUI（TypeScript 子进程） | [STDIO.md](./STDIO.md) |
| WebSocket | Web（JS/TS） | [WEBSOCKET.md](./WEBSOCKET.md) |

跨传输消息内容完全一致：方法名、参数、事件名、错误码全局唯一。

## 4. 方法目录（client → server）

### 4.1 会话生命周期

#### `session/create`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `template` | string | 否 | 模板标识或路径；缺省用服务端默认模板 |
| `config` | object | 否 | 会话级覆盖（如 `{"model": "..."}`）；v1 可选 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话标识，后续所有消息复用 |
| `snapshot` | SessionSnapshot | 初始状态快照（定义见 §4.4 `query/status`） |

#### `session/resume`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | 已存在会话 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `snapshot` | SessionSnapshot | 恢复后快照 |
| `tree` | ValueTreeNode | 完整取值树快照（前端可直接渲染，亦可后续用 `query/tree` 增量获取） |

恢复后若存在挂起的 interrupt，引擎会补发 `input/required` / `proposal/pending`。

#### `session/list`

无参数。

| 结果 | 类型 | 说明 |
|------|------|------|
| `sessions` | SessionMeta[] | 按 `updated_at` 倒序 |

SessionMeta：`{session_id, template_title, status, stage, created_at, updated_at}`

#### `session/delete`

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

删除会话及 checkpoint。已删除会话再次 `resume` 报错误 1001。

#### `session/quit`

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

保存 checkpoint 后结束会话。随后推送事件 `session/ended {reason: "quit"}`。

### 4.2 交互

#### `input/send`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |
| `text` | string | 是 | 自由文本（访谈回复 / 补充信息），原样转发给当前活跃节点 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |
| `queued` | boolean | `true` 表示引擎计算中，输入已暂存，将在下一个中断点回放（「非等待期输入不丢弃」引擎机制，见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) §3） |

错误：会话不存在（1001）、既非等待也非计算中（1002）。

#### `command/skip`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |

语义：跳过当前访谈单元——跳过评审、强制转录（缺失处标「待补充」）、进入下一单元，**不结束整个流程**。仅访谈阶段且存在当前单元时可用，否则错误 1003。

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |

#### `command/finish`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |

语义：结束访谈阶段，进入全文档审核。与 `command/skip` 语义互斥：skip 只跳过当前单元，finish 才终止访谈阶段。仅访谈阶段可用，否则错误 1003。

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |

#### `command/undo`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |

语义：回退最近一次转录 / 转换。无可回退记录时错误 1003。

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |

#### `proposal/respond`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |
| `proposal_ids` | string[] | 是 | 待处理提案 id（来自 `proposal/pending`），一次可多条 |
| `action` | string | 是 | `apply`（确认执行）\| `reject`（拒绝） |

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |

错误：提案不存在或状态已变（1004）。

### 4.3 查询（同步返回）

#### `query/status`

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | |
| `stage` | string | `interview` \| `document_review` \| `polish` \| `finished` |
| `status` | string | `running`（计算中）\| `waiting`（有挂起中断）\| `idle` |
| `current_unit` | object \| null | `{path, title}`，访谈阶段当前单元 |
| `units_done` | string[] | 已完成单元路径 |
| `waiting` | object | `{kind: "interview" \| "proposal" \| "none", pending_proposals?: [...]}` |
| `document_review` | object \| null | `{passed: boolean, gap_count: number}` |
| `iterations` | object | `{document_review: number}`（全文档审核往返计数） |
| `template_title` | string | |
| `created_at` / `updated_at` | string | ISO8601 |

#### `query/tree`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |
| `path` | string | 否 | 取值树位置路径；缺省返回整树 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `tree` | ValueTreeNode | 取值树（与模板同构，定义见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) 取值树一节） |

ValueTreeNode：`{path, title, node_type: "group"|"repeat"|"field", field_type?: "text"|"enum"|"table", value?, children?, instances?}`

- `group` → `children`；`repeat` → `instances[]`（每实例为子树）；`field` → `value`（`enum` 为所选项，`table` 为行对象列表，`text` 为字符串）。

#### `query/commands`

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `commands` | object[] | `[{command, description, available, usage?}]`，按当前阶段过滤（供 `/help` 与 TUI 命令面板渲染） |

#### `query/template`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `template` | string | 否 | 缺省用默认模板 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `template` | TemplateMeta | 顶层章节数组（`{title, tier?, children…}`，含各节点类型与 `field_type`），供前端渲染提纲/表单；完整契约见 [TEMPLATE_SPEC.md](../TEMPLATE_SPEC.md) |

### 4.4 配置

#### `config/get`

无参数。

| 结果 | 类型 | 说明 |
|------|------|------|
| `provider` | string | LLM 提供商 |
| `model` | string | 模型名 |
| `template` | string | 当前模板 |
| `db_path` | string | 持久化路径 |
| `version` | string | 后端版本 |

v1 不提供 `config/set`；配置变更经配置文件后重启生效。

## 5. 事件目录（server → client 通知）

事件是唯一的异步回流通道。前端必须处理与自身形态相关的事件；未处理的事件应忽略（不报错）。

| 事件 | 参数（`params`） | 语义 |
|------|------------------|------|
| `session/ready` | `{session_id, snapshot}` | 创建 / 恢复会话完成 |
| `node/entered` | `{session_id, stage, node}` | 进入某阶段节点（`stage`: `interview` \| `document_review` \| `polish`） |
| `node/exited` | `{session_id, stage, node}` | 离开某阶段节点 |
| `status/changed` | `{session_id, snapshot}` | 当前单元 / 已完单元 / 阶段变化（快照同 `query/status`） |
| `tree/changed` | `{session_id, path?}` | 取值树转录 / 覆盖完成；前端如需最新树自行 `query/tree` |
| `message/chunk` | `{session_id, message_id, delta}` | agent 回复流式增量（顺序即文本顺序） |
| `message/complete` | `{session_id, message_id, text}` | 一条流式消息结束（携带全量文本） |
| `input/required` | `{session_id, kind: "interview"\|"proposal", context: {current_unit, prompt?}}` | 引擎挂起等待输入；`kind=proposal` 时详情由 `proposal/pending` 携带 |
| `proposal/pending` | `{session_id, proposals: [{id, position, original, target, scenario, risk: "high"\|"low", reason}]}` | 高风险转换待用户确认（低风险自动执行，经 `polish/progress` 通报） |
| `document/reviewed` | `{session_id, passed, gap_list: [{path, dimension, reason}]}` | 全文档审核结果 |
| `polish/progress` | `{session_id, applied: [{id, position, scenario}], rejected: [{id, position, reason}]}` | 润色转换应用 / 丢弃记录 |
| `prd/rendered` | `{session_id, markdown, conversion_log, unresolved_gaps?}` | 最终 PRD 产出；会话进入 `finished` 但未销毁，可继续查询 |
| `session/ended` | `{session_id, reason: "quit"\|"finished"\|"error", detail?}` | 会话结束 |
| `error` | `{session_id?, code, message, data?}` | 引擎内部错误推送（请求失败不在此列，走 JSON-RPC error 响应） |
| `log` | `{session_id?, level, message}` | 后端日志通道（stdio 绑定走 stderr，见 [STDIO.md](./STDIO.md)） |

## 6. 流式输出语义

- agent 回复（访谈提问 / 答复、审核结论、润色输出）一律流式推送：若干 `message/chunk` → 一条 `message/complete`。
- 前端按 `message_id` 拼接 `delta`；`message/complete` 的 `text` 为全量文本（供复制/落盘）。
- 单会话同一时刻至多一条流式消息（会话串行执行，见 §8）。

## 7. 交互定义归属

命令语法（`/skip`、`/help`、`/paste` 等）、快捷键与界面表现均属于**前端**——后端不理解任何命令文本，只提供结构化方法与事件。各前端的完整交互定义（命令集、命令 → 方法映射、中断/退出语义）见：

- [frontends/CLI.md](../frontends/CLI.md)
- [frontends/TUI.md](../frontends/TUI.md)
- [frontends/WEB.md](../frontends/WEB.md)

三份定义初期一致、后期允许因技术限制分叉。后端提供 `query/commands` 供前端查询当前阶段可用操作（结构化数据，非命令语法）。

## 8. 并发与排队语义

- **单会话串行**：一个会话内同一时刻只处理一个请求；引擎计算期间到达的 `input/send` 被接受并暂存（`queued: true`），其余请求返回 1005「会话忙」。
- **跨会话并行**：不同会话互不影响，可并发处理。
- **多前端竞争**：同一会话被多连接订阅时，事件广播到全部订阅连接；竞态下的输入先到先得（v1 简化）。

## 9. 前端义务清单（摘要）

每个前端必须：接收并渲染全部相关事件、支持会话创建/恢复/退出、实现输入与命令入口。各前端详细义务见 [frontends/CLI.md](../frontends/CLI.md)、[frontends/TUI.md](../frontends/TUI.md)、[frontends/WEB.md](../frontends/WEB.md)。

## 10. 时序示例

### 10.1 新建会话 + 首轮访谈

```
client → session/create {}
server ← {result: {session_id, snapshot}}
server → session/ready {snapshot}
server → node/entered {stage: "interview", node: "draft"}
server → message/chunk {delta: "请…"} × N
server → message/complete {text: "请…"}
server → input/required {kind: "interview", context: {current_unit: {path: "3.2.1", title: "核心功能 1"}}}
client → input/send {text: "..."}
server ← {result: {accepted: true, queued: false}}
server → message/chunk × N → message/complete（agent 答复）
server → tree/changed {path: "3.2.1"}
server → input/required（下一单元）
```

### 10.2 润色确认

```
server → node/entered {stage: "polish", node: "polish_agent"}
server → input/required {kind: "proposal", context: {current_unit: null}}
server → proposal/pending {proposals: [{id: "p1", scenario: "B1", risk: "high", ...}]}
client → proposal/respond {proposal_ids: ["p1"], action: "apply"}
server ← {result: {accepted: true}}
server → polish/progress {applied: [{id: "p1", ...}], rejected: []}
server → prd/rendered {markdown: "..."}
server → session/ended {reason: "finished"}
```

### 10.3 恢复会话

```
client → session/resume {session_id}
server ← {result: {snapshot, tree}}
server → session/ready
（若挂起 interrupt）
server → input/required / proposal/pending
```

## 11. 错误码

| code | 含义 |
|------|------|
| -32700 | JSON 解析错误 |
| -32600 | 无效请求 |
| -32601 | 方法不存在 |
| -32602 | 参数无效 |
| -32603 | 内部错误 |
| 1001 | 会话不存在 |
| 1002 | 当前状态不接受输入（非等待、非计算中） |
| 1003 | 结构化操作（`command/skip` / `command/finish` / `command/undo`）在当前阶段不可用 |
| 1004 | 提案不存在或状态已变 |
| 1005 | 会话忙（另一请求处理中；`input/send` 除外——它被暂存而非拒绝） |
| 2001 | 模板不合法（加载时 Pydantic 校验失败，启动阶段即拒绝并报错定位，见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) 约束 4） |
| 2002 | 配置错误（缺 API Key 等） |
| 3001 | LLM 提供商调用失败（重试耗尽） |
| 3002 | checkpoint 持久化失败 |
