# PRD 模板

以下为 YesPM 使用的标准 PRD（产品需求文档）模板，各章节在撰写阶段由 LLM 按产品定义自动填充。

---

## 一、文档信息

| 字段 | 内容 |
|------|------|
| 文档版本 | v1.0 |
| 创建日期 | YYYY-MM-DD |
| 作者 | - |
| 产品名称 | {product_name} |

## 二、产品概述

### 2.1 产品定位

> 一句话描述产品是什么，解决什么问题

{product_positioning}

### 2.2 目标用户

| 用户角色 | 描述 | 核心诉求 |
|----------|------|----------|
| {role_1} | {desc_1} | {need_1} |

### 2.3 产品目标

- {goal_1}
- {goal_2}

## 三、功能需求

### 3.1 功能清单

| 优先级 | 功能模块 | 功能描述 | 验收标准 |
|--------|----------|----------|----------|
| P0 | {module} | {desc} | {criteria} |
| P1 | {module} | {desc} | {criteria} |
| P2 | {module} | {desc} | {criteria} |

### 3.2 核心功能详述

#### 3.2.1 {功能名称}

- **功能描述**: {description}
- **用户操作流程**: {user_flow}
- **前置条件**: {precondition}
- **后置条件**: {postcondition}
- **异常处理**: {exception}

## 四、非功能需求

| 类别 | 要求 |
|------|------|
| 性能 | {performance} |
| 安全性 | {security} |
| 可用性 | {availability} |
| 兼容性 | {compatibility} |
| 可扩展性 | {scalability} |

## 五、用户场景（User Stories）

| ID | 用户角色 | 场景描述 | 期望结果 | 优先级 |
|----|----------|----------|----------|--------|
| US-001 | {role} | {scenario} | {expected} | P0 |

## 六、界面与交互

### 6.1 核心页面

- {page_1}: {description}
- {page_2}: {description}

### 6.2 交互规范

- {interaction_rule_1}
- {interaction_rule_2}

## 七、数据需求

### 7.1 数据模型概述

{data_model}

### 7.2 数据流转

{data_flow}

## 八、里程碑与排期

| 阶段 | 内容 | 预计时间 | 交付物 |
|------|------|----------|--------|
| M1 | {phase_1} | {date} | {deliverable} |
| M2 | {phase_2} | {date} | {deliverable} |

## 九、风险与假设

| 风险项 | 影响 | 应对措施 |
|--------|------|----------|
| {risk_1} | {impact_1} | {mitigation_1} |

## 十、附录

- 术语表
- 参考资料
- 变更记录
