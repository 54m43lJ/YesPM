# PRD 模板数据结构规范（TEMPLATE_SPEC）

本规范定义 YesPM 模板的数据结构契约。模板 YAML 是全流程的**唯一真源**，同时承担「对话引导元数据」与「渲染脚手架」双重职责。用户可完全自定义模板，只要符合本规范即可被 `draft_prd`、`audit_input`、`review_prd`、`finalize_prd` 节点正确消费（`polish_prd` 作用于成文后的 Markdown，不接触模板）。

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
| `tier` | 继承 | `P0` 章节内逐字段深入追问 / `P1` 整章自由对话讨论 / `P2` LLM 推断填充 |
| `question` | `请说明{title}：` | 对话引导话术：引导 LLM 发起该话题的自然提问（兜底参考，不直接展示）；支持 `{title}` 与 `{n}`（repeat 实例序号）插值 |
| `example` | — | 提问示例 |
| `description` | — | 字段含义，供追问与审核参考 |
| `default` | — | 兜底默认值；支持 `{n}` 插值 |

> `question` / `example` / `description` 是**对话引导元数据**：作为上下文喂给 `draft_prd` 章节会话的 LLM，由 LLM 组织成自然对话提问，**不直接作为提示词文本**展示给用户（`{title}` / `{n}` 插值同样用于引导）。
>
> field_type 有 `text` / `enum` / `table` 三种。建模原则：**嵌套结构 → `repeat`；扁平表格（清单 / 矩阵）→ `table`；纯文本 → `text`；受限选择 → `enum`**。
>
> `table` 与 `repeat` 的区别：`table` 为扁平固定列、渲染为 Markdown 表格，整张表是单个寻址字段（值为「行对象列表」，键为列名）；`repeat` 每个实例是可任意嵌套的子树、渲染为分层小节，每个实例与其中字段各自独立寻址。需多层嵌套用 `repeat`，仅需行列清单用 `table`。

## tier 继承

- 叶子节点的有效 tier = 自身 `tier` 或最近祖先的 `tier`。
- 整棵树无任何 tier 时，根默认 `P1`。
- 任一节点可覆盖祖先 tier，对其子树生效。

tier 仅决定 `draft_prd` 节点的讨论策略（对话式澄清的深度与粒度），不与 `required` 耦合：一个字段可以是「P2 推断 + required」，也可以是「P0 章节内逐字段追问 + 非必填」。

| tier | 讨论策略 |
|------|---------|
| `P0` | 章节会话内逐字段深入追问，必要时多轮追问 |
| `P1` | 按章节聚合，以对话方式讨论整章细节 |
| `P2` | 不主动提问，章节成熟后由 LLM 基于上下文推断填入 |

## 编号与寻址

无 `id`，纯位置推断。渲染器 / 审核器遍历树时按出现序生成路径：

- 顶层章节按序编号 `1`、`2`、`3` …
- 每深一层追加 `.序号`
- `repeat` 的每个实例占一级编号：`3.2 核心功能详述`（repeat）→ 实例 1 = `3.2.1` → 其子 = `3.2.1.1` …

位置路径（如 `"3.2.1.2.1"`）用作 `filled_paths` / `failed_fields` 的寻址键。repeat 实例增删不改变既有实例的路径，仅追加或移除尾部序号。

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

- 遍历模板实例化：`field` 叶子持有用户填入值或空，`group` 仅作结构，`repeat` 展开为实例数组。
- 审核（`review_prd`）与渲染（`finalize_prd`）均基于此树。
- 取值树是全流程唯一中间态，取代任何字符串形式的草稿。
- **任何写入取值树的值必须通过确定性格式校验**（`validate_value`，见 [ARCHITECTURE.md](../docs/ARCHITECTURE.md)「结构化数据格式保障机制」），格式不合法或未通过用户预览确认的值不得写入。

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

`finalize_prd` 由渲染器遍历取值树**确定性**生成 Markdown：

- 按深度产出标题层级（`#` / `##` / `###` …）。
- `group` 产出标题，不产出正文。
- `repeat` 实例产出标题（形如 `{item_label} {序号}` 或实例自定义名）。
- `field` 按 `field_type` 产出内容：`text` → 段落；`enum` → 所选项；`table` → Markdown 表格（首行为 `columns` 列名，其后每行一条记录）。
- 空值字段按策略跳过或输出占位符。
- 渲染纯程序性、可重现，不依赖 LLM 重写；成文后由独立的 `polish_prd` 节点做整体润色定稿（仅表达层面，不改结构与内容）。

## 用户自定义合规约束

自定模板必须满足：

1. 根为节点列表（章节数组）。
2. 每个节点至少含 `title`。
3. `repeat` 必须含 `item_label` 与 `children`；`group` 必须含 `children`；`field` 不得含 `children`。
4. `field_type: enum` 必须提供 `enum_values`；`field_type: table` 必须提供非空 `columns`。
5. 每个节点都应是合法的 group / repeat / field（`children` 内不得出现 `item_label` 与 `children` 同时缺失的节点）。
6. 加载时由 Pydantic 校验；不合规模板在启动阶段即被拒绝并报错定位。

## YAML 示意

```yaml
# 一个 group（容器章节）
- title: 产品概述
  tier: P0
  children:
    - title: 一句话定位          # 极简 field：仅 title
      required: true

# 一个 repeat（可重复子树）
- title: 核心功能详述
  item_label: 功能
  tier: P0
  children:                      # 单实例子模板，运行时复制
    - title: 功能名称
      required: true
    - title: 用户操作流程
      children:                  # 嵌套 group，任意深度
        - title: 主流程
        - title: 异常分支
          tier: P1               # 子树降级为章节级讨论

# 一个 enum field
- title: 优先级
  field_type: enum
  enum_values: [P0, P1, P2]

# 一个 table field（扁平结构化清单）
- title: 风险登记
  field_type: table
  columns:                  # 列名可用字符串简写
    - 风险描述
    - 影响评估
    - 应对措施
    - title: 优先级          # 也可用对象形式，附加约束
      enum_values: [高, 中, 低]
```
