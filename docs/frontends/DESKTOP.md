# 桌面前端（占位）

> 契约见 [api/PROTOCOL.md](../api/PROTOCOL.md)，传输见 [api/WEBSOCKET.md](../api/WEBSOCKET.md)。桌面前端（二进制 `yespm-desktop`）是 **Web 前端的 WebView 重新包装**（**占位**，技术选型未定）：复用 Web 前端的界面与逻辑，以桌面应用形态分发。交互定义与 [WEB.md](./WEB.md) 一致，差异仅为桌面壳层（窗口管理、系统集成、打包分发）。

## 1. 定位

- 进程形态：桌面壳层内嵌 WebView 加载 Web 前端构建产物，连接 `yespm-ws`（WebSocket 主通道 + `/healthz` 健康检查）。
- 技术选型（占位，实现时定）：Tauri / Electron。
- 交付：`yespm-desktop` 桌面应用，独立分发（占位：安装器 / 便携包，未定）。
- 职责：Web 前端的桌面包装——窗口、菜单、系统集成由壳层负责，业务交互全部来自 Web 前端。

## 2. 与 WEB 前端的关系

| 层次 | 归属 |
|------|------|
| 界面 / 交互 / 协议逻辑 | Web 前端（[WEB.md](./WEB.md)），桌面直接复用 |
| 桌面壳层（窗口 / 菜单 / 打包） | 桌面前端（本文档） |

- 无独立业务逻辑：Web 前端的演进自动反映到桌面端。
- 若壳层技术限制导致分叉（如离线存储、系统通知），差异记录于本节。

## 3. 视图 / 事件 / 生命周期

与 [WEB.md](./WEB.md) 一致（复用同一实现）；额外义务：窗口生命周期——关闭窗口时发送 `session/quit`（尽力而为；即使未发出，会话状态也已在引擎侧持续存在，checkpoint 自动保存，可恢复续聊）。

## 4. 与 CLI / TUI 的差异

传输（WebSocket vs in-process / stdio）与渲染形态（桌面窗口 vs 终端）不同；协议调用、事件处理、错误语义遵循同一契约（[PROTOCOL.md](../api/PROTOCOL.md)）。
