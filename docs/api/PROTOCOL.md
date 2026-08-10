# YesPM 后端接口协议

> 本文档定义 Python 后端暴露的统一接口契约：JSON-RPC 2.0 语义层。传输绑定见 [STDIO.md](./STDIO.md)（本地子进程）与 [WEBSOCKET.md](./WEBSOCKET.md)（远端服务）。全部前端（形态与命名见 [frontends/](../frontends/)）通过**同一协议**与后端通信，差异仅在传输。

## 1. 设计原则

协议设计遵循 [ARCHITECTURE.md](../ARCHITECTURE.md) 设计原则（协议优先 / 事件驱动 / 会话导向 / 后端无头 / 命令结构化 / 消息模板化与状态码统一等），本文档是其落地的唯一契约来源，不复述原则论证。契约规则见各相应章节：异步命令 + 同步查询（§4）、事件模板与载荷通道（§5）、单会话串行（§8）、四位状态码（§11）。

## 2. 消息信封（JSON-RPC 2.0）

三种消息，均为一帧一个完整 JSON 对象：

**请求**（client → server，带 `id`）：

```json
{"jsonrpc": "2.0", "id": 1, "method": "input/send", "params": {"session_id": "s-abc", "text": "..."}}
```

**响应**（server → client，`id` 对应请求）：

```json
{"jsonrpc": "2.0", "id": 1, "result": {"accepted": true}}
{"jsonrpc": "2.0", "id": 1, "error": {"code": 4102, "message": "当前状态不接受输入", "data": {"stage": "finished"}}}
```

**通知**（server → client，无 `id`，即事件）：

```json
{"jsonrpc": "2.0", "method": "session/message", "params": {"session_id": "s-abc", "message_id": "m-1", "status_code": 2031, "delta": "请描述"}}
```

约定：

- 所有字段名 snake_case。
- `result` / `error` 二选一，其余遵循 JSON-RPC 2.0 规范。
- 请求到达顺序即处理顺序（单会话内串行，见 §8 并发语义）。

## 3. 传输与寻址

| 传输 | 说明 | 绑定文档 |
|------|------|---------|
| in-process（进程内直连引擎） | 进程内形态的前端（与引擎同进程直连） | —（无独立绑定文档） |
| stdio（JSON Lines） | 本地子进程形态的前端 | [STDIO.md](./STDIO.md) |
| WebSocket | 远端形态的前端 | [WEBSOCKET.md](./WEBSOCKET.md) |

跨传输消息内容完全一致：方法名、参数、事件名、错误码全局唯一。

## 4. 方法目录（client → server）

### 4.1 会话生命周期

#### `session/create`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `template` | string | 否 | 模板标识或路径；缺省用服务端默认模板 |
| `config` | object | 否 | 会话级覆盖（如 `{"model": "..."}`） |

| 结果 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话标识，后续所有消息复用 |
| `snapshot` | SessionSnapshot | 初始状态快照（定义见 §4.3 `query/status`） |

#### `session/resume`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | 已存在会话 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `snapshot` | SessionSnapshot | 恢复后快照 |

> 恢复结果**不携带取值树**——大载荷一律经专属查询拉取（见 §5.3 封闭性条款）。推荐实现：前端创建 / 恢复会话后主动 `query/tree` 获取初始取值树（或依赖后续 `tree/changed` 信号）。

恢复后若存在挂起的 interrupt，引擎会补发 `session/await_input`（阶段与等待上下文经 `session/status` 的 `stage` / `waiting` 字段携带）。

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

删除会话及 checkpoint。已删除会话再次 `resume` 报错误 4001。

#### `session/quit`

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

保存 checkpoint 后结束会话。随后推送 `session/status`（`fields` 含 `ended`）。

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

错误：会话不存在（4001）、既非等待也非计算中（4102）。

#### `command/skip`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |

语义：跳过当前访谈单元——跳过评审、强制转录（缺失处标「待补充」）、进入下一单元，**不结束整个流程**。仅访谈阶段且存在当前单元时可用，否则错误 4103。

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |

#### `command/finish`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |

语义：结束访谈阶段，进入全文档审核。与 `command/skip` 语义互斥：skip 只跳过当前单元，finish 才终止访谈阶段。仅访谈阶段可用，否则错误 4103。

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |

#### `command/undo`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |

语义：回退最近一次转录 / 转换。无可回退记录时错误 4103。

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |

#### `proposal/respond`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |
| `proposal_ids` | string[] | 是 | 待处理提案 id（来自 `session/status` 的 `waiting.proposal_ids`），一次可多条 |
| `action` | string | 是 | `apply`（确认执行）\| `reject`（拒绝） |

| 结果 | 类型 | 说明 |
|------|------|------|
| `accepted` | boolean | 是否接受 |

错误：提案不存在或状态已变（4404）。

### 4.3 查询（同步返回）

#### `query/status`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |
| `fields` | string[] | 否 | 需要返回的常规字段白名单（见 §5.2 字段表）；缺省返回全部常规字段。**大载荷字段（`tree` / `final_prd` / `gaps` / `conversion_log`）禁止在此列出**——大载荷一律走专属查询（`query/tree` / `query/prd` / `query/gaps` / `query/conversions`） |

| 结果 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | |
| `fields` | string[] | 本次返回的字段列表（与请求 `fields` 一致；缺省为全部常规字段） |
| 常规字段 | — | `stage`、`node`、`status`、`current_unit`、`units_done`、`waiting`、`document_review`、`iterations`、`template_title`、`ended`、`revision`、`created_at`、`updated_at`；字段定义以 [§5.2 字段表](#52-sessionstatus-字段表fields-白名单) 为唯一来源 |

#### `query/tree`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | |
| `path` | string | 否 | 取值树位置路径；缺省返回整树 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `tree` | ValueTreeNode | 取值树（与模板同构）；节点结构与取值语义见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) 取值树一节（唯一来源） |

#### `query/commands`

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `commands` | object[] | `[{command, description, available, usage?}]`，按当前阶段过滤（供 `/help` 与前端命令面板渲染） |

#### `query/template`

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `template` | string | 否 | 缺省用默认模板 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `template` | TemplateMeta | 顶层章节数组（`{title, tier?, children…}`，含各节点类型与 `field_type`），供前端渲染提纲/表单；完整契约见 [TEMPLATE_SPEC.md](../TEMPLATE_SPEC.md) |

#### `query/prd`

大载荷专属查询（与 `prd/changed` 成对）：引擎只经 `prd/changed` 通知变更，前端按需调用本方法拉取；`query/status` 不承载。

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `markdown` | string | 最终 PRD Markdown（确定性基线 + 已应用润色转换；转换日志与「审核未决清单」（如有）由引擎渲染时附于文末） |
| `unresolved_gaps` | object[] \| null | 审核未决清单（全文档审核超限时存在） |

#### `query/gaps`

大载荷专属查询（与 `gaps/changed` 成对）。审核结论的自然语言描述经 `session/message` 流式推送，结构化清单按需经本方法拉取。

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `gap_list` | object[] | 最近一次全文档审核的结构化缺口清单（`[{path, dimension, reason}]`；path 恒指字段路径，寻址与归并规则见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) 寻址与 gap 归并一节，唯一来源） |

#### `query/conversions`

大载荷专属查询（与 `conversions/changed` 成对）。

| 参数 | 类型 | 必填 |
|------|------|------|
| `session_id` | string | 是 |

| 结果 | 类型 | 说明 |
|------|------|------|
| `conversion_log` | object[] | 润色转换日志：`[{id, position, scenario, before, after, risk, reason, evaluation?}]`，含已应用与已丢弃记录，支持复核与回退 |

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

不提供 `config/set`；配置变更经配置文件后重启生效。

## 5. 事件目录（server → client 通知）

事件是唯一的异步回流通道。**事件收敛为 4 个通用模板 + 4 个大载荷变更信号**，模板之间信息零重合。前端必须处理与自身形态相关的事件；未处理的事件应忽略（不报错）。

### 5.1 通用模板

| 事件 | 参数（`params`） | 语义 |
|------|------------------|------|
| `log` | `{session_id?, status_code, message, data?}` | 系统日志与错误：等级由 `status_code` 千位推断（1xxx debug / 2xxx info / 3xxx warn / 4xxx error / 5xxx fatal，见 §11）；请求失败不在此列，走 JSON-RPC error 响应；stdio 绑定落 stderr（见 [STDIO.md](./STDIO.md)） |
| `session/status` | `{session_id, status_code: 2001, fields: string[], revision: number, …}` | **唯一会话状态通道**：状态变化即推送，只带**变化/相关字段**，`fields` 声明本消息包含的字段（白名单见 5.2）；创建 / 恢复后首条推全字段快照；会话结束时 `fields` 含 `ended`。大载荷不在此通道 |
| `session/message` | `{session_id, message_id, status_code: 2031\|2032, delta?, text?}` | **唯一文本流通道**：agent 回复流式推送；2031 = chunk（增量，顺序即文本顺序），2032 = complete（携带全量文本）；前端按 `message_id` 拼接（见 §6） |
| `session/await_input` | `{session_id, status_code: 2002}` | **唯一「可回复」信号**：纯信号——引擎挂起，前端可以发送进一步消息（`input/send` 或 `proposal/respond`）。处于什么阶段、等待何种输入完全由前端从 `session/status` 的 `stage` / `waiting` 字段推断，本消息不携带任何上下文 |

### 5.2 `session/status` 字段表（fields 白名单）

| 字段 | 类型 | 说明 |
|------|------|------|
| `stage` | string | `interview` \| `document_review` \| `polish` \| `finished` |
| `node` | string | 当前阶段节点（如 `interview_agent` / `document_review_agent` / `polish_agent`） |
| `status` | string | `running`（计算中）\| `waiting`（有挂起中断）\| `idle` |
| `current_unit` | object \| null | `{path, title}`，访谈阶段当前单元 |
| `units_done` | string[] | 已完成单元路径 |
| `waiting` | object | `{kind: "interview"\|"proposal"\|"none", proposal_ids?: string[]}`——等待种类与轻量元数据；`kind=proposal` 时 `proposal_ids` 供 `proposal/respond` 引用 |
| `document_review` | object \| null | `{passed: boolean, gap_count: number}`（结构化缺口清单不在此列，见 5.3） |
| `iterations` | object | `{document_review: number}`（全文档审核往返计数） |
| `template_title` | string | |
| `ended` | object \| null | `{reason: "quit"\|"finished"\|"error", detail?}` 会话已结束 |
| `revision` | number | 状态版本号（随每次推送单调递增，随 checkpoint 持久化） |
| `created_at` / `updated_at` | string | ISO8601 |

> **revision 与同步**：前端记录已见 `revision`，发现跳号（说明中间有状态事件未处理）应主动 `query/status` 对齐。事件经可靠传输送达（WebSocket / stdio 语义），本机制只用于检测前端处理缺口，**不需要逐事件 ack**——状态权威在引擎（checkpoint），`query/status` 幂等对齐即可。
>
> **大载荷禁入**：`tree` / `final_prd` / `gaps` / `conversion_log` 不允许出现在 `fields` 中（推送与查询均同），一律走 5.3 的成对通道。

### 5.3 大载荷变更信号（大载荷设计例外）

大载荷不进 `session/status` / `query/status`，每个载荷有**成对**的专用通道：变更信号（本表）+ 专属查询（§4.3）。信号为纯提示，载荷一律按需拉取，信号与载荷零重合。

| 事件 | 参数（`params`） | 语义 | 拉取 |
|------|------------------|------|------|
| `tree/changed` | `{session_id, path?}` | 取值树转录 / 覆盖完成（`path` 为变更位置，可选） | `query/tree` |
| `prd/changed` | `{session_id}` | 最终 PRD 已产出 / 变更 | `query/prd` |
| `gaps/changed` | `{session_id}` | 全文档审核缺口清单已产生（审核结论的自然语言描述经 `session/message` 流式推送） | `query/gaps` |
| `conversions/changed` | `{session_id}` | 润色转换日志更新 | `query/conversions` |

> 封闭性条款：任何新增大载荷字段必须**成对**提供（`xxx/changed` + `query/xxx`），不得塞入 `session/status` 或 `query/status`。

## 6. 流式输出语义

- agent 回复（访谈提问 / 答复、审核结论、润色提案描述等）一律流式推送：若干 `session/message`（2031 chunk）→ 一条 `session/message`（2032 complete）。
- 前端按 `message_id` 拼接 `delta`；2032 的 `text` 为全量文本（供复制/落盘）。
- 单会话同一时刻至多一条流式消息（会话串行执行，见 §8）。

## 7. 交互定义归属

命令语法（`/skip`、`/help`、`/paste` 等）、快捷键与界面表现均属于**前端**——后端不理解任何命令文本，只提供结构化方法与事件。各前端在 [frontends/](../frontends/) 维护各自的完整交互定义（命令集、命令 → 方法映射、中断/退出语义）；定义初期一致、后期允许因技术限制分叉。后端提供 `query/commands` 供前端查询当前阶段可用操作（结构化数据，非命令语法）。

## 8. 并发与排队语义

- **单会话串行**：一个会话内同一时刻只处理一个请求；引擎计算期间到达的 `input/send` 被接受并暂存（`queued: true`），其余请求返回 4005「会话忙」。
- **跨会话并行**：不同会话互不影响，可并发处理。
- **多前端竞争**：同一会话被多连接订阅时，事件广播到全部订阅连接；竞态下的输入先到先得。

## 9. 前端义务清单（摘要）

每个前端必须：接收并渲染全部相关事件、支持会话创建/恢复/退出、实现输入与命令入口。各前端详细义务见 [frontends/](../frontends/) 各文档。

## 10. 时序示例

### 10.1 新建会话 + 首轮访谈

```
client → session/create {}
server ← {result: {session_id, snapshot}}
server → session/status {status_code: 2001, fields: [全部常规字段], revision: 1, stage: "interview", …}
server → session/status {status_code: 2001, fields: ["node"], node: "interview_agent"}
server → session/message {status_code: 2031, delta: "请…"} × N
server → session/message {status_code: 2032, text: "请…"}
server → session/status {status_code: 2001, fields: ["current_unit", "waiting"], current_unit: {path: "3.2.1", title: "核心功能 1"}, waiting: {kind: "interview"}}
server → session/await_input {status_code: 2002}
client → input/send {text: "..."}
server ← {result: {accepted: true, queued: false}}
server → session/message 2031 × N → 2032（agent 答复）
server → tree/changed {path: "3.2.1"}
server → session/status {fields: ["current_unit"], current_unit: {path: "3.2.2", title: "核心功能 2"}}（下一单元）
server → session/await_input
```

### 10.2 润色确认

```
server → session/status {fields: ["stage", "node"], stage: "polish", node: "polish_agent"}
server → session/message 2031 × N → 2032（自然语言提案描述）
server → session/status {fields: ["waiting"], waiting: {kind: "proposal", proposal_ids: ["p1"]}}
server → session/await_input {status_code: 2002}
client → proposal/respond {proposal_ids: ["p1"], action: "apply"}
server ← {result: {accepted: true}}
server → conversions/changed
server → session/status {fields: ["stage"], stage: "finished"}
server → prd/changed
client → query/prd {session_id}
server ← {result: {markdown: "...", unresolved_gaps: null}}
server → session/status {fields: ["ended"], ended: {reason: "finished"}}
```

### 10.3 恢复会话

```
client → session/resume {session_id}
server ← {result: {snapshot}}
（推荐）client → query/tree → 拉取初始取值树
server → session/status {status_code: 2001, fields: [全部常规字段], revision: N, …}
（若挂起 interrupt）
server → session/status {fields: ["waiting"], waiting: {kind: "interview" | "proposal", …}}
server → session/await_input
```

## 11. 状态码（四位码体系）

全部状态码统一为四位码 `ABCD`，贯穿 `log` 事件、`session/message` 分型与 JSON-RPC error 响应，无第二套码表。

### 11.1 等级位（千位）

| 千位 | 等级 | 用途 |
|------|------|------|
| 1 | debug | 诊断日志（`log` 事件） |
| 2 | info | 正常业务事件与消息（事件通道、`session/message` 分型） |
| 3 | warn | 警告（重试中、接近上限、兜底回退） |
| 4 | error | 可恢复错误（请求被拒、业务错误） |
| 5 | fatal | 致命错误（引擎无法继续 / 启动阶段拒绝） |

### 11.2 业务域位（百位）

| 百位 | 域 | 说明 |
|------|-----|------|
| 0 | session | 会话生命周期 |
| 1 | stage | 阶段与节点 |
| 2 | tree | 取值树 |
| 3 | message | 文本消息流 |
| 4 | polish | 润色 / 提案 / 转换 |
| 5 | review | 全文档审核 / 缺口 |
| 6 | protocol | 协议与传输 |
| 7 | template | 模板 |
| 8 | provider | LLM 提供商 |
| 9 | engine | 引擎 / 持久化 |

### 11.3 码表

| code | 语义 |
|------|------|
| 2001 | info / session：状态变更（`session/status` 事件） |
| 2002 | info / session：等待输入（`session/await_input` 事件） |
| 2031 | info / message：消息流 chunk（`session/message` 增量） |
| 2032 | info / message：消息流 complete（`session/message` 全量） |
| 2220 | info / tree：`tree/changed` |
| 2420 | info / polish：`prd/changed` |
| 2422 | info / polish：`conversions/changed` |
| 2520 | info / review：`gaps/changed` |
| 3101 | warn / provider：LLM 调用失败，重试中 |
| 3201 | warn / tree：P2 推断失败，回退 `default` / 留「待补充」 |
| 3301 | warn / polish：保真评估不通过，提案修订中 |
| 3401 | warn / polish：修订超限，提案丢弃 |
| 3501 | warn / review：全文档审核接近最大轮数 |
| 4001 | error / session：会话不存在 |
| 4005 | error / session：会话忙（另一请求处理中；`input/send` 除外——它被暂存而非拒绝） |
| 4102 | error / stage：当前状态不接受输入（非等待、非计算中） |
| 4103 | error / stage：结构化操作（`command/skip` / `command/finish` / `command/undo`）在当前阶段不可用 |
| 4404 | error / polish：提案不存在或状态已变 |
| 4601 | error / protocol：JSON 解析错误 |
| 4602 | error / protocol：无效请求 |
| 4603 | error / protocol：方法不存在 |
| 4604 | error / protocol：参数无效 |
| 4803 | error / provider：LLM 调用失败（重试耗尽） |
| 4905 | error / engine：请求处理内部错误 |
| 5701 | fatal / template：模板不合法（加载时 Pydantic 校验失败，启动阶段即拒绝并报错定位，见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) 约束 4） |
| 5802 | fatal / provider：配置错误（缺 API Key 等） |
| 5901 | fatal / engine：引擎内部故障 |
| 5902 | fatal / engine：checkpoint 持久化失败 |

> debug 级（1xxx）码由实现按业务域扩展，如 1001（debug / session：会话操作追踪）。
