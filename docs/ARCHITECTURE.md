# YesPM 架构设计文档

## 整体架构

采用 LangGraph 工作流引擎，将 PRD 撰写过程建模为有向图，通过 5 个核心节点协同完成文档生成。

```
┌─────────────────────────────────────────────────┐
│                  User Input (初始简述)            │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────────────────────────┐
          │ ① draft_prd                                   │  章节对话会话（逐轮审核循环）
          │  逐章节以对话方式讨论细节，讨论成熟后总结为章节   │  依 tier 决定讨论深度
          └──────────────────┬───────────────────────────┘
                             │ 单轮用户回复（interrupt）
                             ▼
          ┌──────────────────────────────────────────────┐
          │ ①′ audit_input（阶段内审核 agent）             │  判定本轮回复是否达到
          │  内容达标判定 + 欠缺原因反馈                    │  章节要求
          └──────┬───────────────────────────────┬───────┘
           未达标 ┘                               └─ 达标
          ▼                                        ▼
  回到 ① 反馈注入对话，自然追问               继续对话 / 章节成熟后抽取写入
                                                  │ prd_draft（取值树）
                                                  ▼
          ┌──────────────────────────────────────────────┐
          │ ② review_prd                                  │  整树字段级审核
          │  完整性 / 一致性 / 可行性                      │  产出 failed_fields
          └──────┬───────────────────────────────┬───────┘
            未通过┘                               └─ 通过
          ▼                                        ▼
  回到 ① 仅追问 failed_fields              ③ finalize_prd
  （含 interrupt）                          遍历取值树确定性渲染
                                                 │ Markdown 初稿
                                                 ▼
                                   ┌──────────────────────────────┐
                                   │ ④ polish_prd                  │  LLM 整体润色
                                   │ 语言 / 措辞 / 格式润色定稿      │  不改结构内容
                                   └──────────────────────────────┘
                                                 │
                                                 ▼
                                           final_prd（Markdown）
```

阶段内循环（① ↔ ①′）：每个章节开启一个与 LLM **直接对话**的会话，`draft_prd` 生成自然对话回复、`audit_input` 后台判定每轮回复；未达标时审核反馈作为上下文注入、由 LLM 自然追问（不机械复述），达标则继续对话；章节讨论成熟（LLM 判定或用户 Ctrl+C）后，由 LLM 一次性抽取章节取值，经校验与批量预览确认后写入取值树。
阶段间循环（② → ①）：整树审核未通过时仅追问 `failed_fields` 对应字段。
[演进目标：失败字段先经 auto_fix_hook 自动修正，无法解决再回问用户]

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
| `audit_result` | `dict` | 阶段内审核结果 `{passed, feedback}`（单轮回复达标判定） |
| `audit_passed` | `bool` | 当前轮回复是否达到章节要求 |
| `preview_pending` | `list` | 待用户预览确认的解析结果 `[{path, value, errors}]` |
| `review_result` | `dict` | 审核评分 + 明细 |
| `review_passed` | `bool` | 审核是否通过 |
| `failed_fields` | `list[dict]` | 字段级失败原因 `[{path, dimension, reason}]` |
| `final_prd` | `str` | 最终 PRD（Markdown，经 polish_prd 润色定稿） |
| `iteration_count` | `int` | review → draft 阶段间往返次数（draft 内部逐轮循环不计入） |

### 2. 模板与数据结构

模板是全流程的**唯一真源**，由 YAML 定义，同时驱动问答与渲染：

- 数据结构契约见 [`TEMPLATE_SPEC.md`](./TEMPLATE_SPEC.md)。
- 出厂默认模板见 [`../prompts/template_schema.yaml`](../prompts/template_schema.yaml)。
- 树状递归结构，无深度限制；节点类型（`group` / `repeat` / `field`）由字段存在性推断，无显式 `id` / `type`。
- 取值树 `prd_draft` 与模板同构：`field` 叶子持值或空，`repeat` 展开为实例数组。

### 3. 节点说明

#### ① draft_prd - 章节对话会话节点

- **输入**: 用户初始简述 + 模板树（阶段间回访时另含 `failed_fields` 对应的追问路径）
- **处理**: 每个章节开启一个与 LLM **直接对话**的会话，由 LLM 自由主导追问，而非系统逐字段输出预制提示词；讨论成熟后总结为章节
  - 按位置遍历模板，依字段有效 tier 决定讨论深度：
    - `P0` 章节会话内逐字段深入追问，必要时多轮追问
    - `P1` 按章节聚合，一次讨论一个章节的细节
    - `P2` 不主动提问，章节成熟后由 LLM 基于上下文推断填入
  - 会话回合：LLM 生成自然对话回复（结构化输出 `{reply, mature}`）→ `interrupt` 展示回复并等待用户输入 → 回复进入对话历史 → **由 `audit_input` 后台判定**（见 [①′ audit_input](#-audit_input---阶段内审核节点)）
    - 模板元数据（`question` / `example` / `description` / `enum_values` / `columns`）仅作为 LLM 发起对话的引导上下文，不直接作为提示词展示给用户
    - 章节讨论成熟：LLM 判定 `mature=true`，或用户按 Ctrl+C 主动结束章节
  - 章节成熟后（一次性）：LLM 从整段对话中结构化抽取本章节全部取值 → 逐字段经统一格式保障机制校验与修复 → 整章批量预览确认后写入取值树（见 [结构化数据格式保障机制](#结构化数据格式保障机制全应用统一)）
  - `repeat` 子树：实例数量与内容都在同一章节会话内由 LLM 自然询问讨论，抽取时展开实例数组
- **输出**: 取值树 `prd_draft`（更新 `filled_paths`）
- **人工介入**: LangGraph `interrupt` 实现每轮 human-in-the-loop；Ctrl+C 为章节结束快捷键
- **LLM Prompt**: `prompts/draft.txt`

#### ①′ audit_input - 阶段内审核节点

- **输入**: 用户最新回复 + 当前章节的讨论上下文 + 模板对本章节的要求
- **处理**: 独立的审核 agent（与 `draft_prd` 角色分离，后台运行）判定本轮回复是否达到章节要求
  - 判定维度：信息充分性（是否覆盖本章节所需细节）、明确性（是否含糊或答非所问）、可执行性
  - 未达标时输出具体欠缺原因与追问建议（`feedback`）
- **输出**: `audit_result`（`{passed, feedback}`）+ `audit_passed`
- **路由**: 未达标 → 回到 ①，`feedback` 作为上下文注入，由对话 LLM 以自然追问继续会话（不机械复述）；达标 → 继续对话或进入章节抽取
- **LLM Prompt**: `prompts/audit.txt`

#### ② review_prd - 整树审核节点

- **输入**: 取值树 `prd_draft`
- **处理**: 基于结构化数据的字段级审核（区别于 ①′ 的单轮回复判定，此为整树终审）
  - **完整性**: 遍历模板，凡 `required=true` 而取值树对应位置路径为空 → 标记失败
  - **一致性**（规则 + LLM）: repeat 间引用一致性，例如用户故事角色 ∈ 目标用户角色；P0 功能清单均有对应核心功能详述
  - **可行性**: LLM 整体判分
- **输出**: `failed_fields`（路径级原因）+ `review_passed`
- **LLM Prompt**: `prompts/review.txt`

#### ③ finalize_prd - 渲染节点

- **输入**: 通过审核的取值树
- **处理**: 渲染器遍历取值树**确定性**生成 Markdown 初稿（规则见 [`TEMPLATE_SPEC.md`](./TEMPLATE_SPEC.md) 渲染规则一节）
- **输出**: `final_prd`（Markdown 初稿，交 ④ 润色）
- 渲染纯程序性、可重现，不依赖 LLM 重写
- 渲染规则：
  - 按位置路径深度生成标题层级（# / ## / ### …）。
  - group / repeat / instance 仅产出标题；field 按 field_type 产出正文。
  - text 输出段落；enum 输出所选项；table 输出 Markdown 表格。
  - 必填未填字段输出占位符「（待补充）」，非必填空字段跳过。
  - 若审核未完全通过，文末附「审核未决清单」。

#### ④ polish_prd - 润色定稿节点

- **输入**: ③ 产出的 Markdown 初稿
- **处理**: LLM 对整篇文档做最后润色
  - 语言通顺、术语一致、措辞专业；修正格式瑕疵与排版问题
  - **约束**: 仅限表达层面，不得新增 / 删除 / 变更任何业务内容与文档结构
- **输出**: `final_prd`（润色定稿，可导出 PDF）
- **LLM Prompt**: `prompts/polish.txt`

### 4. 路由逻辑

- `draft_prd` → `audit_input`: 每轮收到用户回复即进入阶段内审核
- `audit_input` → `draft_prd`: 未达标，`feedback` 注入对话由 LLM 自然追问（阶段内循环，不计入 `iteration_count`）
- `audit_input` → （继续）: 达标，继续对话直至章节成熟；章节抽取写入完成后进入下一章节或 ②
- `review_prd` → `finalize_prd`: 审核通过则渲染输出
- `review_prd` → `draft_prd`: 审核未通过则返回，`pending_paths` 替换为 `failed_fields` 对应路径，**仅追问失败字段**，不重头问
- `finalize_prd` → `polish_prd` → 输出定稿
- 格式修复循环的超限 / 失败结果走 HITL 预览确认：接受 → 写入取值树；拒绝 → 视为未填并回问用户（见 [结构化数据格式保障机制](#结构化数据格式保障机制全应用统一)）
- 阶段间最大往返次数（默认 3 次）防死循环；超限则强制进入 `finalize_prd` 并附审核未决清单；阶段内逐轮循环另设最大轮次上限

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

## 结构化数据格式保障机制（全应用统一）

所有产生结构化数据的环节——章节对话抽取、P2 推断、review 审核输出、`audit_input` 判定，以及未来的 `polish_prd`——统一采用「**解析 → 确定性校验 → LLM 修复重试 → HITL 预览确认**」的保障链路。该机制是全应用级通用能力，**不归属于任何单个节点**。

### 解析与校验

- **`validate_value(vn, value) -> list[str]`（唯一判官）**：纯规则、不依赖 LLM 的确定性校验器，是「格式正确」的唯一判定标准：
  - `enum`：取值必须是 `enum_values` 之一
  - `table`：值必须为行对象列表；每行必须包含全部 `columns` 键；单元格类型正确；行数 ≥ `min_items`
  - `text`：非空（可选）
  - 其余（如 repeat 实例数量）同样由规则校验
  - 只有它判「格式正确」，LLM 自证不算数
- **`llm_structured(model, messages, retries)` 兜底封装**：所有 `with_structured_output` 调用（P2 推断、review 等）统一走此封装；解析抛 OutputParserException / ValidationError 时，将错误消息拼回 messages 重试，为不严格执行 schema 的兼容端点（`OPENAI_BASE_URL` 切换场景）提供双保险

### 修复循环与 HITL 兜底

- **`parse_and_repair(vn, raw, llm, max_retries=2)`**：解析 → `validate_value` 校验 → 有错则将「错误清单 + 原文 + 目标格式」回喂 LLM 修正（按 field_type 用 per-type Pydantic 模型做结构化输出）→ 再校验，循环至通过或达到重试上限（默认 2 次）
- **HITL 预览确认（无论成功或失败都执行）**：修复循环结束后，无论结果是否通过校验，都将解析结果以 preview 形式输出给用户确认，由用户决定接受与否：
  - 接受 → 写入取值树
  - 拒绝（或结构仍非法）→ 视为未填，记入待追问字段回问用户
- **原则**：程序只保证数据结构正确（写入取值树的值必过 `validate_value`）；内容是否可接受由用户裁决。任何环节不得静默存入未通过校验的数据

## 扩展性

- **模板切换**: 替换或编辑 YAML 即可适配不同业务场景，只要符合 [`TEMPLATE_SPEC.md`](./TEMPLATE_SPEC.md) 契约
- **节点扩展**: 可在流程中插入新节点（如竞品分析、技术方案评估、二次润色）
- **模型切换**: 通过环境变量切换 OpenAI / Azure / 本地模型
- **人工审核**: draft 节点每轮 `interrupt` 断点 + audit 阶段内判定；review 节点支持 `interrupt` 断点
