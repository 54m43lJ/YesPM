# YesPM 架构设计

> 本文档是 YesPM 的**项目地图与设计原则**：定义系统分层与核心设计决策及其理由，不含实现细节。工作流程与引擎机制见 [backend/ARCHITECTURE.md](./backend/ARCHITECTURE.md)，接口契约见 [api/PROTOCOL.md](./api/PROTOCOL.md)，各前端的交互定义见 [frontends/](./frontends/)（每前端一份，允许因技术限制分叉）。

## 分层架构：前后端分离

系统拆为**后端引擎**与**前端**两层，中间以统一协议衔接（契约 [api/PROTOCOL.md](./api/PROTOCOL.md)）：

```
┌──────────────────────────────────────────────────────────────────┐
│ 前端 Frontends（交互解释与包装，API 的唯一理解者）                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │ CLI         │  │ TUI         │  │ Web         │  │ Desktop     │ │
│  │ (Python)    │  │ (Rust)      │  │ (JS/TS)     │  │ (占位)       │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘ │
└─────────┼────────────────┼───────────────┼────────────────┼────────┘
          │ 进程内           │ stdio          └──── WebSocket ────────┘
          ▼                ▼
┌──────────────────────────────────────────────────────────────────┐
│ 协议层 JSON-RPC 2.0（单一契约、双传输可插拔）                      │
│  方法 / 事件 / 错误码目录见 api/PROTOCOL.md                        │
└──────────────────────┬────────────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│ 后端 Backend（Python，无头 headless）                              │
│  引擎：API 分发 / 中断 / 输入暂存 / checkpoint                     │
│  工作流程见 backend/ARCHITECTURE.md                                │
└──────────────────────────────────────────────────────────────────┘
```

## 设计原则

1. **模板唯一真源 + 取值树中间态**：模板 YAML 是问答与渲染的唯一真源，不存在独立的渲染模板文件，最终文档由渲染器程序性遍历模板树生成；全流程中间态是与模板同构的取值树（结构化数据），而非文本草稿（编写规范见 [TEMPLATE_SPEC.md](./TEMPLATE_SPEC.md)）。

2. **缺口清单统一反馈**：单元成熟度评审与全文档审核产出同一形态的缺口清单 `[{path, dimension, reason}]`，是全流程唯一的反馈载体，回灌访谈 agent 复用同一套单元循环，无逐字段回问路径。

3. **interrupt + checkpoint 为交互核心**：人工介入（interrupt）与断点续聊（checkpoint）是本系统的核心需求；会话状态统一由 checkpointer 持久化，不另建状态管理。

4. **协议优先、事件驱动、后端无头**：语义与传输解耦（协议层可插拔传输），**单一契约、多端复用**，不存在第二套接口；后端是唯一状态所有者，状态变化一律事件推送，前端零轮询、零状态推断；事件收敛为少量通用模板，大载荷以成对的变更信号 + 专属查询提供（契约细节见 [api/PROTOCOL.md](./api/PROTOCOL.md) §5）；后端只理解 API，不理解任何用户命令与交互形态——命令语法、快捷键、界面表现都是前端对 API 的重新解释与包装。

5. **交互定义前端冗余、允许分叉**：每个前端各自维护一份完整交互定义（[frontends/](./frontends/)）。

6. **命令结构化**：后端 API 不允许承载「命令执行」语义的自由文本（如 `/skip`、`y/n`）。命令是结构化方法调用（`command/skip`、`command/finish`、`command/undo`、`proposal/respond`、`session/quit`）；`input/send` 的 `text` 是纯数据（访谈回复 / 补充信息），由引擎原样转发给当前活跃节点，引擎内不存在命令文本解析。

7. **确定性基线 + 分级润色**：渲染与润色由两层组成——第一层确定性渲染程序性产出可重现的 Markdown 基线，第二层润色在基线上做增量表示转换；低风险转换（表格化 / 结构化）自动执行，高风险转换（图表化 / 删减）由用户逐条确认；所有转换记录于转换日志，支持复核与回退。

8. **审核回灌闭环**：全文档审核不通过时，缺口按字段有效 tier 归并为访谈单元重新访谈，最多 N 轮，超限强制进入渲染并附「审核未决清单」（归并规则见 [backend/ARCHITECTURE.md](./backend/ARCHITECTURE.md) 寻址与 gap 归并一节）。

9. **TDD：先测后写，节点级验证**：交付的代码必须包含意义明确的单元测试，颗粒度以**验证每个流程节点能否跑通**为基准（访谈 / 单元评审 / 转录 / 全文档审核 / 润色生成 / 保真评估 / 确定性渲染各节点），禁止以冒烟测试充数——测试断言具体行为与状态变化，非「无异常即通过」。开发收尾阶段按自然语言脚本形式的集成测试（[TEST-DRIVEN-DEVELOPMENT.md](./TEST-DRIVEN-DEVELOPMENT.md)）逐场景走查，由开发确认全流程端到端跑通。

## 流程速览

```
用户简述 → ① 访谈（interview ↔ 用户 → 单元评审 → 转录取值树）
        → ② 全文档审核（不通过 → 缺口清单 → 回 ①；通过 ↓）
        → ③ 确定性渲染 → 润色（polish_agent ↔ fidelity_evaluation_agent）→ 最终 Markdown
```

（阶段细节与 agent 职责见 [backend/ARCHITECTURE.md](./backend/ARCHITECTURE.md) 工作流程章节）

## 文档地图

| 文档 | 内容 |
|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md)（本文） | 项目地图 + 设计原则（含选型理由） |
| [TEMPLATE_SPEC.md](./TEMPLATE_SPEC.md) | 模板编写规范（面向自定义模板的二次开发者） |
| [backend/ARCHITECTURE.md](./backend/ARCHITECTURE.md) | 后端：工作流程（三阶段）+ 取值树与寻址（含 gap 归并）+ 引擎机制 + 技术栈结论 + 模块/进程形态 |
| [TEST-DRIVEN-DEVELOPMENT.md](./TEST-DRIVEN-DEVELOPMENT.md) | 测试策略（节点级单元测试）+ 集成测试脚本（自然语言，收尾阶段走查） |
| [api/PROTOCOL.md](./api/PROTOCOL.md) | 接口契约：方法 / 事件 / 错误码 / 时序 |
| [api/STDIO.md](./api/STDIO.md) | stdio 传输绑定 API |
| [api/WEBSOCKET.md](./api/WEBSOCKET.md) | WebSocket 传输绑定 API |
| [frontends/CLI.md](./frontends/CLI.md) | CLI 前端架构（Python，高级用法）：交互定义 + 事件处理 |
| [frontends/TUI.md](./frontends/TUI.md) | TUI 前端架构（Rust，主入口）：交互定义 + 事件处理 |
| [frontends/WEB.md](./frontends/WEB.md) | Web 前端架构（JS/TS）：交互定义 + 事件处理 |
| [frontends/DESKTOP.md](./frontends/DESKTOP.md) | 桌面前端架构（占位，WebView 包装 Web）：交互定义 + 事件处理 |
