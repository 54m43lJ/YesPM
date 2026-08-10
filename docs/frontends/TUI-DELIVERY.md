# TUI 前端交付草稿（需求与交付内容）

> 状态：**草稿**。本文档定义 TUI 前端（Rust）的需求与交付内容，产出于 M0 文档里程碑。交互定义见 [TUI.md](./TUI.md)，语义层契约见 [api/PROTOCOL.md](../api/PROTOCOL.md)，传输绑定见 [api/STDIO.md](../api/STDIO.md)。

## 1. 命名与定位

| 前端 | 二进制 | 语言 | 定位 | 传输 |
|------|--------|------|------|------|
| TUI | `yespm` | Rust | **主入口**（默认体验） | spawn `yespm-server`，stdio JSON Lines |
| CLI | `yespm-cli` | Python | 高级用法（脚本化 / 无交互环境 / 深度调试） | 进程内 |
| Web | — | JS/TS | 浏览器前端（占位） | WebSocket（`yespm-ws`） |
| Desktop | `yespm-desktop` | WebView 包装 Web | Web 前端的桌面壳层（占位） | WebSocket（`yespm-ws`） |

后端桥进程（由 Python 包提供）：`yespm-server`（stdio）/ `yespm-ws`（WebSocket）。两者**服务端接口一致**（同一契约、双传输）；客户端各自实现，**无客户端侧等价性要求**。

## 2. 技术选型

| 项 | 结论 | 说明 |
|----|------|------|
| 渲染 | ratatui | immediate-mode，widget 差分渲染，长文本流式无压力 |
| 终端后端 | crossterm | 跨平台（含 Windows），原始模式 / 键盘事件 |
| 异步运行时 | tokio | 子进程管理（`tokio::process`）、stdout 逐行读取 |
| 序列化 | serde / serde_json | 协议类型静态化：信封 / 方法 / 事件 / 状态码全部编译期校验 |
| CLI 参数 | clap | `--db` / `--template` / `--config` 透传 |
| 错误处理 | thiserror | 状态码 → 用户可读错误 |

选型理由：单二进制分发（与 Python 后端捆绑最简、零运行时依赖）、流式渲染性能、协议类型编译期校验、长驻交互进程可靠性。不选 Node/Ink 的理由：npm 分发需 Node 运行时、React reconciliation 对长流式文本需裁剪策略、与「二进制捆绑分发」交付目标不符。

## 3. 需求清单

| # | 需求 | 契约依据 |
|---|------|---------|
| R1 | 协议客户端：JSON-RPC 2.0 信封编解码、请求 id 关联、通知分发、按行帧解析 | PROTOCOL §2 |
| R2 | stdio 传输适配：spawn 子进程、`server/ready` 握手后发请求、stdout 事件/响应、stderr 落日志、请求级超时（秒级） | STDIO §2/3/5 |
| R3 | 五个视图：会话列表 / 访谈 / 润色确认 / 取值树 / 文档预览，Tab + 方向键切换 | TUI §3 |
| R4 | 底部多行输入框（Shift+Enter 换行、Enter 提交、Ctrl+V 粘贴，Ctrl+J 兜底） | TUI §2.1 |
| R5 | 命令与快捷键映射（`/help`、`/status`、`/view [路径]`、`/skip` Alt+S、`/finish` Alt+F、`/undo` Ctrl+Z、`/quit` Ctrl+Q、`y`/`n` 批量确认） | TUI §2.2 |
| R6 | 流式渲染：按 `message_id` 拼接 2031 chunk，2032 全量可复制 | TUI §2.3、PROTOCOL §6 |
| R7 | 事件处理表全量实现（11 类事件 → 行为） | TUI §4 |
| R8 | revision 跳号检测 → 主动 `query/status` 对齐 | PROTOCOL §5.2 |
| R9 | 进程生命周期：正常退出、崩溃恢复 + `session/resume`、防孤儿（EOF 关子进程） | TUI §5、STDIO §4/5 |
| R10 | Ctrl+C 两级中断语义（第一次打断流式输出，再次触发退出流程）；窗口关闭发 `session/quit` | TUI §2.4 |
| R11 | 错误呈现：4xxx/5xxx 按等级浮层（`log` 事件 + JSON-RPC error 响应） | TUI §4、PROTOCOL §11 |
| R12 | 大载荷按需拉取：`tree/changed` → `query/tree` 等成对通道，禁入 status | PROTOCOL §5.3 |

非功能（模糊）：Windows 优先（当前开发机）、流式渲染不卡顿、协议处理与 UI 渲染解耦（事件队列 + 状态快照）。

## 4. 交付内容

### 4.1 文档交付（doc/tui 分支）

| 文件 | 变更 |
|------|------|
| `frontends/TUI-DELIVERY.md`（本文件） | 需求与交付草稿 |
| `frontends/TUI.md` | 选型落地（ratatui）、交付形态（二进制 `yespm`）、主入口定位、键盘兜底 |
| `frontends/CLI.md` | 二进制 `yespm-cli`、高级用法定位 |
| `frontends/WEB.md` | 恢复（Web 浏览器前端） |
| `frontends/DESKTOP.md` | 新建：WebView 包装 Web 的桌面壳层（占位） |
| `api/STDIO.md` / `api/PROTOCOL.md` / `api/WEBSOCKET.md` | 语言中性措辞；删除客户端侧等价性描述（仅保留服务端双传输一致） |
| `backend/ARCHITECTURE.md` / `ARCHITECTURE.md` | 命名修订、约束 2 理由句清理、文档地图更新 |
| `README.md` / `TEST-DRIVEN-DEVELOPMENT.md` | 入口表与执行载体更新 |

### 4.2 代码交付（dev/tui 分支，基于 dev/evol-2）

```
crates/                        # cargo workspace
├── yespm-client/              # TUI 专用协议客户端（无共享约束）
│   ├── src/protocol/          # serde 类型：信封 / 方法 / 事件 / 状态码
│   ├── src/client.rs          # 请求 id 分配、pending 表、通知分发、revision 对齐
│   └── src/transport/         # Transport trait + StdioTransport
│                              #   spawn / server/ready 握手 / 逐行解析 / EOF / 崩溃恢复 / 防孤儿 / 超时
├── yespm-tui/                 # bin `yespm`（主入口）
│   ├── src/app.rs             # 视图状态机、会话 store、命令路由
│   ├── src/ui/                # SessionList / Interview / PolishPanel / TreeView / DocPreview /
│   │                          # InputBox / StatusBar / HelpPanel
│   └── src/input.rs           # crossterm 键盘事件 → 动作映射
└── yespm-mock/                # 按 STDIO.md 行为的 mock 子进程（开发 + cargo test 集成测试）
```

依赖（占位）：ratatui、crossterm、tokio、serde/serde_json、clap、thiserror。

### 4.3 打包与后端发现

- 交付形态：Rust 单二进制 `yespm`，与后端进程捆绑分发（占位：zip / 安装器 / 自解压，未定）。
- 后端发现：PATH 探测 `yespm-server`，回退 Conda 环境 `yespm` 的 Python 路径；`--server <path>` 覆盖（占位，实现时定）。

## 5. 里程碑与验证

| 里程碑 | 内容 | 验证 |
|--------|------|------|
| M0 | doc/tui：本草稿成文 + 各文档命名/选型落地 | 文档评审 |
| M1 | yespm-client + StdioTransport + yespm-mock | cargo test（单元 + mock 集成） |
| M2 | TUI 骨架：会话列表 + 访谈视图 + 输入框 + 流式渲染 | mock 集成 |
| M3 | 润色确认 / 取值树 / 文档预览 / 命令集齐 | mock 集成 |
| M4 | 生命周期：崩溃恢复、resume、退出语义、打包与后端发现 | mock + 手工 |
| M5 | 真实后端集成测试（后端 dev/evol-2 就绪后） | 端到端 |

## 6. 开放问题（占位）

- Windows 终端下 Alt+S / Ctrl+Z / Shift+Enter 键码差异与兜底（Ctrl+J 候选）；crossterm 对 Alt 修饰键的表现待实测。
- 捆绑发布形态（zip / 安装器 / 自解压）。
- `yespm-mock` 是否保留为开发依赖。
- Web 前端（JS/TS）与桌面（`yespm-desktop`，WebView 包装 Web）技术选型未定（Web：React + Vite 建议；桌面：Tauri / Electron 候选）。
