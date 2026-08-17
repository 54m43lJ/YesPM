# 后端交付（需求与验收）

> 面向后端实现者：本文档逐项列出可交付功能、验收标准与里程碑，需求来源均引用项目文档（引擎与工作流见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md)，语义层契约见 [api/PROTOCOL.md](../api/PROTOCOL.md)，传输绑定见 [api/STDIO.md](../api/STDIO.md) / [api/WEBSOCKET.md](../api/WEBSOCKET.md)，测试策略见 [TEST-DRIVEN-DEVELOPMENT.md](../TEST-DRIVEN-DEVELOPMENT.md)）。本文档只对**交付一个可运行的后端**负责（无头引擎 + 协议层 + 传输绑定 + 持久化 + 安装），不涉及任何前端实现。

## 1. 范围与定位

| 项 | 内容 |
|----|------|
| 交付物 | Python 包 `yespm_backend`：无头会话引擎 + 协议层 + 传输绑定（in-process / stdio / WebSocket）+ checkpoint 持久化 |
| 入口 | `yespm-server`（stdio 桥）/ `yespm-ws`（WebSocket 服务）/ `yespm-cli`（进程内入口） |
| 运行要求 | Python 3.12；依赖清单 `requirements.txt`（选型结论见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) §4） |
| 不在范围 | 任何前端实现与前端集成；模板编写（二次开发，见 [TEMPLATE_SPEC.md](../TEMPLATE_SPEC.md)） |
| 分层依据 | 引擎只理解 API（[backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) §1）；对外契约唯一来源为 [PROTOCOL.md](../api/PROTOCOL.md)，本文档不重复契约细节 |

## 2. 技术栈与工程结构

- 技术栈结论（选型理由见 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) §4）：LangGraph 编排、SqliteSaver 持久化（SQLite 标准库）、大载荷文件存储、`websockets`（仅 WebSocket 服务）。
- 模块结构（唯一来源 [backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) §5）：`engine/` `graph/` `protocol/` `entry/`，此处不重复。
- 进程形态（[backend/ARCHITECTURE.md](../backend/ARCHITECTURE.md) §6）：一个引擎、三个壳（进程内 / stdio / WebSocket）。

## 3. 需求分段与里程碑

每个里程碑交付物独立可检查（检查方式见段末「检查」与 §4 总表）；验收标准逐条对应上表需求。

### M1 引擎与三阶段工作流

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| B1 | 会话状态机与 API 方法分发（阶段 / 等待 / 忙；方法 → 引擎操作；无命令文本路由） | 状态迁移测试覆盖；`input/send` 文本零语义（原样转发） | backend §1 / §3；PROTOCOL §2 |
| B2 | 访谈阶段：单元循环（tier 划分 / 评审触发 / 修订式转录 / 上下文隔离 / 无关输入承接） | 节点级测试（interview / unit_review / transcribe）+ TEST-DRIVEN S01 / S02 / S05 | backend §2.1 |
| B3 | 全文档审核：审核维度 / gap 归并 / 回灌闭环 / N 轮兜底 / 未决清单 | 节点级测试（document_review）+ TEST-DRIVEN S04 / S05 | backend §2.2 |
| B4 | 渲染与润色：确定性基线 / polish ↔ fidelity 迭代 / 风险分级 / 转换日志 / 转换禁区 | 节点级测试（渲染器 / polish / fidelity）+ TEST-DRIVEN S06 / S07 / S08 | backend §2.3 |
| B5 | 中断管理与输入暂存（interrupt 挂起 / pending_queue 回放 / 非等待期输入不丢弃） | 等待 / 忙态测试；`queued: true` 输入在下一中断点正确回放 | backend §3；PROTOCOL §4.2 / §8 |

**里程碑交付物**：三阶段工作流端到端跑通（LLM 已配置）+ 节点级测试全绿。
**检查**：`pytest`（节点级）+ TEST-DRIVEN S01–S05 走查。

### M2 协议与事件

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| B6 | JSON-RPC 信封与错误码映射（请求 / 响应 / 通知；error 码 46xx / 4xxx） | 协议往返测试：非法 JSON 4601、无效请求 4602、方法不存在 4603、参数无效 4604 | PROTOCOL §2 / §11 |
| B7 | 方法目录全量实现（§4.1–§4.4：生命周期 / 交互 / 查询 / 配置） | 逐方法契约测试：参数校验、结果形状、错误码（4001 / 4102 / 4103 / 4404） | PROTOCOL §4 |
| B8 | 事件发布（4 通用模板 + 4 大载荷信号；fields 白名单；revision 单调递增；大载荷禁入 status） | 事件序列断言：创建首推全字段、`ended` 推送、大载荷仅走成对通道 | PROTOCOL §5 |
| B9 | 流式输出与并发语义（2031 → 2032 顺序 / 单会话串行 4005 / 跨会话并行 / 多连接广播） | 并发测试：busy 期间 `input/send` 暂存、其余请求 4005 | PROTOCOL §6 / §8 |

**里程碑交付物**：协议层契约测试全绿（经 stdio 驱动）。
**检查**：协议级自动测试。

### M3 传输绑定

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| B10 | stdio 绑定（JSON Lines 帧 / 通道纪律 / `server/ready` 握手 / EOF / SIGINT / SIGTERM 关闭语义与退出码） | STDIO §2–§4 逐条测试（退出码 0 / 130 / 143；stdout 零污染） | STDIO §1–§4 |
| B11 | WebSocket 绑定（/healthz / /ws 主通道 / 多会话承载 / 断线保存 checkpoint / 多连接广播） | 连接级测试：握手、双会话、断开后 checkpoint 落盘、重连 resume | WEBSOCKET §2–§3 |
| B12 | in-process Transport（进程内形态与外部传输同一 JSON-RPC 消息） | 进程内往返与 stdio 消息逐字节一致 | PROTOCOL §3；backend §7 约束 2 |

**里程碑交付物**：三个传输绑定可用。
**检查**：绑定级自动测试 + 手工验证握手 / 关闭。

### M4 持久化与恢复

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| B13 | checkpoint 两级存储（SqliteSaver 小状态 / `data_dir` 大载荷 / 保存时机表） | 保存时机逐项验证（节点边界 / interrupt / quit / 断线 / 退出前） | backend「checkpointer 定义」 |
| B14 | `data_dir` 平台用户数据目录与 `--data-dir` 覆盖 | 默认落平台标准目录；覆盖生效；删除会话连数据目录一并删除 | backend「checkpointer 定义」 |
| B15 | resume / undo / delete（恢复快照 / 历史 checkpoint 回退 / 删除会话与数据 / 4001） | TEST-DRIVEN S09 / S11 / S12；kill -9 后恢复读回大载荷 | PROTOCOL §4.1 / §4.2 |

**里程碑交付物**：持久化全链路（创建 → 中断 → 退出 → 崩溃 → 恢复 → 删除）。
**检查**：TEST-DRIVEN S09 / S11 / S12 + 崩溃恢复手工验证。

### M5 配置、可靠性与安装

| # | 可交付功能 | 验收标准 | 来源 |
|---|-----------|---------|------|
| B16 | 启动参数与配置（`--db` / `--data-dir` / `--template` / `--config` / `--version`；`.env`；`config/get`；无 `config/set`） | 参数矩阵测试；`config/get` 返回齐全；配置变更重启生效 | STDIO §1；WEBSOCKET §1；PROTOCOL §4.4 |
| B17 | 错误分级与致命拒绝（5701 模板不合法 / 5802 配置错误 / 5901 / 5902 / 4803 重试耗尽） | 错误路径测试：启动阶段拒绝并定位、运行期可恢复 | PROTOCOL §11；backend §7 约束 4 |
| B18 | 后端安装与验证（环境发现 uv → conda → venv → PATH） | 按「后端安装」步骤在全新 Python 环境跑通；`yespm-server --version` 退出码 0 | 本文档 |

#### 后端安装（独立于任何前端）

环境发现优先级：**uv → conda → venv → PATH**（取第一个可用的执行）：

1. `uv` 可用：`uv venv` 创建环境 → `uv pip install -r requirements.txt`。
2. 无 `uv`、`conda` 可用：`conda create -n yespm python=3.12 -y` → `conda run -n yespm pip install -r requirements.txt`。
3. 无 `conda`：使用现有 venv 或经 PATH 上 Python 3.12 创建 `python -m venv` → `pip install -r requirements.txt`。
4. 无任何 Python 工具链：安装流程终止并指引（先安装 Python 3.12 后重试）。

验证：`yespm-server --version` 打印版本、退出码 0；启动后 stdout 收到 `server/ready`（[STDIO.md](../api/STDIO.md) §3）。

**里程碑交付物**：配置 / 错误分级 / 安装验证全项通过。
**检查**：参数与错误路径自动测试 + 全新环境安装走查。

## 4. 里程碑总表

| 里程碑 | 交付物 | 检查方式 |
|--------|--------|---------|
| M1 | 引擎与三阶段工作流 | `pytest` 节点级 + TEST-DRIVEN S01–S05 |
| M2 | 协议与事件 | 协议级自动测试 |
| M3 | 传输绑定（stdio / WebSocket / in-process） | 绑定级自动测试 + 手工 |
| M4 | 持久化与恢复 | TEST-DRIVEN S09 / S11 / S12 + 崩溃恢复 |
| M5 | 配置、可靠性与安装 | 自动测试 + 全新环境安装走查 |

## 5. 额外细节（占位）

- Windows 下信号语义：SIGINT / SIGTERM 模拟与退出码约定（130 / 143）实测（[STDIO.md](../api/STDIO.md) §4）。
- `.env` 配置：LLM API Key 缺失 → 5802 启动阶段拒绝（[PROTOCOL.md](../api/PROTOCOL.md) §11）。
- `--version` 输出与 `config/get` 的 `version` 字段一致。
