# YesPM 后端架构（Backend）

> 本文档描述后端**工作流程**（三阶段流程与 agent 职责）与**引擎实现**（会话机制、技术栈结论、模块与进程形态）。设计原则与选型理由见 [ARCHITECTURE_V2.md](../ARCHITECTURE_V2.md)；对外接口契约见 [api/PROTOCOL.md](../api/PROTOCOL.md)；交互定义（命令语法、快捷键等）在 [frontends/](../frontends/) 各前端文档中，后端不承载。

## 1. 定位与边界

后端是一个**无头（headless）会话引擎**：持有全部状态、流程控制与持久化，对外只暴露统一 API（[PROTOCOL.md](../api/PROTOCOL.md)）。后端**只理解 API**——不解析任何命令文本、不感知任何交互形态。

| 在后端（引擎） | 在前端（解释与包装） |
|----------------|---------------------|
| 会话状态机（阶段 / 等待 / 忙） | 事件渲染 |
| API 方法分发（方法 → 引擎操作） | 命令语法与快捷键 → 方法调用 |
| 中断管理与输入暂存 | 输入采集（多行、粘贴模式） |
| checkpoint 持久化 | 帮助文本、状态面板 |
| LangGraph 流程（三阶段） | 取值树 / 文档预览 |

## 2. 工作流程（三阶段）

### 总体流程

```
用户简述
   │
   ▼
① 访谈阶段（Interview Stage）          ← 自然对话访谈（多轮）
   interview_agent ↔ 用户（多轮对话）
   → unit_review_agent（单元成熟度评审）
   → transcribe_agent（对话 → 结构化取值树）
   │
   ▼
② 全文档审核阶段（Full Review）        ← document_review_agent（跨章节一致性 / 完整性 / 逻辑自洽）
   │
   ├─ 通过 ──────────────▶ ③ 渲染润色阶段（Render & Polish）
   │                        确定性渲染 → 润色（polish_agent ↔ fidelity_evaluation_agent）→ Markdown
   │
   └─ 不通过 → 缺口清单 ──▶ 回到 ①，交由访谈 agent 从头处理（复用单元循环）
                              （最多 N 轮，超限强制进入 ③ 并附未决清单）
```

**统一反馈机制**：缺口清单（gap list）是全流程唯一的反馈载体。单元成熟度评审与全文档审核都产出 `[{path, dimension, reason}]` 形式清单，回灌给访谈 agent，复用同一套单元循环。

### 取值树：贯穿全流程的中间态数据

`prd_draft` 是与模板同构的**取值树**（模板结构规范见 [TEMPLATE_SPEC.md](../TEMPLATE_SPEC.md)）：

- 遍历模板实例化：`field` 叶子持有转录值或空，`group` 仅作结构，`repeat` 展开为实例数组。
- interview_agent / unit_review_agent 的跨单元上下文、document_review_agent 的审核输入、渲染基线均基于此树。
- 取值树是全流程唯一中间态，取代任何字符串形式的草稿。

```
模板树（template）            取值树（prd_draft）
─────────────────            ─────────────────
group                        group
├─ field                     ├─ field → "已填值"
├─ repeat                    ├─ repeat
│  └─ children(单实例)        │  ├─ 实例1 (children 已实例化)
│                            │  └─ 实例2
└─ group                     └─ group
   └─ field                     └─ field → 空
```

### 2.1 访谈阶段（Interview Stage）

#### 目标

以自然访谈为信息采集方式：模板（契约见 [TEMPLATE_SPEC.md](../TEMPLATE_SPEC.md)）是访谈者的内部提纲与转录的目标结构，不直接作为用户可见的提问脚本。

#### Tier 语义（访谈颗粒度）

| tier | 语义 | 处理 |
|------|------|------|
| P0 | 最细，需专门提问 | **字段级访谈单元**：围绕该字段专项对话，直到成熟 |
| P1 | 章节整体访谈 | **章节级访谈单元**：整章一个自由会话 |
| P2 | 不访谈 | 无访谈单元，转录/收尾时由 LLM 依据上下文推断，推不出则留「待补充」 |

#### 单元级循环

每个访谈单元（P0 字段 / P1 章节）执行：

```
interview_agent ──自然对话访谈──▶ 用户
   （依据：模板单元描述 + 已转录取值树；无历史对话）
        ↕ 多轮 interrupt 往返
interview_agent 声明覆盖完毕 / 对话超过 N 轮（默认 5）/ 用户 skip 操作
        ↓
unit_review_agent ── 成熟度评审（本单元对话 + 取值树）──▶
   ├─ 不成熟 → 返回缺口清单 → interview_agent 针对缺口继续追问
   ├─ 用户 skip → 跳过评审，强制转录
   └─ 成熟  → transcribe_agent
                  ↓
       转录为本单元取值树子树 → 丢弃本单元对话 → 下一单元
```

#### 关键设计决策

1. **单元划分**：由 tier 决定。P0 字段 → 字段级单元；P1 章节 → 章节级单元；P2 → 无单元，转录/收尾时推断。
2. **评审触发点**：interview_agent 主动声明"本单元覆盖完毕"时触发评审；兜底为对话超过 N 轮（默认 5）未声明时强制评审一次，防止闲聊不推进。
3. **评审反馈**：unit_review_agent 不成熟时返回**缺口清单**（哪些要点缺失/不清晰），interview_agent 据此针对性追问，形成闭环。
4. **单元级中断与阶段级结束（操作语义）**：`skip` 操作立即停止当前单元的访谈，**跳过评审、强制转录**（尽力而为，缺失处标「待补充」），写入取值树后**继续下一单元**，不结束整个流程；`finish` 操作才是阶段级结束——结束访谈阶段、进入全文档审核。两者语义互斥：`skip` 只跳过当前单元，`finish` 只终止访谈阶段。（命令语法与映射由各前端定义，见 [frontends/](../frontends/)）
5. **跨单元上下文彻底隔离**：每个单元的访谈历史在转录完成后**丢弃**。下一单元开始时，interview_agent / unit_review_agent 的上下文仅为**已转录的结构化取值树** + 模板单元描述，不含任何原始对话。

#### transcribe_agent 硬性约束

1. **上下文必须包含现有结构化数据**：转录时输入 = 本单元对话 + 该单元对应的现有取值树子树（及必要的全局上下文）。重访/补访时，"现有数据"即上次转录的结果。
2. **提示词强制修订式转录**：提示词必须明确要求 agent **在任何原有数据的基础上修改**——保留仍有效的内容、修改过时的、补充缺失的，而非从对话重新生成或整体覆盖。
   - 语义：覆盖 = 有依据地修订，不是重写。
   - 修订后仍未被对话覆盖的既有内容，保持原样。

#### 无关输入处理

用户答非所问时，interview_agent 自然承接（确认 + 引导回主题），不强校验——引擎对 `input/send` 的 `text` 零语义，原样转发。

#### 数据与状态

- `prd_draft`：模板同构取值树，转录数据的落点，贯穿全流程
- `current_unit`（当前单元位置路径）、`units_done`（已完成单元列表）记录访谈进度
- 失败反馈统一走缺口清单（见决策 3 与 2.2），无逐字段回问路径
- 对话记录按单元隔离，转录后即弃

### 2.2 全文档审核阶段（Full Review）

#### 目标

在访谈阶段产出完整取值树后，进行**文档级**审核。由 **document_review_agent** 执行。与单元级成熟度评审（2.1 内）互补：后者保证"单元内够细"，前者保证"文档整体成立"。

#### 审核维度

| 维度 | 含义 |
|------|------|
| 完整性 | 全文档维度检查必填内容是否齐备、无遗漏章节 |
| 跨章节一致性 | 章节间引用是否一致（如功能清单 ↔ 核心功能详述、用户故事角色 ∈ 目标用户） |
| 逻辑自洽 | 整体逻辑是否自洽：目标 ↔ 方案 ↔ 指标是否闭环、有无自相矛盾 |

#### 输入 / 输出

- **输入**：完整取值树 `prd_draft` + 用户初始简述
- **输出**：`gap_list`（`[{path, dimension, reason}]`）+ `document_review_passed`
- 只允许报告取值树中真实存在的 path，不臆造

#### 失败处理：回灌访谈 agent，从头处理

审核不通过时，`gap_list` 交给访谈 agent，**从头处理**——不是直接 patch 取值树，而是：

1. 将 gap 对应位置按字段有效 tier 归并为访谈单元（规则见 [TEMPLATE_SPEC.md](../TEMPLATE_SPEC.md) 寻址一节）：P0 → 字段自身单元；P1 → 所属章节单元；P2 → 不走访谈，直接交由 transcribe_agent 依据上下文重新总结（复用修订式转录，输入为现有取值树 + 全局上下文，无新对话）
2. 访谈 agent 针对 gap 所在单元重新访谈 → 成熟度评审 → 转录
3. 完成后再次进入全文档审核
4. 兜底：最多 N 轮（默认 3）往返，超限强制进入渲染润色，渲染时在文末附「审核未决清单」

**覆盖语义**：gap 单元重新访谈后，转录结果**覆盖**该单元原有值。覆盖通过 transcribe_agent 的修订式转录实现（见 2.1 约束）：在原有数据基础上修改——保留仍有效的内容、修改过时的、补充缺失的，而非重写或仅追加。

#### 数据与状态

- `gap_list`、`document_review_passed`、`document_review_iterations`（全文档审核往返计数，与单元级迭代计数区分）
- 访谈阶段的**缺口驱动模式**：当带着 `gap_list` 进入时，按 gap 路径归并构造访谈单元（跳过无 gap 的单元），仍走同一套单元循环；P2 类 gap 不走访谈，直接转录兜底（见失败处理第 1 步）

### 2.3 渲染与润色阶段（Render & Polish）

#### 目标

在基线渲染之上提供**表示层润色**：同一信息，选择人类阅读效率更高的表示形式（表格 > 文字、图 > 文字、列表 > 段落）。

#### 表示优化场景目录（润色 agent 的能力依据）

**A 文本→表格**：A1 权限矩阵、A2 功能清单、A3 数据字典、A4 验收标准、A5 指标 KPI、A6 风险清单、A7 版本计划、A8 错误码/边界规则
**B 文本→图表（Mermaid）**：B1 系统架构（flowchart）、B2 业务主流程（flowchart）、B3 状态流转（stateDiagram）、B4 关键交互时序（sequenceDiagram）、B5 里程碑排期（gantt）、B6 实体关系（erDiagram）
**C 段落→结构化列表**：C1 术语表、C2 并列规则集、C3 版本历史、C4 长段落拆要点
**D 删减与提纯**：D1 冗余去重、D2 空话删减、D3 长句拆分

#### 流程

```
取值树（prd_draft）
   │
   ▼
① 确定性渲染（基线 Markdown）      ← 程序性、确定性、可重现
   │
   ▼
② polish_agent（润色生成器）   ← LLM
   │  扫描基线，识别 A/B/C/D 场景，产出转换提案：
   │  {位置, 原形态, 目标形态, 场景类, 风险级别, 理由}
   │
   ▼
③ 风险分级（规则化，非 LLM）
   │  A/C 类（表格化/结构化）→ 低风险，自动执行
   │  B/D 类（图表化/删减）  → 高风险，用户逐条确认
   │
   ▼
④ fidelity_evaluation_agent（保真评估器）← LLM
   │  逐条校验提案：事实点不增、不减、不改
   │  ┌─ 通过 → 应用转换
   │  └─ 不通过 → 反馈原因 → 回到 ② polish_agent 修订
   │            （最多 N 轮，仍不通过则丢弃该提案）
   │
   ▼
⑤ 输出最终 Markdown + 转换日志（附「审核未决清单」若有）
```

#### 确定性基线渲染规则

渲染器程序性遍历取值树，按模板结构确定性生成基线 Markdown（节点类型与 `field_type` 语义见 [TEMPLATE_SPEC.md](../TEMPLATE_SPEC.md)）：

- 按深度产出标题层级（`#` / `##` / `###` …）。
- `group` 只产出标题，不产出正文。
- `repeat` 实例产出标题（形如 `{item_label} {序号}` 或实例自定义名）。
- `field` 按 `field_type` 产出内容：`text` → 段落；`enum` → 所选项；`table` → Markdown 表格（首行为 `columns` 列名，其后每行一条记录）。
- 空值字段按策略跳过或输出占位符。
- 纯程序性、可重现；作为润色的**基线**，无提案区域不被触碰。

#### polish_agent ↔ fidelity_evaluation_agent 架构（多 agent）

| 角色 | 职责 |
|------|------|
| polish_agent | 扫描基线 → 识别场景 → 生成转换提案；接收 fidelity_evaluation_agent 反馈后修订提案 |
| fidelity_evaluation_agent | 对每个提案做事实点核对（增/减/改），判定通过/不通过，不通过时给出具体修订意见 |

- 两者形成迭代闭环：生成 → 评估 → 修订 → 再评估，最多 N 轮（默认 2），仍不通过则丢弃该转换提案
- fidelity_evaluation_agent 只负责保真（语义不变），不做风格与格式决策，职责单一

#### 关键设计决策

1. **图表格式**：Mermaid（flowchart / stateDiagram / sequenceDiagram / gantt / erDiagram），嵌入 Markdown，git 友好
2. **执行方式（分级策略）**：低风险（A/C）自动执行；高风险（B/D）用户逐条确认——确认也是 interrupt，一次可确认多条
3. **保真校验**：polish_agent ↔ fidelity_evaluation_agent 多 agent 架构，取代"规则化事实点比对"；fidelity_evaluation_agent 独立于 polish_agent，避免自评
4. **确定性基线**：润色只在确定性渲染的基线上做增量转换，无提案区域不被触碰
5. **转换日志**：所有已应用/已丢弃的转换记录在案（位置、场景类、原/新形态、理由、评估结果），支持复核与回退
6. **未决清单**：全文档审核超限仍未通过时，在最终 Markdown 文末附「审核未决清单」，列出仍未解决的所有缺口
7. **转换禁区**：`field_type: table` / `enum` 字段具有固有表示形态，`preserve: true` 的 `text` 字段为显式禁区，三者一律禁止任何表示转换（`preserve` 语义见 [TEMPLATE_SPEC.md](../TEMPLATE_SPEC.md)）

#### 数据与状态

- `polish_proposals`（polish_agent 产出，待分级/待确认）、`conversion_log`（已应用/已丢弃转换记录）
- `final_prd` 为最终输出

## 3. 会话引擎（只理解 API）

```
Session
 ├─ graph          LangGraph 编译图（checkpointer = SqliteSaver）
 ├─ stage          interview | document_review | polish | finished
 ├─ status         running | waiting | idle
 ├─ pending_queue  输入暂存队列（busy 期间收下，下一个中断点回放）
 └─ interrupt      当前挂起的中断（interview 等待 / proposal 确认）
```

- **API 方法分发**：引擎入口是协议方法分发器（方法 → 引擎操作），不存在「命令文本路由」——`input/send` 的 `text` 是纯数据，原样转发给当前活跃节点；`command/skip` / `command/finish` / `command/undo` / `proposal/respond` / `session/quit` 是结构化操作（原则 6，见 [ARCHITECTURE_V2.md](../ARCHITECTURE_V2.md)）。
- **单一状态源**：状态本体在 LangGraph checkpoint 中（仅 SqliteSaver 一份）；会话元数据表只维护会话清单（创建时间、模板、状态摘要），不复制状态。
- **单会话串行**：一个 Session 同一时刻只跑一个图执行（流式 `stream_mode`）；busy 期间到达的 `input/send` 进入 `pending_queue`（对应协议 `queued: true`，见 [PROTOCOL.md](../api/PROTOCOL.md) §8），其余请求返回 1005。
- **事件发布**：引擎内所有状态变化发布领域事件；由传输适配层序列化为协议通知。引擎不感知前端存在。

## 4. 技术栈结论

（选型理由见 [ARCHITECTURE_V2.md](../ARCHITECTURE_V2.md) 设计原则 3）

- **编排**：主图（LangGraph 图 1）覆盖访谈阶段的单元循环（interview_agent → unit_review_agent → transcribe_agent），含缺口驱动模式，以产出完整结构化取值树为终点；审核与润色为简单循环（或轻量图 2）：全文档审核回灌循环 + 渲染润色（polish_agent ↔ fidelity_evaluation_agent 迭代，纯 while 实现即可）。
- **持久化：仅 SqliteSaver 一份**：会话状态（取值树、进度、interrupt 点）统一存入 LangGraph SqliteSaver（`sqlite3` 为 Python 标准库，零新增依赖）；查询直接读 checkpoint 内容，不自建第二套快照；`interrupt` 依赖 checkpointer，跨进程恢复必须持久化 saver。
- 技术栈选型唯一结论：**保留 LangGraph，不切换**；新增 WebSocket 服务依赖待定（`websockets` / `uvicorn`），不影响协议层。

## 5. 模块结构

```
src/yespm_backend/
├── engine/                 # 无头会话引擎（零 UI、零传输依赖）
│   ├── session.py          # Session 状态机：持有图实例、方法分发入口、中断管理、暂存队列
│   ├── dispatch.py         # API 方法分发：协议方法 → 引擎操作（无命令文本路由）
│   ├── events.py           # 领域事件定义（事件目录的 Python 实现，PROTOCOL §5）
│   └── store.py            # 会话元数据（会话清单；状态本体在 checkpoint 中）
├── graph/                  # LangGraph 图与节点（访谈 / 审核 / 润色，见 §2）
├── protocol/               # 协议层（唯一通信面）
│   ├── jsonrpc.py          # 信封编解码、错误码映射（PROTOCOL §2 / §11）
│   ├── transport.py        # Transport 抽象：in-process / stdio / websocket 三实现
│   ├── stdio.py            # stdio 适配（见 api/STDIO.md）
│   └── websocket.py        # WebSocket 适配（见 api/WEBSOCKET.md）
└── entry/                  # 进程入口（三个壳，共享同一 engine 与 protocol）
    ├── cli.py              # yespm        → 进程内 transport（CLI 前端）
    ├── server_stdio.py     # yespm-server → stdio 桥（TUI 用）
    └── server_ws.py        # yespm-ws     → WebSocket 桥（Web 用）
```

## 6. 进程形态（一个引擎，三个壳）

| 命令 | 传输 | 使用者 |
|------|------|--------|
| `yespm` | 进程内（engine 直连） | 纯 CLI（Python） |
| `yespm-server` | stdio | TUI（TypeScript 子进程） |
| `yespm-ws` | WebSocket | Web（JS/TS） |

三个壳共享同一 `engine/` 与 `protocol/`；`entry/` 只做传输启动与生命周期管理，不含任何业务逻辑。

## 7. 关键约束

1. **依赖方向单向**：`engine/` → `graph/`；`protocol/` 与 `entry/` → `engine/`。engine 不得 import 任何 UI / 传输代码。
2. **单一契约**：CLI 不绕过协议直调 engine 内部——它使用进程内 Transport 走同一 JSON-RPC 消息（[PROTOCOL.md](../api/PROTOCOL.md) 原则 6），保证三前端行为一致。
3. **自由文本零命令语义**：引擎对 `input/send` 的 `text` 不做任何命令解析（`/xxx`、`y/n` 等一律按数据转发给当前活跃节点）。
4. **错误分级**：可恢复（LLM 调用失败 → 重试，耗尽后 3001）与致命（配置错误 2002、模板不合法 2001——加载时由 Pydantic 校验，启动阶段即拒绝并报错定位）。
5. **零新增依赖**：SQLite（标准库）持久化沿用设计原则 3 的结论。
