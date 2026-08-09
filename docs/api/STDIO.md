# stdio 传输绑定（STDIO / V1）

> [PROTOCOL.md](./PROTOCOL.md) 定义语义层；本文档定义其 stdio 绑定。适用：TUI（TypeScript）等本地子进程前端。纯 CLI（Python）使用进程内绑定，不经过本传输（见 [frontends/CLI.md](../frontends/CLI.md)）。

## 1. 进程

- 命令：`yespm-server`（由后端 Python 包提供；Python 3.12，Conda 环境 `yespm`）
- 定位：**无 UI 桥**——把协议消息在 stdin/stdout 与引擎之间搬运，自身不实现任何前端逻辑

### 启动参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--db <path>` | `./yespm.db` | SQLite checkpoint 路径（SqliteSaver，见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md)） |
| `--template <path>` | 内置模板 | 模板 YAML 路径 |
| `--config <path>` | `.env` | LLM 等配置 |
| `--version` | — | 打印版本后退出 |

## 2. 帧格式（JSON Lines）

- stdin（client → server）与 stdout（server → client）各一行一个完整 JSON-RPC 消息。
- 消息内文本一律按 JSON 转义（`\n`），**消息本身不换行**。
- 编码 UTF-8。

### 通道纪律

| 通道 | 内容 |
|------|------|
| stdin | 请求（唯一） |
| stdout | 响应 + 事件通知（唯一） |
| stderr | 日志、进程级错误、崩溃栈（`log` 事件的落点；等级由 `status_code` 千位决定，见 [PROTOCOL.md](./PROTOCOL.md) §11） |

> stdout 只允许出现协议消息，任何日志写入 stdout 均视为协议破坏。

## 3. 启动握手

子进程启动完成（引擎就绪、checkpoint 打开）后，服务器向 stdout 推送一条传输级通知，供父进程判断可发消息：

```json
{"jsonrpc": "2.0", "method": "server/ready", "params": {"pid": 1234, "version": "0.3.0", "db": "C:\\...\\yespm.db"}}
```

`server/ready` 为**传输级**握手，不属于 PROTOCOL 语义层事件目录；父进程必须在收到它之后才开始发送请求。

## 4. 关闭语义

| 触发 | 行为 |
|------|------|
| 客户端关闭 stdin（EOF） | 服务器优雅保存后退出（退出码 0） |
| `session/quit` | 保存 checkpoint，推送 `session/status`（`fields` 含 `ended`），进程退出（退出码 0） |
| `SIGINT` / `SIGTERM` | 保存 checkpoint 后退出（退出码 130 / 143 约定） |
| 致命错误 | stderr 输出错误、非 0 退出码 |

## 5. 子进程管理（TUI 侧）

1. **spawn**：`yespm-server --db <path>`，等待 `server/ready` 后建立协议客户端。
2. **事件分发**：逐行解析 stdout，响应按 `id` 回调，通知按 `method` 分发。
3. **请求超时**：命令类请求无超时语义（处理结果经事件回流）；请求级响应应在可接受时间内返回（实现取数秒级上限）。
4. **崩溃恢复**：子进程意外退出 → 前端提示，重新 spawn 后 `session/resume` 续聊（checkpoint 已持久化，见 [PROTOCOL.md](./PROTOCOL.md) 10.3）。
5. **防孤儿**：前端退出时必须结束子进程（stdin EOF 或 terminate）。

## 6. 与 WebSocket 绑定的等价性

同一条协议消息在两种传输下逐字节一致；绑定差异仅为帧化方式与进程/连接生命周期。前端实现共享同一协议客户端核心，仅替换传输适配器。
