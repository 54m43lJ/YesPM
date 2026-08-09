# Web 前端（JS/TS）

> 契约见 [api/PROTOCOL.md](../api/PROTOCOL.md)，传输见 [api/WEBSOCKET.md](../api/WEBSOCKET.md)。Web 前端是协议在**远端多用户形态**下的第三个消费方。交互定义（本节）与 CLI / TUI 初期一致，此后允许因浏览器技术限制分叉（无终端信号、无键盘优先输入流）。

## 1. 定位

- 进程形态：浏览器连接 `yespm-ws`（WebSocket 主通道 + `/healthz` 健康检查）。
- 技术选型（占位，实现时定）：React + Vite（仅建议，不强制）。
- 部署：与后端**分离部署**——后端只提供 API；前端构建产物独立托管（[WEBSOCKET.md](../api/WEBSOCKET.md) §7）。
- 职责：交互解释与包装——按钮 / 快捷键 / 命令 → 方法调用，事件 → 界面更新。

## 2. 交互定义

### 2.1 输入方式

- **输入区**：多行文本域（Enter 换行、Ctrl+Enter 提交），长内容直接粘贴（等价 CLI 的 `/paste`，无需专用命令）。
- **操作入口**：阶段相关的**按钮**（跳过单元 / 结束访谈 / 回退）+ 命令输入框（`/` 前缀，兼容 CLI 习惯）。

### 2.2 命令集与 API 映射

| 输入 / 控件 | 语义 | 映射 |
|-------------|------|------|
| `/help` 或 `?` | 帮助面板（本地渲染，可查 `query/commands`） | 前端本地 |
| `/status` | 状态面板：当前单元、已完成单元、取值树摘要 | `query/status` |
| `/view [路径]` | 取值树视图（可折叠） | `query/tree` |
| 按钮「跳过单元」 | 跳过当前单元（强制转录，进入下一单元） | `command/skip` |
| 按钮「结束访谈」 | 结束访谈阶段，进入全文档审核 | `command/finish` |
| 确认面板 应用 / 拒绝 | 润色阶段确认 / 拒绝高风险提案（可批量） | `proposal/respond` |
| 按钮「回退」 | 回退最近一次转录 / 转换 | `command/undo` |
| 按钮「退出」 | 退出（保存 checkpoint） | `session/quit` |

### 2.3 流式输出

- 对话流按 `message_id` 增量渲染 `session/message`（2031 chunk）；2032（complete）提供全量文本（复制按钮）。

### 2.4 中断与退出语义

- **无 SIGINT**：以「停止」按钮打断当前流式输出（局部行为）；不做强制退出语义。
- **关闭标签页**：`beforeunload` 时发送 `session/quit`；即使未发出，会话状态也已在引擎侧持续存在（checkpoint 自动保存），可在会话列表中恢复续聊。
- **断线**：自动重连（见 §5），重连期间输入暂存、恢复后补发。

## 3. 视图结构

| 视图 | 内容 | 数据来源 |
|------|------|---------|
| 会话列表 | 历史会话 + 新建（模板选择） | `session/list` → `session/create` |
| 访谈视图 | 对话流、当前单元提示、输入区、操作按钮 | 事件流 + `query/status` |
| 取值树 | 可折叠树形浏览（路径寻址） | `tree/changed` → `query/tree` |
| 润色确认 | 高风险提案卡片，应用 / 拒绝批量操作——自然语言描述来自 `session/message`，`proposal_ids` 来自 `session/status` 的 `waiting` | `session/message` + `session/status` → `proposal/respond` |
| 文档预览 | Markdown 渲染 + 下载 / 复制 | `prd/changed` → `query/prd` |

## 4. 必须处理的事件 → 行为

| 事件 | 行为 |
|------|------|
| `session/status`（首条全字段） | 进入访谈视图，恢复挂起状态 |
| `session/message`（2031 / 2032） | 流式渲染（增量追加；2032 提供全量文本供复制） |
| `session/await_input` | 激活输入区（`waiting.kind=interview`）；`waiting.kind=proposal` 切换确认面板 |
| `session/status`（`waiting` 含 `proposal_ids`） | 结合 `session/message` 自然语言提案描述，批量应用 / 拒绝后 `proposal/respond` |
| `tree/changed` | 刷新取值树视图（`query/tree`） |
| `session/status`（`current_unit` / `units_done` / `stage` 等字段） | 刷新进度与当前单元 |
| `gaps/changed` | 缺口清单展示（按需 `query/gaps`；自然语言结论已随 `session/message` 显示） |
| `conversions/changed` | 转换日志面板（`query/conversions`） |
| `prd/changed` | 文档预览 + 导出（`query/prd`） |
| `session/status`（`ended` 字段） | 返回会话列表 |
| `log` | toast 展示（按 `status_code` 等级） |

## 5. 连接与会话管理

- **多标签页**：同一会话被多个连接订阅 → 事件广播到全部订阅连接（[WEBSOCKET.md](../api/WEBSOCKET.md) §3）；前端不做本地状态推断，全部以事件为准。
- **断线重连**：指数退避（1s→30s 上限）；重连成功后对活跃会话逐个 `session/resume`；重连期间输入由前端暂存补发（[WEBSOCKET.md](../api/WEBSOCKET.md) §5）。
- **健康检查**：启动 / 重连前 `GET /healthz` 探测后端可用性。

## 6. 与 CLI / TUI 的差异

仅传输（WebSocket vs in-process / stdio）与渲染形态不同；协议调用、事件处理、错误语义完全一致。额外义务：连接管理（重连、多标签页）、健康检查；无终端信号语义，用按钮替代。
