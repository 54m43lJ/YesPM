# CLI 前端（Python）

> 契约见 [api/PROTOCOL.md](../api/PROTOCOL.md)。纯 CLI 是**参考实现**：最薄、最先交付，验证协议完备性后 TUI / Web 按同一契约实现。交互定义（本节）初期与 TUI / Web 一致，此后允许各自演变。

## 1. 定位

- 与后端同仓库、同包交付，命令 `yespm`。
- 传输：**进程内绑定**——CLI 通过协议层的 in-process Transport 直连引擎，但**消息与 stdio / WebSocket 完全一致**（不绕过协议，见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) 约束 2）。
- 职责：交互解释与包装——把用户输入翻译为方法调用、把事件渲染为终端输出，零业务逻辑。

## 2. 交互定义

### 2.1 输入方式

- **提示符带上下文**：依据 `query/status` 的 `current_unit` 渲染，如 `PRD [3.2 核心功能 1]>`；无当前单元时 `PRD>`。
- **多行输入**：空行结束输入；长内容用 `/paste` 粘贴模式（多行原样采集，结束后一次 `input/send`）。
- **输入路由**（CLI 本地，后端不感知）：`/` 前缀 → 命令解析；其余 → `input/send`。

### 2.2 命令集与 API 映射

| 命令 | 语义 | 映射 |
|------|------|------|
| `/help` | 显示命令列表与当前可用操作（本地渲染，可查 `query/commands`） | 前端本地 |
| `/status` | 当前单元、已完成单元、取值树摘要 | `query/status` |
| `/view [路径]` | 查看取值树（全部或指定路径） | `query/tree` |
| `/skip` | 跳过当前单元（强制转录，进入下一单元） | `command/skip` |
| `/finish` | 结束访谈阶段，进入全文档审核 | `command/finish` |
| `/y` `/n` `/confirm` | 润色阶段确认 / 拒绝高风险提案 | `proposal/respond` |
| `/undo` | 回退最近一次转录 / 转换 | `command/undo` |
| `/quit` | 退出（保存 checkpoint） | `session/quit` |
| `/paste` | 粘贴模式（多行输入） | 前端本地 |
| `/resume <id>` | 恢复历史会话（配合启动时的 `session/list`） | `session/resume` |

### 2.3 流式输出

- 按 `message_id` 拼接 `message/chunk` 增量打印；`message/complete` 落盘副本（供复制 / 导出）。

### 2.4 中断与退出语义

- **Ctrl+C 两级**：第一次中断当前流式输出（等待中的话）；再次按强制退出（先发 `session/quit`，确认保存后退出）。
- **EOF / 关闭**：直接退出（引擎侧 checkpoint 已自动保存，会话可恢复）。

## 3. 必须处理的事件

| 事件 | 行为 |
|------|------|
| `session/ready` | 打印会话信息与恢复后的挂起状态 |
| `node/entered` / `node/exited` | 打印阶段切换（如 `—— 全文档审核 ——`） |
| `message/chunk` / `message/complete` | 流式渲染 agent 输出 |
| `input/required` | 更新提示符上下文，进入等待输入态 |
| `proposal/pending` | 逐条打印提案（位置 / 原形态 / 目标形态 / 风险），等待 `y/n` |
| `document/reviewed` | 打印审核结论与缺口清单 |
| `polish/progress` | 打印转换日志 |
| `prd/rendered` | 打印最终文档（或写文件），附转换日志与未决清单 |
| `session/ended` | 退出主循环 |
| `error` / `log` | 打印（`log` 到 stderr） |

## 4. 与后端的分界

- CLI 代码只依赖 `protocol/` 客户端侧（in-process Transport），不 import `engine/` 内部。
- 所有语义决策（操作可用性、输入暂存、中断）都在引擎侧；CLI 对不可用操作直接展示后端错误（1003 等）。
