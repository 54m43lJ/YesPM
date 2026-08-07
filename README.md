# YesPM - AI 驱动的 PRD 文档生成器

基于 LangGraph 构建的智能 PRD（产品需求文档）撰写 Agent，通过多阶段协作自动产出高质量产品需求文档。

## 工作流程

```
用户输入 → ① 产品定义澄清 → ② 按模板填写 PRD → ③ 文档质量审核 → ④ 输出成熟 PRD
```

| 阶段 | 节点 | 职责 |
|------|------|------|
| ① | `define_product` | 通过与用户交互明确产品定位、目标用户、核心功能等关键信息 |
| ② | `draft_prd` | 基于预定义的 PRD 模板，将产品定义填充为结构化文档 |
| ③ | `review_prd` | 根据质量标准审核文档完整性、逻辑性和规范性 |
| ④ | `finalize_prd` | 整合审核意见，输出最终版 PRD 文档 |

## 项目结构

```
YesPM/
├── src/
│   ├── nodes/          # LangGraph 节点实现
│   │   ├── define.py       # 产品定义节点
│   │   ├── draft.py        # PRD 撰写节点
│   │   ├── review.py       # 审核节点
│   │   └── finalize.py     # 终稿节点
│   ├── state/          # 状态定义
│   │   └── prd_state.py    # PRD 工作流 State
│   ├── tools/          # 工具函数
│   │   └── ...
│   └── graph.py        # LangGraph 图定义与路由
├── prompts/            # Prompt 模板
│   ├── define.txt
│   ├── draft.txt
│   ├── review.txt
│   └── finalize.txt
├── docs/
│   ├── ARCHITECTURE.md     # 架构设计文档
│   └── PRD_TEMPLATE.md     # PRD 模板定义
├── .env.example            # 环境变量示例
├── requirements.txt        # Python 依赖
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
