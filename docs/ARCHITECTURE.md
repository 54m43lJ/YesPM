# YesPM 架构设计文档

## 整体架构

采用 LangGraph 工作流引擎，将 PRD 撰写过程建模为有向图，通过 3 个核心节点协同完成文档生成。

```
┌─────────────────────────────────────────────────┐
│                  User Input (初始简述)            │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │ ① draft_prd                   │  模板驱动的引导式问答（混合粒度）
          │  澄清 + 按模板填写（合并）     │  借助模板逐章节问答填空
          └──────────────┬───────────────┘
                         │ prd_draft（取值树）
                         ▼
          ┌──────────────────────────────┐
          │ ② review_prd                  │  基于结构化数据的字段级审核
          │  完整性 / 一致性 / 可行性      │  产出 failed_fields（路径级原因）
          └──────────────┬───────────────┘
                    ┌────┴────┐
                    │  通过？  │
                    └────┬────┘
            否 ─────────┘   └───────── 是
            ▼                              ▼
┌────────────────────────┐   ┌──────────────────────────┐
│ 回到 ① draft_prd        │   │ ③ finalize_prd            │
│ 仅追问 failed_fields    │   │ 遍历取值树确定性渲染       │
│ 对应字段（含 interrupt）│   │ → Markdown / PDF          │
└────────────────────────┘   └──────────────────────────┘
[演进目标：失败字段先经 auto_fix_hook 自动修正，无法解决再回问用户]
```

## 核心组件

### 1. State 定义 (`prd_state.py`)

工作流的共享状态，在各节点间传递：

| 字段 | 类型 | 说明 |
|------|------|------|
| `messages` | `list` | 对话历史 |
| `initial_brief` | `str` | 用户初始简述 |
| `template` | `list[TemplateNode]` | 模板树（启动时加载一次） |
| `prd_draft` | `ValueTree` | 中间态：与模板同构的取值树 |
| `filled_paths` | `set[str]` | 已填字段的位置路径 |
| `pending_paths` | `list[str]` | 待问字段（含审核失败回填） |
| `review_result` | `dict` | 审核评分 + 明细 |
| `review_passed` | `bool` | 审核是否通过 |
| `failed_fields` | `list[dict]` | 字段级失败原因 `[{path, dimension, reason}]` |
| `final_prd` | `str` | 最终 PRD（Markdown） |
| `iteration_count` | `int` | review → draft 往返次数 |

### 2. 模板与数据结构

模板是全流程的**唯一真源**，由 YAML 定义，同时驱动问答与渲染：

- 数据结构契约见 [`TEMPLATE_SPEC.md`](./TEMPLATE_SPEC.md)。
- 出厂默认模板见 [`../prompts/template_schema.yaml`](../prompts/template_schema.yaml)。
- 树状递归结构，无深度限制；节点类型（`group` / `repeat` / `field`）由字段存在性推断，无显式 `id` / `type`。
- 取值树 `prd_draft` 与模板同构：`field` 叶子持值或空，`repeat` 展开为实例数组。

### 3. 节点说明

#### ① draft_prd - 澄清与撰写节点（合并原 define + draft）

- **输入**: 用户初始简述 + 模板树
- **处理**: 模板驱动的引导式问答，混合粒度
  - 按位置遍历模板，依字段有效 tier 决定提问方式：
    - `P0` 逐字段深问，必要时多轮追问
    - `P1` 按章节聚合，一次性批量提问
    - `P2` 不主动提问，节点末尾由 LLM 基于上下文推断填入
  - 每轮：LLM 依 field 的 `question` / `example` 生成提问 → `interrupt` 等待回复 → LLM 结构化抽取值 → 写入取值树对应位置路径
  - `repeat` 子树：先问实例数量，再对每个实例套用子模板
- **输出**: 取值树 `prd_draft`（更新 `filled_paths`）
- **人工介入**: LangGraph `interrupt` 实现每轮 human-in-the-loop
- **LLM Prompt**: `prompts/draft.txt`

#### ② review_prd - 审核节点

- **输入**: 取值树 `prd_draft`
- **处理**: 基于结构化数据的字段级审核
  - **完整性**: 遍历模板，凡 `required=true` 而取值树对应位置路径为空 → 标记失败
  - **一致性**（规则 + LLM）: repeat 间引用一致性，例如用户故事角色 ∈ 目标用户角色；P0 功能清单均有对应核心功能详述
  - **可行性**: LLM 整体判分
- **输出**: `failed_fields`（路径级原因）+ `review_passed`
- **LLM Prompt**: `prompts/review.txt`

#### ③ finalize_prd - 渲染节点

- **输入**: 通过审核的取值树
- **处理**: 渲染器遍历取值树**确定性**生成 Markdown（规则见 [`TEMPLATE_SPEC.md`](./TEMPLATE_SPEC.md) 渲染规则一节）
- **输出**: `final_prd`（Markdown，可导出 PDF）
- 渲染纯程序性、可重现，不依赖 LLM 重写
- 渲染规则：
  - 按位置路径深度生成标题层级（# / ## / ### …）。
  - group / repeat / instance 仅产出标题；field 按 field_type 产出正文。
  - text 输出段落；enum 输出所选项；table 输出 Markdown 表格。
  - 必填未填字段输出占位符「（待补充）」，非必填空字段跳过。
  - 若审核未完全通过，文末附「审核未决清单」。

### 4. 路由逻辑

- `draft_prd` → `review_prd`: 取值树填充完成后进入审核
- `review_prd` → `finalize_prd`: 审核通过则渲染输出
- `review_prd` → `draft_prd`: 审核未通过则返回，`pending_paths` 替换为 `failed_fields` 对应路径，**仅追问失败字段**，不重头问
- 最大往返次数（默认 3 次）防死循环；超限则强制进入 `finalize_prd` 并附审核未决清单

### 5. 演进钩子：auto_fix_hook

当前审核失败一律回问用户。预留扩展点：`review_prd` 产出 `failed_fields` 后先经 `auto_fix_hook`，由 LLM 尝试自动修正，能修的从失败清单移除并直接 patch 进取值树；无法解决的才回问用户。实现 `auto_fix_hook` 即可渐进升级，主流程不变。

```
review_prd 产出 failed_fields
        │
        ▼
auto_fix_hook (当前 no-op) ── 能修 → patch 取值树
        │
        ▼ 无法修
回问用户（pending_paths）
```

## 扩展性

- **模板切换**: 替换或编辑 YAML 即可适配不同业务场景，只要符合 [`TEMPLATE_SPEC.md`](./TEMPLATE_SPEC.md) 契约
- **节点扩展**: 可在流程中插入新节点（如竞品分析、技术方案评估）
- **模型切换**: 通过环境变量切换 OpenAI / Azure / 本地模型
- **人工审核**: draft / review 节点均支持 `interrupt` 断点
