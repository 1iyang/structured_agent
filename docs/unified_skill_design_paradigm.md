# 统一 Skill 设计范式

## 1. 文档目的

本文把当前项目对 `skill-defined workflow runtime` 的主张正式收敛成一个统一范式，用来回答四个问题：

1. skill 在这个项目里究竟承担什么角色
2. 什么样的 workflow 适合被 skill 化
3. schema 应该如何设计，才能真正支持最小必要传递（`minimal-transfer`）
4. 新增 skill 时，应该用什么实验口径和验收标准判断它是否成立

本文不主张：

> 只要把 workflow 写成 skill，系统就会天然更高效。

本文主张的是：

> skill 提供一个显式的 workflow contract，使 runtime 能够识别阶段边界、状态充分性、工具依赖和最小必要传递条件。

只有当这个 contract 真正改变了后续阶段的信息流时，skill 才会和 token、延迟、策略控制产生实质关系。

---

## 2. 适用范围

这套范式主要面向下面这类任务：

- `task-specific`
- `known workflow`
- 输入仍包含混合文本、半结构化证据或工具返回结果
- 阶段内仍需要语义压缩、证据整合、软判断或自然语言生成

典型例子包括：

- support ticket workflow
- bug triage workflow
- repo-aware coding subflows
- research synthesis
- compliance / review

如果任务已经：

- 完全规则化
- 输出空间稳定且可枚举
- 不再需要语义压缩或生成

那么直接用代码约束通常更简单，也更高效。此时 skill 的价值会明显下降。

---

## 3. 统一范式：从原始输入到可交接状态

从更广的 skill 类型看，不同任务表面差异很大，但多数都可以收敛到同一条主链：

`Normalize -> Observe -> Abstract -> Route -> Execute -> Verify -> Handoff`

这七段不是要求每个 skill 都逐段显式实现，而是一个统一的设计坐标系。具体 skill 可以合并其中若干段，但不应该跳过它们在语义上的区分。

### 3.1 Normalize

目的：

- 把不同来源、不同字段命名、不同外部格式的任务输入统一到 canonical task

产物应该是：

- canonical task fields
- input profile match / mapping

设计要求：

- 只做输入对齐，不做任务判断
- 不把具体数据集字段名写死到后续 stage 中

### 3.2 Observe

目的：

- 从原始文本、工具结果、上下文包中抽取第一层可复用信号

产物通常包括：

- raw facts
- entities
- explicit symptoms
- user request
- policy snippets
- tool observations

这一层更接近“看到了什么”，而不是“最后该怎么办”。

### 3.3 Abstract

目的：

- 把 Observe 阶段得到的大量局部信号压缩成后续阶段可重复使用的中间状态

产物通常包括：

- evidence factors
- blockers
- feasible path
- candidate modules
- search queries
- risk flags

这一层是结构化传递的关键，因为它通常对应原始大证据到小状态的第一道边界。

### 3.4 Route

目的：

- 基于中间状态决定后续需要走哪条处理路径、是否需要额外检索、是否需要人工/专家队列、是否需要工具调用

产物通常包括：

- chosen branch
- escalation need
- retrieval need
- tool plan
- fallback mode

Route 的重点是“决定接下来去哪”，不是“执行完了什么”。

### 3.5 Execute

目的：

- 基于已选路径产出决策、方案、修复计划、响应内容或工具动作

产物通常包括：

- eligibility decision
- resolution plan
- triage decision
- fix plan
- drafted response

这一层可以使用 LLM，也可以是 tool executor 或规则执行器。

### 3.6 Verify

目的：

- 对执行结果做一致性、证据充分性、约束符合性或风险检查

产物通常包括：

- missing evidence
- unsupported commitment
- schema repair
- policy violations
- confidence / risk markers

对于高风险任务，Verify 不应只是可有可无的文案润色。

### 3.7 Handoff

目的：

- 形成面向后续系统、人工、用户或下一轮 agent 的可交接结果

产物通常包括：

- final response
- next actions
- required follow-up
- compact handoff state

Handoff 强调“给谁接、接什么、用什么最小格式接”。

---

## 4. 两个跨层约束

上面七段之外，还有两个横跨全流程的维度必须显式存在。

### 4.1 Constraints

包括：

- budget / SLO
- policy constraints
- tool permissions
- required-vs-optional stages
- high-risk guardrails

这些约束不应只藏在 prompt 文本里，而应该作为 runtime 可以读取的显式 contract。

### 4.2 Memory and Provenance

包括：

- 当前状态来自哪一阶段
- 哪些字段是事实、哪些是判断
- 哪些字段来自工具、哪些来自用户原文
- 哪些字段已经被后续阶段消费

如果 provenance 不清楚，后续“最小必要传递”很容易变成“带着一堆不可解释的压缩结论继续推”。

---

## 5. 和当前 runtime 的对应关系

当前项目里的 stage metadata 可以直接映射到这套范式：

- `anchor stages`：
  通常位于 `Observe / Abstract` 边界，是重证据读取后形成可复用状态的关键阶段。
- `suffix stages`：
  通常位于 `Route / Execute / Verify / Handoff` 一侧，理论上更应该依赖结构化状态而不是完整原文。
- `optional stages`：
  适合后续策略层在预算紧张时做跳过、降级或延后处理。
- `tool_mode`：
  把“纯推理阶段”和“依赖外部工具阶段”区分开，避免所有 stage 都被当成同一种成本模型。
- `tool_intent`：
  把“为什么开工具”显式化，至少区分常规检索、suffix re-retrieval、轻量 lookup 和副作用动作。
- `post_tool_policy`：
  把“开完工具后如何回到可复用状态”显式化，避免工具结果直接漂进后续 suffix 推理。

因此，skill 在当前项目中的核心作用不是“把 prompt 改写成 markdown”，而是把这些边界正式暴露给 runtime。

---

## 6. Schema 设计原则

### 6.1 先分层，再定字段

先判断一个字段属于哪一层：

- Observe facts
- Abstract state
- Route state
- Execute result
- Verify state
- Handoff result

不要一开始就直接围绕“最后答案怎么写”组织 schema。

### 6.2 区分事实、路径、决策、动作

这是当前项目最重要的原则之一。

以 support 为例：

- `missing_information`：
  缺的具体信息是什么
- `blocking_requirements`：
  阻塞类型是什么
- `preferred_resolution_path`：
  路径上仍然成立的处理方向是什么
- `eligibility_decision`：
  当前在证据条件下的决策状态是什么
- `resolution_steps`：
  接下来要做的动作是什么

如果把这些混成一个字段，后续阶段会失去可复用的控制面。

### 6.3 枚举用于稳定控制状态，不用于开放世界事实

适合做严格枚举的通常是：

- decision states
- route states
- blocker types
- risk levels
- action categories

不适合过早枚举的通常是：

- 原始症状文本
- 用户叙述细节
- repo 中的开放文件路径集合
- 长尾政策细则原文

也就是说，枚举应该更多服务 runtime 控制，而不是试图穷举世界。

### 6.4 抽象 blocker 类型，不把样例细节写进控制字段

例如：

- 好的设计：
  - `blocking_requirements = ["customer_evidence"]`
  - `missing_information = ["damage_proof"]`
- 差的设计：
  - 把 `damage_proof` 直接当成通用 blocker 类型

前者更适合泛化到同类任务；后者容易被当前测试集牵着走。

### 6.5 Tool / Artifact contract 也要结构化，而不是只写在 instruction 里

如果某个 stage 会开工具，那么至少还要回答两个问题：

- 这次工具调用的意图是什么？
- 工具结果是直接形成轻量 state，还是必须先经过一次压缩才能继续往后传？

推荐约定：

- 常规前缀检索：`tool_intent = retrieval`
- suffix 内补证据：`tool_intent = re_retrieval`
- 轻量事实查询：`tool_intent = lookup`
- 真实副作用动作：`tool_intent = side_effect`

以及：

- 如果工具产出的是重 artifact，优先要求 `post_tool_policy = requires_compression`
- 如果只是把少量工具结果直接下沉成状态，可用 `post_tool_policy = direct_state`

其中最重要的一条是：

> suffix 中一旦发生 re-retrieval，后面必须显式经过一次 compression boundary，再回到抽象状态传递。

否则所谓“最小必要传递”很容易退化成“suffix 临时重新打开大证据后一路把它带到底”。

### 6.6 不要让 anchor state 直接变成答案草稿

anchor state 应该优先保存：

- 事实
- 抽象因子
- 路径信息
- 风险与缺口

而不是直接保存：

- 最终回复文本
- 过强的最终裁决
- 高度任务特定的表述模板

否则结构化状态只是“提前把答案写了一遍”，并没有真正形成可复用抽象。

### 6.7 对后续复用负责，而不是只对当前 stage 正确负责

一个字段是否值得存在，不只看它是否能解释当前 stage，还要看：

1. 后续是否会复用
2. 后续是否能在不回读原始大证据的情况下依赖它继续推进
3. 它是否比原始证据明显更小、更稳、更可控

### 6.8 工具输出先成 artifact，再决定是否下沉为 state

外部工具返回往往更大、更噪、更依赖具体环境。

更稳的模式是：

1. 工具原始返回保存在 artifact
2. 由单独 stage 把 artifact 摘成结构化 state
3. `suffix stages` 只读取必要 state，必要时再显式回读 artifact

这样才能在 tool-aware workflow 中继续讨论“最小必要传递”。

---

## 7. 常见反模式

### 7.1 负向信息过多，正向路径缺失

只存：

- 为什么现在不能做
- 缺什么
- 风险是什么

却不存：

- 如果补齐信息，哪条路径仍然成立

这会让 `suffix stages` 容易漂到 `denied` 一类过强结论。

### 7.2 用数据集标签替代通用控制语义

如果枚举边界来自当前标注集而不是来自 workflow 自身的控制需求，schema 很容易过拟合。

### 7.3 用文本摘要代替结构化因子

长段 summary 看起来压缩了，但后续阶段仍然难以稳定复用，也不利于策略层判断 sufficiency。

### 7.4 把请求、判断、动作写进同一字段

例如把“客户想要什么”“当前是否允许”“接下来准备做什么”混在一个 `resolution` 字段里，会让 stage 间 contract 非常脆弱。

### 7.5 suffix 仍然默认依赖完整原始证据

如果关键 `suffix stages` 仍然总是读取原始大上下文，那么 skill 虽然存在，但并没有真正形成结构化传递边界。

---

## 8. 三种实验口径

为了判断一个新 skill 是否真的带来结构化传递价值，至少应该保留下面三种实验口径。

### 8.1 minimal

定义：

- 前缀 anchor stages 读取原始证据
- suffix stages 主要依赖结构化状态
- 只允许有限、显式声明的补充回读

它回答的是：

> 如果后续主要依赖最小状态，这个 workflow 还能不能站住？

### 8.2 full

定义：

- 前缀仍然产出相同结构化状态
- 但 suffix 继续保留完整证据或更大证据视图

它回答的是：

> 当后续继续看全量证据时，质量上限和成本上限大概在哪？

### 8.3 raw

定义：

- 不依赖 anchor state 形成的中间瓶颈
- 让后续阶段持续主要依赖原始上下文

它回答的是：

> 如果不走结构化传递主张，端到端系统本身表现如何？

### 8.4 minimal + escape

对于更真实的系统，建议再增加一个增强口径：

- 默认走 minimal
- 当 Verify 或 sufficiency check 发现证据缺口时，允许受限回读或再检索

它更接近未来策略层真正会落地的形态，也更适合和预算插件结合。

---

## 9. 新 Skill 的验收标准

一个新 skill 至少应满足以下大部分条件，才值得进入主实验线。

### 9.1 Contract 清晰

- 有明确的 canonical task
- 至少一个稳定 input profile
- stage graph 顺序清晰
- task / state / artifact / tool 读写边界清楚

### 9.2 State 分层清晰

- facts、path、decision、action、handoff 没有混成一团
- anchor state 不是答案草稿
- suffix 有机会主要依赖结构化状态推进

### 9.3 泛化性清晰

- 没有把现有测试集字段名写死进 workflow 主干
- 没有把长尾样例细节硬编码成核心控制枚举
- 换一份同类但格式不同的数据，仍能通过 Normalize 对齐

### 9.4 实验性清晰

- 可以跑 `minimal / full / raw`
- 有质量指标，也有成本指标
- 能解释质量差异是来自 schema、检索、工具、还是 suffix 决策

### 9.5 可策略化

- stage metadata 足够支持后续策略插件读取
- required / optional / tool-aware 边界可见
- 可以进一步挂接预算、降级、补读或回退策略

如果一个 workflow 做不到这些，它仍然可以是一个合法 skill；但它不一定是一个适合研究“结构化最小传递”的好实验对象。

---

## 10. 对当前项目的直接含义

对当前项目而言，这套统一范式的落脚点很明确：

1. support ticket workflow 与 bug triage workflow 不应再被当成彼此独立的 prompt 工程
2. 两者都应尽量被整理为共享的 skill contract 问题
3. 结构化状态的价值应主要体现在 suffix 是否真正减少了重复证据传递
4. 后续策略插件应建立在 stage metadata 与 state sufficiency 上，而不是让模型临场决定一切

因此，下一阶段最重要的不是继续堆更多字段，而是持续检查：

- anchor boundary 是否成立
- suffix 是否真的 state-sufficient
- tool / artifact 是否被正确分层
- minimal / full / raw 的差异是否能说明问题
