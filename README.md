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

```powershell
yespm
```

三个进程入口共享同一引擎（详见 [后端架构](docs/backend/ARCHITECTURE.md)）：

| 命令 | 传输 | 使用者 |
|------|------|--------|
| `yespm` | 进程内 | 纯 CLI（Python） |
| `yespm-server` | stdio | TUI（TypeScript） |
| `yespm-ws` | WebSocket | Web（JS/TS） |

## 技术栈

- **LangGraph**: 工作流编排
- **LangChain**: LLM 调用抽象
- **OpenAI API**: 大语言模型（支持兼容接口切换）

## 前置条件

- Python 3.12+
- Anaconda / Miniconda
- OpenAI API Key（或兼容的 LLM 服务）
