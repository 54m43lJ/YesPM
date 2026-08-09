# PRD 模板编写规范

本文档面向自定义模板的二次开发者，说明模板 YAML 的编写规范。模板承担「访谈指引」与「渲染脚手架」双重职责：模板树直接驱动访谈单元的划分与提问方向，渲染器程序性遍历模板树生成最终文档（模板唯一真源原则见 [ARCHITECTURE.md](./ARCHITECTURE.md)）。用户可完全自定义模板，只要符合本规范即可被 interview_agent、unit_review_agent、transcribe_agent、document_review_agent、润色管线正确消费。

## 自定义模板编写原则

1. **推断优于声明**：节点身份由树中位置推断，不显式写 `id`；节点类型由字段存在性推断，不显式写 `type`。
2. **极致精简**：所有元数据均可省略并走默认；一个 field 最少只需 `title`。
3. **任意深度**：树状递归结构，嵌套层数无限制。
4. **结构合法**：根为节点列表（章节数组）；每个节点至少含 `title`；`repeat` 必须含 `item_label` 与 `children`，`group` 必须含 `children`，`field` 不得含 `children`。
5. **类型配套**：`field_type: enum` 必须提供 `enum_values`；`field_type: table` 必须提供非空 `columns`。
6. **禁区受限**：`preserve: true` 仅允许出现在 `field_type: text` 的字段上。

## 节点类型

类型不显式声明，按下表推断（推断优先级自上而下）：

| 推断条件 | 类型 | 必需字段 | 语义 |
|---------|------|---------|------|
| 含 `item_label` | `repeat` | `title`、`item_label`、`children` | 可重复子树；`children` 为单实例子模板，运行时按实例数复制 |
| 含 `children`（且无 `item_label`） | `group` | `title`、`children` | 容器章节，仅组织结构 |
| 二者皆无 | `field` | `title` | 叶子节点，承载数据 |

## field 叶子元数据

| 字段 | 默认 | 取值 / 说明 |
|------|------|------------|
| `field_type` | `text` | `text`（自由文本，可多行） \| `enum`（受限选择） \| `table`（扁平结构化表格） |
| `enum_values` | — | 仅 `field_type: enum` 时必填，枚举项列表 |
| `columns` | — | 仅 `field_type: table` 时必填，列定义列表；元素可为字符串（列名）或 `{title, enum_values?}` |
| `required` | `false` | 完整性审核依据；与 `tier` 完全解耦 |
| `tier` | 继承 | `P0` 字段级访谈单元 / `P1` 章节级访谈单元 / `P2` 无单元、转录时推断（见 tier 语义） |
| `hint` | — | 访谈要点提示：给 interview_agent 的提问方向（怎么问、追问什么），同时供 unit_review_agent 作为成熟度评审参考 |
| `description` | — | 字段含义说明，供访谈与审核参考 |
| `default` | — | 兜底默认值；P2 推断失败时的回退值 |
| `preserve` | `false` | 润色禁区标记：`true` 时禁止润色 agent 对该字段做任何表示转换。**仅对 `field_type: text` 生效**；`table` / `enum` 类型天然禁止润色转换，无需标记 |

> field_type 有 `text` / `enum` / `table` 三种。建模原则：**嵌套结构 → `repeat`；扁平表格（清单 / 矩阵）→ `table`；纯文本 → `text`；受限选择 → `enum`**。
>
> `table` 与 `repeat` 的区别：`table` 为扁平固定列、渲染为 Markdown 表格，整张表是单个寻址字段（值为「行对象列表」，键为列名）；`repeat` 每个实例是可任意嵌套的子树、渲染为分层小节，每个实例与其中字段各自独立寻址。需多层嵌套用 `repeat`，仅需行列清单用 `table`。
>
> `table` / `enum` 与润色：两者具有固有表示形态，**不允许**润色时转换（表格不得转回文字、枚举不得转图表）。润色转换只作用于 `text` 类型，且 `preserve: true` 的 `text` 字段同样禁止。

## tier 语义（访谈单元划分）

tier 决定访谈阶段的**单元划分**——每个访谈单元执行「访谈 → 成熟度评审 → 转录」循环：

| tier | 语义 | 处理 |
|------|------|------|
| `P0` | 最细，需专门提问 | **字段级访谈单元**：该字段单独成单元，专项对话直至成熟 |
| `P1` | 章节整体访谈 | **章节级访谈单元**：该 group 整章一个自由会话 |
| `P2` | 不访谈 | 无访谈单元，转录/收尾时由 LLM 依据上下文推断，推断失败则取 `default`，再无则留「待补充」 |

> 例外：`repeat` 的 tier 声明无效、仅靠继承（见下）。其有效 tier 为 `P0` 时，访谈单元是**整棵 repeat 子树**（自由会话确定实例数与内容），而非字段级。

### tier 继承与单元拆分规则

- 节点的有效 tier = 自身 `tier` 或最近祖先声明的 `tier`；自身及祖先均未声明时默认 `P0`（符合人工介入原则：未声明 tier 的字段同样经专门访谈）。
- `tier` 可声明在任意节点上并对其子树生效；声明在 `group` 上即定义章节级单元。**例外：`repeat` 及其子树（`children` 单实例模板）内声明的 `tier` 一律无效**——repeat 的有效 tier 仅由最近祖先的声明继承（无则默认 `P0`），其子树内所有字段随 repeat 的有效 tier，整棵 repeat 子树始终作为单一访谈单元（见「访谈中涉及的节点行为」）。
- **P1 章节内嵌 P0 字段的拆分**：该 P0 字段**拆出独立字段级单元**，先于章节单元访谈；章节剩余内容（不含该 P0 字段）整体作为一个 P1 单元。**（不适用于 repeat 子树内字段——其字段不参与拆分）**
- 一个字段可以是「P2 推断 + required」，也可以是「P0 访谈 + 非必填」，tier 与 `required` 完全解耦。

### 访谈中涉及的节点行为

- **repeat**：实例数量与内容在单元访谈中自然确定（不强制"先问数量、再逐实例问"）；转录后按实例展开取值树。repeat 自身及其子树内声明的 `tier` 无效、仅继承——整棵 repeat 子树始终作为**单一访谈单元**：有效 tier `P0` → 专门访谈整棵子树；`P1` → 并入祖先章节单元；`P2` → 不访谈、转录时推断。
- **table**：访谈内容由 transcribe_agent 转录为「行对象列表」，键为 `columns` 列名；**enum 转录约束**：`enum` 字段只能转录为 `enum_values` 之一，不符合时由 transcribe_agent 就近修正或标记「待补充」。
- **P2**：不产生访谈单元，转录/收尾时推断，见上表。

## YAML 示意

```yaml
# 一个 group（容器章节，tier 声明在 group 上即定义章节级单元）
- title: 产品概述
  tier: P0                    # P0 章节：其中 P0 字段逐个单独访谈
  children:
    - title: 一句话定位          # 极简 field：仅 title
      required: true
    - title: 目标用户            # 字段级访谈单元（P0 继承）
      hint: 谁在用、解决谁的什么问题，必要时追问典型用户画像
      required: true

# 一个 P1 章节（整体访谈）+ 内嵌 P0 字段（拆出独立单元）
- title: 文档信息
  tier: P1
  children:
    - title: 产品标识
      children:
        - title: 产品名称
          required: true
        - title: 产品代号
          tier: P0            # 从 P1 章节中拆出，单独访谈

# 一个 repeat（可重复子树，实例数在访谈中自然确定；tier 声明无效、仅继承——
# 此处无祖先声明，有效 tier 默认 P0：整棵子树作为一个专门访谈单元）
- title: 核心功能详述
  item_label: 功能
  children:
    - title: 功能名称
      required: true
    - title: 功能描述
      hint: 一句话讲清做什么，随后可追问操作流程与异常场景

# 一个 enum field（固有表示形态，禁止润色转换）
- title: 优先级
  field_type: enum
  enum_values: [P0, P1, P2]

# 一个 table field（扁平结构化清单，禁止润色转换）
- title: 风险登记
  field_type: table
  columns:
    - 风险描述
    - 影响评估
    - 应对措施

# 一个 preserve 禁区（仅 text 有效，禁止润色转换）
- title: 合规声明
  preserve: true
  hint: 原文引用，任何表示转换都不可接受
```
