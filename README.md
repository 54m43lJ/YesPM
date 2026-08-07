# YesPM - AI 驱动的 PRD 文档生成器

基于 LangGraph 构建的智能 PRD（产品需求文档）撰写 Agent，通过多阶段协作自动产出高质量产品需求文档。

## 工作流程

```
用户输入 → ① draft_prd（模板驱动问答）→ ② review_prd（字段级审核）→ ③ finalize_prd（渲染输出）
```

| 阶段 | 节点 | 职责 |
|------|------|------|
| ① | `draft_prd` | 借助模板通过引导式问答（混合粒度）澄清并填写，产出结构化取值树 |
| ② | `review_prd` | 按字段维度（完整性 / 一致性 / 可行性）审核取值树，产出字段级失败清单 |
| ③ | `finalize_prd` | 遍历取值树确定性渲染为 Markdown |

> 澄清与按模板填写合并为单节点 `draft_prd`；中间态为与模板同构的取值树（非纯文本）。详见 [架构设计](docs/ARCHITECTURE.md)。

## 项目结构

```
YesPM/
├── src/
│   ├── nodes/                # LangGraph 节点实现
│   │   ├── draft.py          # 模板驱动问答节点（澄清+填写合一）
│   │   ├── review.py         # 字段级审核节点
│   │   └── finalize.py       # 渲染输出节点
│   ├── state/                # 状态定义
│   │   └── prd_state.py      # PRDState 与取值树
│   ├── tools/                # 工具函数
│   │   ├── template_loader.py  # 加载 + Pydantic 校验模板 YAML
│   │   └── renderer.py         # 取值树 → Markdown 渲染器
│   └── graph.py              # LangGraph 图定义与路由
├── prompts/                  # Prompt 与模板
│   ├── template_schema.yaml  # 默认 PRD 模板（问答与渲染的唯一真源）
│   ├── draft.txt
│   ├── review.txt
│   └── finalize.txt
├── docs/
│   ├── ARCHITECTURE.md       # 架构设计文档
│   └── TEMPLATE_SPEC.md      # 模板数据结构规范（自定义模板契约）
├── .env.example              # 环境变量示例
├── requirements.txt          # Python 依赖
└── README.md
```

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
python src/graph.py
```

## 技术栈

- **LangGraph**: 工作流编排
- **LangChain**: LLM 调用抽象
- **OpenAI API**: 大语言模型（支持兼容接口切换）

## 前置条件

- Python 3.12+
- Anaconda / Miniconda
- OpenAI API Key（或兼容的 LLM 服务）
