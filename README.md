# YesPM - AI 驱动的 PRD 文档生成器

基于 LangGraph 构建的智能 PRD（产品需求文档）撰写系统，以自然对话访谈采集需求，经全文档审核与渲染润色，自动产出高质量产品需求文档。

> 实现基于 [V2 架构设计](docs/ARCHITECTURE_V2.md) 与 [模板数据结构规范](docs/TEMPLATE_SPEC.md)。

## 工作流程

```
用户简述
   │
   ▼
① 访谈阶段          interview_agent ↔ 用户（多轮自然对话访谈）
                    → unit_review_agent（单元成熟度评审）
                    → transcribe_agent（对话 → 结构化取值树）
   │
   ▼
② 全文档审核阶段    document_review_agent（完整性 / 跨章节一致性 / 逻辑自洽）
   │  不通过 → 缺口清单回灌访谈阶段（最多 N 轮）
   ▼
③ 渲染润色阶段      确定性渲染 → 生成器-评估器润色 → 最终 Markdown
```

| 阶段 | 执行者 | 职责 |
|------|--------|------|
| ① 访谈 | `interview_agent` / `unit_review_agent` / `transcribe_agent` | 按 tier 划分访谈单元，自然对话访谈 → 成熟度评审 → 修订式转录，产出与模板同构的取值树 |
| ② 全文档审核 | `document_review_agent` | 文档级审核（完整性 / 跨章节一致性 / 逻辑自洽），产出缺口清单回灌访谈阶段 |
| ③ 渲染润色 | 确定性渲染器 + 转换生成器 / 保真评估器 | 基线确定性渲染，对表示转换提案做风险分级执行，输出最终 Markdown + 转换日志 |

- **缺口清单**（gap list）是全流程唯一的反馈载体，单元级评审与全文档审核共用同一套回灌循环。
- **交互层**为 REPL 会话模式：用户随时可输入任何内容（回复、补充、命令），支持 `/skip` `/finish` `/status` `/undo` 等命令与断点续聊。

## 快速开始

### 1. 创建 Conda 环境并安装依赖

```powershell
conda create -n yespm python=3.12 -y
conda activate yespm
pip install -r requirements.txt
```

### 2. 配置 API Key

复制 `.env.example` 为 `.env`，填入 OpenAI 兼容 API 配置（DeepSeek / OpenAI / 本地网关均可）：

```powershell
copy .env.example .env
```

### 3. 运行

```powershell
python -m src.cli new "面向小企业的记账 SaaS，主打自动对账"   # 新建会话并开始访谈
python -m src.cli list                                        # 列出会话
python -m src.cli resume <会话id>                              # 恢复续聊
```

无 API Key 时可用内置 mock 模型体验全流程（确定性行为，用于测试/演示）：

```powershell
$env:YESPM_MOCK = "1"
python -m src.cli new "一个测试产品"
```

最终 PRD 输出到 `prd_output.md`，并在会话结束时打印；转换日志与「审核未决清单」（若有）附于文末。

## REPL 命令

| 命令 | 语义 |
|------|------|
| `/help` | 显示命令列表与当前可用操作 |
| `/status` | 当前单元、已完成单元、取值树摘要 |
| `/view [路径]` | 查看取值树（全部或指定路径） |
| `/skip` | 跳过当前单元（强制转录，进入下一单元） |
| `/finish` | 结束访谈阶段，进入全文档审核 |
| `/y` `/n` `/ya` `/na` | 润色阶段确认/拒绝高风险转换提案（ya/na 批量处理） |
| `/undo` | 回退最近一次转录/转换 |
| `/paste` | 粘贴模式（多行输入，空行结束） |
| `/quit` | 退出（保存 checkpoint，可恢复续聊） |

## 项目结构

```
YesPM/
├── src/
│   ├── cli.py                  # CLI 入口（new / resume / list）
│   ├── repl.py                 # REPL 会话层（输入路由 / 命令 / 断点续聊驱动）
│   ├── graph.py                # 主图：访谈阶段单元循环（interrupt / 缺口驱动）
│   ├── pipeline.py             # Node ②③：P2 兜底推断 / 审核回灌循环 / 润色管线
│   ├── state.py                # PRDState 通道定义
│   ├── nodes/                  # 具名 agent：interview / unit_review / transcribe / document_review / polish
│   ├── tools/                  # template_loader（Pydantic 校验）/ value_tree（取值树）/ units（tier 拆分）/ renderer / llm
│   ├── prompts/                # 各 agent 提示词
│   └── templates/
│       └── default_prd.yaml    # 默认 PRD 模板（唯一真源，可自定义）
├── tests/                      # pytest 全流程测试（MockLLM，无需 API Key）
├── docs/                       # V2 架构设计 + 模板规范
├── .env.example
└── requirements.txt
```

## 技术栈

- **LangGraph**: 工作流编排（interrupt 人工介入、checkpoint 断点续聊原生支持）
- **SqliteSaver**: 会话状态持久化（`sqlite3` 标准库，零新增依赖）
- **LLM**: 各具名 agent（interview / unit_review / transcribe / document_review / 润色生成器与保真评估器）均由 LLM 驱动（OpenAI 兼容接口）

## 测试

```powershell
python -m pytest tests -q
```

测试使用确定性 MockLLM（`YESPM_MOCK=1`），覆盖模板校验、寻址、tier 拆分、渲染与全流程 e2e（含 /skip /finish /undo、断点续聊、审核回灌与未决清单）。
