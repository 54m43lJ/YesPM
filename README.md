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

> 澄清与按模板填写合并为单节点 `draft_prd`；中间态为与模板同构的取值树（非纯文本）。详见 [架构设计](docs/ARCHITECTURE_V2.md)。

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
