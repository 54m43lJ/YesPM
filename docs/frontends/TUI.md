# TUI 前端（Rust，主入口）

> 契约见 [api/PROTOCOL.md](../api/PROTOCOL.md)，传输见 [api/STDIO.md](../api/STDIO.md)。TUI 是**主入口**前端（Rust 二进制 `yespm`），CLI 为高级用法。交互定义（本节）与 CLI / Web / 桌面初期一致，此后允许因键盘驱动界面的技术限制分叉。需求与交付内容见 [TUI-DELIVERY.md](./TUI-DELIVERY.md)（草稿）。

## 1. 定位

- 进程形态：spawn `yespm-server` 子进程（Python），通过 **stdio JSON Lines** 通信。
- 技术选型：Rust——ratatui（渲染）+ crossterm（终端后端）+ tokio（子进程管理），选型记录见 [TUI-DELIVERY.md](./TUI-DELIVERY.md) §2。
- 交付：Rust 单二进制 `yespm`，与后端进程捆绑分发（TUI 自身可引导发现 `yespm-server`）。
- 职责：交互解释与包装——快捷键 / 命令 → 方法调用，事件 → 面板渲染。

## 2. 交互定义

### 2.1 输入方式

- **输入框**：底部输入区，支持多行输入（Shift+Enter 换行，Enter 提交；终端对 Shift+Enter 支持不一，保留 Ctrl+J 兜底换行，实现时实测）。
- **键盘导航**：`Tab` / 方向键在视图间切换。
- **粘贴**：文本域式粘贴（Ctrl+V），无需专用命令。

### 2.2 命令集与 API 映射

| 输入 | 语义 | 映射 |
|------|------|------|
| `/help` 或 `?` | 显示命令列表与当前可用操作（本地渲染，可查 `query/commands`） | 前端本地 |
| `/status` | 状态面板：当前单元、已完成单元、取值树摘要 | `query/status` |
| `/view [路径]` | 取值树视图（可折叠浏览） | `query/tree` |
| `/skip` 或 `Alt+S` | 跳过当前单元（强制转录，进入下一单元） | `command/skip` |
| `/finish` 或 `Alt+F` | 结束访谈阶段，进入全文档审核 | `command/finish` |
| `y` / `n`（确认面板） | 润色阶段确认 / 拒绝高风险提案（可批量选择后回车） | `proposal/respond` |
| `/undo` 或 `Ctrl+Z` | 回退最近一次转录 / 转换 | `command/undo` |
| `/quit` 或 `Ctrl+Q` | 退出（保存 checkpoint） | `session/quit` |

### 2.3 流式输出

- 对话流按 `message_id` 拼接 `session/message`（2031 chunk）增量渲染；2032（complete）提供全量文本（供复制）。

### 2.4 中断与退出语义

- **Ctrl+C**：由 TUI 框架接管（非终端信号语义）——第一次中断当前流式输出；再次按触发退出流程（`session/quit` 后退出）。
- **窗口关闭**：发送 `session/quit`，关闭 stdin，等待子进程退出（见 §4）。

## 3. 视图结构

| 视图 | 内容 | 数据来源 |
|------|------|---------|
| 会话列表 | 历史会话（模板、状态、时间） | `session/list` |
| 访谈视图 | 对话流 + 输入框、当前单元提示、已完单元进度 | 事件流 + `query/status` |
| 润色确认 | 高风险转换提案逐条确认（apply / reject，可批量）——自然语言描述来自 `session/message`，`proposal_ids` 来自 `session/status` 的 `waiting` | `session/message` + `session/status` → `proposal/respond` |
| 取值树 | `query/tree` 树形浏览 | `tree/changed` → `query/tree` |
| 文档预览 | `query/prd` 的 Markdown（只读，可导出） | `prd/changed` → `query/prd` |

## 4. 必须处理的事件 → 行为

| 事件 | 行为 |
|------|------|
| `session/status`（首条全字段） | 进入访谈视图 |
| `session/message`（2031 / 2032） | 流式渲染到对话流（按 `message_id` 拼接） |
| `session/await_input` | 显示输入框（`stage` / `waiting.kind=interview`）；`waiting.kind=proposal` 切换确认面板 |
| `session/status`（`waiting` 含 `proposal_ids`） | 结合 `session/message` 自然语言提案描述，`y`/`n` 或批量选择后 `proposal/respond` |
| `tree/changed` | 若有打开的取值树视图则刷新（`query/tree`） |
| `session/status`（`current_unit` / `units_done` / `stage` 等字段） | 刷新当前单元 / 进度栏 |
| `gaps/changed` | 显示审核结果与缺口清单（按需 `query/gaps`；自然语言结论已随 `session/message` 显示） |
| `conversions/changed` | 刷新转换日志面板（`query/conversions`） |
| `prd/changed` | 切换到文档预览（`query/prd`），提供导出（写文件 / 复制） |
| `session/status`（`ended` 字段） | 返回会话列表 |
| `log` | 错误浮层（按 `status_code` 等级） |

## 5. 进程生命周期（子进程管理）

1. spawn `yespm-server --db <path>` → 等待 `server/ready` 握手（[STDIO.md](../api/STDIO.md) §3）。
2. 正常退出：发送 `session/quit` → 等待 `session/status`（`ended` 字段）→ 关闭 stdin。
3. 崩溃恢复：子进程非 0 退出 → 提示用户 → 重新 spawn → 对活跃会话 `session/resume`（挂起中断由引擎补发 `session/await_input`）。
4. 前端退出：EOF 关闭子进程，防止孤儿进程。

## 6. 与 CLI 的差异

仅渲染形态与输入方式不同（键盘导航、面板布局、快捷键）；协议调用、事件处理、错误语义遵循同一契约（[PROTOCOL.md](../api/PROTOCOL.md)）。
