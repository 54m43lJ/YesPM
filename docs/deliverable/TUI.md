# TUI 前端交付（需求与验收）

> 面向 TUI 实现者：本文档逐项列出可交付功能、验收标准与里程碑，需求来源均引用项目文档（交互定义见 [frontends/TUI.md](../frontends/TUI.md)，语义层契约见 [api/PROTOCOL.md](../api/PROTOCOL.md)，传输绑定见 [api/STDIO.md](../api/STDIO.md)）。本文档只对**交付一个用户可以使用的 TUI 界面**负责，不涉及其他前端形态或其他传输。

## 1. 范围与定位

| 项 | 内容 |
|----|------|
| 交付物 | Rust 单二进制 `yespm`（主入口前端） |
| 通信方式 | spawn 后端 stdio 桥 `yespm-server`，stdio JSON Lines（[STDIO.md](../api/STDIO.md)） |
| 不在范围 | 其他前端（CLI / Web / 桌面）；非 stdio 传输（WebSocket）；后端引擎实现 |
| 分层依据 | 前端实现只依赖协议契约与传输绑定；后端文档见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md)，本文档不重复其内容 |

## 2. 技术选型与工程结构（实现约定）

| 项 | 结论 | 说明 |
|----|------|------|
| 渲染 | ratatui | immediate-mode，widget 差分渲染，长文本流式渲染 |
| 终端后端 | crossterm | 跨平台（含 Windows），原始模式 / 键盘事件 |
| 异步运行时 | tokio | 子进程管理（`tokio::process`）、stdout 逐行读取 |
| 序列化 | serde / serde_json | 协议类型静态化：信封 / 方法 / 事件 / 状态码编译期校验 |
| CLI 参数 | clap | 后端发现覆盖参数 |
| 错误处理 | thiserror | 状态码 → 用户可读错误 |

```
crates/                        # cargo workspace
├── yespm-client/              # 协议客户端
│   ├── src/protocol/          # serde 类型：信封 / 方法 / 事件 / 状态码
│   ├── src/client.rs          # 请求 id 分配、pending 表、通知分发、revision 对齐
│   └── src/transport/         # Transport trait + StdioTransport
│                              #   spawn / server/ready 握手 / 逐行解析 / EOF / 崩溃恢复 / 防孤儿 / 超时
├── yespm-tui/                 # bin `yespm`（主入口）
│   ├── src/app.rs             # 视图状态机、会话 store、命令路由
│   ├── src/ui/                # SessionList / Interview / PolishPanel / TreeView / DocPreview /
│   │                          # ConversionLog / InputBox / StatusBar / HelpPanel
│   └── src/input.rs           # crossterm 键盘事件 → 动作映射
└── yespm-mock/                # 按 STDIO.md 行为的 mock 子进程（开发 + cargo test 集成测试）
```

## 3. 需求分段与里程碑

每个里程碑交付物独立可检查（检查方式见段末「检查」与 §4 总表）；验收标准逐条对应上表需求。

### M1 协议客户端与 stdio 传输

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| R1 | JSON-RPC 2.0 信封编解码（请求 / 响应 / 通知） | 与 mock 子进程往返：请求得到对应 `id` 的响应，通知按 `method` 分发 | PROTOCOL §2 |
| R2 | 请求 id 关联与 pending 管理 | 跨会话并发请求响应按 `id` 回调，不错乱、不丢失 | PROTOCOL §2 |
| R3 | 子进程 spawn 与 `server/ready` 握手 | 收到 `server/ready` 前不发送请求；收到后正常收发 | STDIO §3 |
| R4 | stdout 逐行 JSON 帧解析 | 一帧一个完整 JSON 对象；消息内转义 `\n` 不破坏帧 | STDIO §2 |
| R5 | stderr 日志落点与请求超时 | `log` 事件与进程错误进 stderr；命令类请求不设硬超时，其余响应秒级上限 | STDIO §2 / §5 |

**里程碑交付物**：`yespm-client` 库 + `yespm-mock`（按 STDIO.md 行为）。
**检查**：`cargo test`（单元 + mock 集成）通过。

### M2 会话管理

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| R6 | 会话列表视图 | 渲染 `session/list` 结果（模板、状态、时间）；选中 + Enter 恢复 | PROTOCOL §4.1；TUI §3 |
| R7 | 新建会话（`/new [模板]`） | 创建成功后进入访谈视图，首条全字段 `session/status` 已渲染 | PROTOCOL §4.1；TUI §2.2 |
| R8 | 恢复会话（`/resume <id>`） | 恢复后取值树、当前单元、已完成单元完整；挂起中断补发 `await_input` 后进入等待态 | PROTOCOL §4.1 / §10.3 |
| R9 | 结束会话（`/quit`） | 保存 checkpoint，收到 `ended` 后返回会话列表，进程保持运行 | PROTOCOL §4.1；TUI §5 |
| R10 | 崩溃恢复 | 子进程非 0 退出 → 提示 → 重新 spawn → 对活跃会话 `session/resume` 续聊 | STDIO §5；TUI §5 |

**里程碑交付物**：会话列表与创建 / 恢复 / 退出全流程。
**检查**：mock 集成走查 R6–R10 各一项。

### M3 访谈主界面

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| R11 | 底部多行输入框 | Shift+Enter 换行、Enter 提交、Ctrl+V 粘贴，Ctrl+J 兜底换行；提交为 `input/send` | TUI §2.1 |
| R12 | 流式输出渲染 | `session/message` 2031 chunk 按 `message_id` 增量拼接渲染；2032 全量文本可复制 | PROTOCOL §6；TUI §2.3 |
| R13 | 等待输入状态 | `session/await_input` 激活输入框；阶段 / 等待种类由 `session/status` 的 `stage` / `waiting` 推断 | PROTOCOL §5.1 |
| R14 | 命令集与快捷键映射 | `/help` `/status` `/view` `/skip` `/finish` `/undo` `/quit` 及 Alt+S / Alt+F / Ctrl+Z / Ctrl+Q 按映射调用方法；`/help` 本地渲染并查 `query/commands` | TUI §2.2 |
| R15 | 进度与单元状态刷新 | `session/status`（`current_unit` / `units_done` / `stage` 等字段）到达时刷新当前单元与进度栏 | TUI §4 |
| R16 | 事件处理表全量实现 | TUI §4 表全部 11 类事件（含首条全字段、`waiting` 提案、`ended` 返回列表、`log` 浮层）行为一致 | TUI §4 |

**里程碑交付物**：访谈主界面（会话列表 + 访谈视图 + 输入框 + 流式渲染 + 命令集）。
**检查**：mock 集成走查一轮完整访谈（新建 → 多轮对话 → 命令操作 → 结束）。

### M4 文档消费视图

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| R17 | 取值树视图（`/view [路径]`） | 树形可折叠浏览（路径寻址）；`tree/changed` 后刷新（按需 `query/tree`） | PROTOCOL §4.3 / §5.3；TUI §3 |
| R18 | 文档预览与导出 | `prd/changed` 后 `query/prd` 渲染最终 Markdown，支持写文件 / 复制 | PROTOCOL §4.3；TUI §3 |
| R19 | 转换日志视图 | `conversions/changed` 后 `query/conversions`，区分已应用 / 已丢弃，支持复核 | PROTOCOL §4.3；TUI §3 |
| R20 | 审核缺口展示 | `gaps/changed` 后按需 `query/gaps` 展示缺口清单；自然语言结论随 `session/message` 显示 | PROTOCOL §4.3 / §5.3 |
| R21 | 润色确认面板 | `waiting.kind=proposal` 时逐条列出提案（描述来自 `session/message`，id 来自 `waiting.proposal_ids`），批量 `y`/`n` 后 `proposal/respond` | PROTOCOL §4.2 / §5.1；TUI §3 |

**里程碑交付物**：取值树 / 文档预览 / 转换日志 / 审核缺口 / 润色确认五个视图。
**检查**：mock 集成走查 R17–R21 各一项（含 apply 与 reject）。

### M5 可靠性、打包与发布

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| R22 | revision 跳号检测 | 发现跳号主动 `query/status` 对齐 | PROTOCOL §5.2 |
| R23 | 退出序列 | 会话退出（`/quit` → `ended` → 列表）；应用退出（`/quit` → `ended` → 关闭 stdin EOF） | TUI §5；STDIO §4 |
| R24 | Ctrl+C 两级中断 | 第一次打断当前流式输出；再次触发应用退出流程；窗口关闭发送 `session/quit` | TUI §2.4 |
| R25 | 防孤儿 | 应用退出时关闭 stdin / terminate，不留孤儿子进程 | STDIO §5；TUI §5 |
| R26 | 错误呈现 | `log` 事件与 JSON-RPC error 响应按 4xxx / 5xxx 等级浮层展示 | PROTOCOL §11 |
| R27 | 打包与后端发现 | `yespm` 单二进制与后端捆绑分发；启动时 PATH → Conda 环境回退发现 `yespm-server`，`--server` 可覆盖 | TUI §1 |

**里程碑交付物**：完整生命周期可靠性 + 发布产物。
**检查**：mock + 手工走查 R22–R26；真实后端端到端走查 R27（含崩溃恢复与断点续聊）。

## 4. 里程碑总表

| 里程碑 | 交付物 | 检查方式 |
|--------|--------|---------|
| M1 | 协议客户端 + mock 子进程 | `cargo test` |
| M2 | 会话管理全流程 | mock 集成 |
| M3 | 访谈主界面 | mock 集成 |
| M4 | 文档消费与润色确认视图 | mock 集成 |
| M5 | 可靠性、打包与发布 | mock + 手工 + 真实后端端到端 |

## 5. 开放问题（占位）

- Windows 终端下 Alt+S / Ctrl+Z / Shift+Enter 键码差异与兜底（Ctrl+J 候选）；crossterm 对 Alt 修饰键的表现待实测。
- 捆绑发布形态（zip / 安装器 / 自解压，未定）。
- `yespm-mock` 是否保留为开发依赖。
