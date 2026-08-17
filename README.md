# YesPM - AI 驱动的 PRD 文档生成器

基于 LangGraph 构建的智能 PRD（产品需求文档）撰写 Agent，通过多阶段协作自动产出高质量产品需求文档。

## 工作流程

```
用户简述 → ① 访谈（interview ↔ 用户 → 单元评审 → 转录取值树）
         → ② 全文档审核（不通过 → 缺口清单 → 回 ①；通过 ↓）
         → ③ 确定性渲染 → 润色（polish ↔ fidelity 评估）→ 最终 Markdown
```

| 阶段 | 节点 | 职责 |
|------|------|------|
| ① | `interview_agent` / `unit_review_agent` / `transcribe_agent` | 按模板单元划分（tier）自然对话访谈，逐单元成熟度评审，转录为与模板同构的取值树 |
| ② | `document_review_agent` | 全文档审核（完整性 / 跨章节一致性 / 逻辑自洽）；不通过时产出缺口清单回灌访谈 |
| ③ | 确定性渲染 + `polish_agent` ↔ `fidelity_evaluation_agent` | 程序性渲染基线 Markdown，再分级润色（低风险自动 / 高风险逐条确认），输出最终文档 |

> 全流程中间态为与模板同构的取值树（非纯文本）。流程与设计原则详见 [架构设计](docs/ARCHITECTURE.md) 与 [后端架构](docs/backend/ARCHITECTURE.md)。

## 环境准备

### 用户路径：发布包 + 安装脚本

1. 下载发布包 `yespm-<版本>-win64.zip` 并解压（下载即用，无需预装 Python / Conda / Rust；交付要求见 [deliverable/TUI.md](docs/deliverable/TUI.md)、[deliverable/BACKEND.md](docs/deliverable/BACKEND.md)）。
2. 运行安装脚本（自动按 uv → conda → venv → PATH 顺序准备后端环境；检测不到 Python 工具链时会弹窗引导）：

```powershell
.\install.ps1
```

3. 配置 API Key：复制 `.env.example` 为 `.env`，填入你的 OpenAI API Key。

4. 运行：

```powershell
yespm
```

### 开发者路径：Conda 环境

### 1. 创建 Conda 环境

```powershell
conda create -n yespm python=3.12 -y
conda activate yespm
```

### 2. 安装依赖

```powershell
pip install -r requirements.txt
```

### 3. 配置 API Key

复制 `.env.example` 为 `.env`，填入你的 OpenAI API Key：

```powershell
copy .env.example .env
```

### 4. 运行

主入口为 TUI（Rust 二进制，spawn 后端 stdio 桥）：

```powershell
yespm
```

高级用法：纯 CLI（Python，脚本化 / 无交互环境）：

```powershell
yespm-cli
```

#### 前端

| 前端 | 二进制 | 语言 | 定位 | 传输 |
|------|--------|------|------|------|
| TUI | `yespm` | Rust | **主入口** | spawn `yespm-server`（stdio） |
| CLI | `yespm-cli` | Python | 高级用法 | 进程内 |
| Web | — | JS/TS | 浏览器前端（占位） | WebSocket（`yespm-ws`） |
| Desktop | `yespm-desktop` | WebView 包装 Web | 桌面壳层（占位） | WebSocket（`yespm-ws`） |

##### TUI（主入口，占位）

- 依赖：Rust 工具链（stable）。
- 编译：`cargo build --release`（`crates/yespm-tui`）。
- 运行：`yespm`，自动发现 `yespm-server`（uv → conda → venv → PATH，可 `--server` 覆盖）。
- 文档：[frontends/TUI.md](docs/frontends/TUI.md)、[deliverable/TUI.md](docs/deliverable/TUI.md)。

##### Desktop（占位）

- 形态：WebView 包装 Web 前端，复用其界面与逻辑。
- 依赖 / 编译：未定（Tauri / Electron 候选）。
- 文档：[frontends/DESKTOP.md](docs/frontends/DESKTOP.md)。

后端进程入口（详见 [后端架构](docs/backend/ARCHITECTURE.md)）：

| 命令 | 传输 | 使用者 |
|------|------|--------|
| `yespm-cli` | 进程内 | CLI（Python） |
| `yespm-server` | stdio | TUI |
| `yespm-ws` | WebSocket | Web / Desktop |

## 技术栈

- **LangGraph**: 工作流编排
- **LangChain**: LLM 调用抽象
- **OpenAI API**: 大语言模型（支持兼容接口切换）

## 前置条件

- Python 3.12+
- Anaconda / Miniconda
- Rust 工具链（构建 TUI 二进制 `yespm` 所需；使用发布产物可免）
- OpenAI API Key（或兼容的 LLM 服务）
