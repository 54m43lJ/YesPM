# PRD 模板数据结构规范（TEMPLATE_SPEC / V2）

本规范定义 YesPM V2 模板的数据结构契约。模板 YAML 是全流程的**唯一真源**，同时承担「访谈指引」与「渲染脚手架」双重职责。用户可完全自定义模板，只要符合本规范即可被 interview_agent、unit_review_agent、transcribe_agent、document_review_agent、润色管线正确消费。

## 设计原则

1. **唯一真源**：不存在独立的渲染模板文件，最终文档由渲染器程序性遍历 YAML 树生成。
2. **推断优于声明**：节点身份由树中位置推断，不显式写 `id`；节点类型由字段存在性推断，不显式写 `type`。
3. **极致精简**：所有元数据均可省略并走默认；一个 field 最少只需 `title`。
4. **任意深度**：树状递归结构，嵌套层数无限制。

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

tier 决定访谈阶段（Node ①）的**单元划分**——每个访谈单元执行「访谈 → 成熟度评审 → 转录」循环：

| tier | 语义 | 处理 |
|------|------|------|
| `P0` | 最细，需专门提问 | **字段级访谈单元**：该字段单独成单元，专项对话直至成熟 |
| `P1` | 章节整体访谈 | **章节级访谈单元**：该 group 整章一个自由会话 |
| `P2` | 不访谈 | 无访谈单元，转录/收尾时由 LLM 依据上下文推断，推断失败则取 `default`，再无则留「待补充」 |

### tier 继承与单元拆分规则

- 节点的有效 tier = 自身 `tier` 或最近祖先声明的 `tier`；自身及祖先均未声明时默认 `P0`（符合人工介入原则：未声明 tier 的字段同样经专门访谈）。
- `tier` 可声明在任意节点上并对其子树生效；声明在 `group` 上即定义章节级单元。
- **P1 章节内嵌 P0 字段的拆分**：该 P0 字段**拆出独立字段级单元**，先于章节单元访谈；章节剩余内容（不含该 P0 字段）整体作为一个 P1 单元。
- 一个字段可以是「P2 推断 + required」，也可以是「P0 访谈 + 非必填」，tier 与 `required` 完全解耦。

### 访谈中涉及的节点行为

- **repeat**：实例数量与内容在单元访谈中自然确定（不强制"先问数量、再逐实例问"）；转录后按实例展开取值树。
- **table**：访谈内容由 transcribe_agent 转录为「行对象列表」，键为 `columns` 列名；**enum 转录约束**：`enum` 字段只能转录为 `enum_values` 之一，不符合时由 transcribe_agent 就近修正或标记「待补充」。
- **P2**：不产生访谈单元，转录/收尾时推断，见上表。

## 编号与寻址

无 `id`，纯位置推断。渲染器 / 审核器遍历树时按出现序生成路径：

- 顶层章节按序编号 `1`、`2`、`3` …
- 每深一层追加 `.序号`
- `repeat` 的每个实例占一级编号：`3.2 核心功能详述`（repeat）→ 实例 1 = `3.2.1` → 其子 = `3.2.1.1` …

位置路径（如 `"3.2.1.2.1"`）用作 `units_done` / `gap_list` 的寻址键：

- **gap 统一为字段级**：`gap_list` 的 `path` 恒指字段路径；访谈单元的归并按下述规则由字段的有效 tier 决定：
  - 有效 tier 为 `P0` → 该字段自身即访谈单元，直接重新访谈该字段；
  - 有效 tier 为 `P1` → 递归向上归并至所属章节级单元（最近声明 `P1` 的祖先），以根节点为兜底边界；
  - 有效 tier 为 `P2` → 不产生访谈单元，兜底直接交由 transcribe_agent 依据上下文重新总结，不走访谈。
- repeat 实例增删不改变既有实例的路径，仅追加或移除尾部序号。

```
3    功能需求                 (group)
3.1    功能模块划分           (field)
3.2    核心功能详述           (repeat, item_label=功能)
3.2.1    功能 1               (实例)
3.2.1.1   功能名称            (field)
3.2.1.2   用户操作流程        (group)
3.2.1.2.1  主流程             (field)
3.2.1.2.2  异常分支           (field)
3.2.2    功能 2               (实例)
...
```

## 中间态：取值树

`prd_draft` 是与模板同构的**取值树**：

- 遍历模板实例化：`field` 叶子持有转录值或空，`group` 仅作结构，`repeat` 展开为实例数组。
- interview_agent / unit_review_agent 的跨单元上下文、document_review_agent 的审核输入、渲染基线均基于此树。
- 取值树是全流程唯一中间态，取代任何字符串形式的草稿。

```
模板树（template）            取值树（prd_draft）
─────────────────            ─────────────────
group                        group
├─ field                     ├─ field → "已填值"
├─ repeat                    ├─ repeat
│  └─ children(单实例)        │  ├─ 实例1 (children 已实例化)
│                            │  └─ 实例2
└─ group                     └─ group
   └─ field                     └─ field → 空
```

## 渲染规则

渲染分两层，均为输出最终 Markdown 的组成部分：

### 第一层：确定性基线渲染

渲染器遍历取值树**确定性**生成 Markdown：

- 按深度产出标题层级（`#` / `##` / `###` …）。
- `group` 产出标题，不产出正文。
- `repeat` 实例产出标题（形如 `{item_label} {序号}` 或实例自定义名）。
- `field` 按 `field_type` 产出内容：`text` → 段落；`enum` → 所选项；`table` → Markdown 表格（首行为 `columns` 列名，其后每行一条记录）。
- 空值字段按策略跳过或输出占位符。
- 纯程序性、可重现，作为润色的**基线**，无提案区域不被触碰。

### 第二层：生成器-评估器润色

- 润色生成器扫描基线，识别表示优化场景（文本→表格 / 文本→Mermaid 图表 / 段落→列表 / 删减提纯），产出转换提案。
- 保真评估器校验提案（事实点不增、不减、不改），通过后应用，不通过则反馈修订，最多 2 轮。
- 低风险转换（表格化、结构化）自动执行；高风险转换（图表化、删减）需用户确认。
- **转换禁区**：`field_type: table` / `enum` 字段、`preserve: true` 的 `text` 字段，一律禁止转换。
- Mermaid 图表（flowchart / stateDiagram / sequenceDiagram / gantt / erDiagram）为合法输出形态，嵌入 Markdown。
- 所有转换记录于转换日志，支持复核与回退。

## 用户自定义合规约束

自定模板必须满足：

1. 根为节点列表（章节数组）。
2. 每个节点至少含 `title`。
3. `repeat` 必须含 `item_label` 与 `children`；`group` 必须含 `children`；`field` 不得含 `children`。
4. `field_type: enum` 必须提供 `enum_values`；`field_type: table` 必须提供非空 `columns`。
5. `preserve: true` 仅允许出现在 `field_type: text` 的字段上。
6. 每个节点都应是合法的 group / repeat / field（`children` 内不得出现 `item_label` 与 `children` 同时缺失的节点）。
7. 加载时由 Pydantic 校验；不合规模板在启动阶段即被拒绝并报错定位。

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

# 一个 repeat（可重复子树，实例数在访谈中自然确定）
- title: 核心功能详述
  item_label: 功能
  tier: P1
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
