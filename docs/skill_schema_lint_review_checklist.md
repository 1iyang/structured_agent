# Skill Schema Lint / Review Checklist

## 1. 文档目的

这个 checklist 用来回答两个不同层面的问题：

1. 哪些问题应该由 loader / runtime 做静态 lint
2. 哪些问题必须由人做 schema review

两者都需要，因为：

- 机械错误适合自动挡掉
- 设计错误往往不会触发解析失败，但会直接破坏实验结论

---

## 2. 当前已经适合做静态 lint 的检查

下面这些项已经和当前 runtime 设计高度一致，应该被视为基础 lint 规则。

### 2.1 Manifest 基础结构

- [ ] `SKILL.md` 具有合法 frontmatter
- [ ] skill 声明了 canonical task fields
- [ ] 至少声明一个 input profile
- [ ] input profile 名称不重复

### 2.2 Input Profile 完整性

- [ ] 每个 profile 都声明了 `match`
- [ ] 每个 canonical task field 都有 `map.<field>` 映射
- [ ] profile 的职责只是输入归一化，不混入任务决策逻辑

### 2.3 Output Schema 基础合法性

- [ ] 每个 output schema 至少定义一个字段
- [ ] schema 字段名稳定，不依赖某个样例的局部措辞
- [ ] schema 语义属于某个明确层次，而不是杂糅状态

### 2.4 Stage Graph 合法性

- [ ] stage 名称不重复
- [ ] 每个 stage 都声明 `executor`
- [ ] 每个 stage 都声明 `writes`
- [ ] `stage_type` 合法
- [ ] `criticality` 合法
- [ ] `tool_mode` 合法

### 2.5 Stage- Schema 一致性

- [ ] `output_schema` 存在且能被解析
- [ ] schema 字段集合与 `writes` 完全一致
- [ ] 不允许 stage 写一套字段、schema 定义另一套字段

### 2.6 依赖合法性

- [ ] `reads.task` 中的字段都存在于 canonical task
- [ ] `reads.state` 中的字段都已在前序 stage 产出
- [ ] `reads.artifacts` / `writes.artifacts` 引用的 artifact 已声明
- [ ] `uses.tools` 引用的工具已声明

### 2.7 Tool Contract 一致性

- [ ] stage 使用工具时，`tool_mode` 不为 `none`
- [ ] stage 不使用工具时，`tool_mode` 应为 `none`
- [ ] stage 使用工具时，显式声明 `tool_intent`
- [ ] stage 声明 `tool_intent` 时，确实存在对应工具调用
- [ ] side-effecting 工具 stage 不伪装成 retrieval / lookup
- [ ] 写重 artifact 的 tool stage 显式声明 `post_tool_policy`
- [ ] suffix 中的 `re_retrieval` 是否在下一阶段回到 compression boundary
- [ ] 工具副作用与 stage 描述一致

---

## 3. 强烈建议补充的静态 lint

这些规则不一定都要现在实现，但它们已经足够稳定，值得作为下一批 lint 目标。

### 3.1 字段覆盖与重复写入

- [ ] 同一字段是否被多个 stage 重复写入且没有明确覆写语义
- [ ] 一个 stage 是否同时写入过多跨层字段
- [ ] `anchor stages` 是否过度写入最终答案类字段

### 3.2 Anchor / Suffix 边界检查

- [ ] `anchor stages` 是否真的读取了重证据，而不是空转
- [ ] `suffix stages` 是否仍然默认读取原始大上下文
- [ ] `suffix stages` 如果再次开 read-only tool，是否声明了回压缩路径
- [ ] raw-context baseline 是否被误标成 anchor workflow

### 3.3 Schema 抽象层次检查

- [ ] blocker type 和具体缺失项是否被混写
- [ ] path / decision / action 是否被混写
- [ ] request / result / response text 是否被混写

### 3.4 泛化性检查

- [ ] enum 值是否明显来自当前测试集标签而不是 workflow 控制需求
- [ ] 字段名是否包含过强的样例特定术语
- [ ] stage instruction 是否要求输入必须长成某种固定 JSON 形状

---

## 4. 人工 Schema Review Checklist

下面这些项很难完全自动化，但每次新增或重构 skill 都应该走一遍。

### 4.1 Normalize Review

- [ ] 不同格式但同类任务，是否都能映射到同一 canonical task
- [ ] canonical task 是否只保留 workflow 真正需要的稳定输入
- [ ] 输入 profile 是否避免了针对单一测试集字段名的隐式绑定

### 4.2 Observe / Abstract Review

- [ ] Observe 字段是否主要记录事实，而不是结论
- [ ] Abstract 字段是否明显比原始证据更小、更稳、更可复用
- [ ] 这些状态是否至少会被两个后续阶段复用，或者能明显减少一次大回读

### 4.3 Route / Execute Review

- [ ] 路径判断与最终决策是否分开
- [ ] 当前不能执行，是否区分为 `needs_info`、`defer`、`denied` 等不同控制语义
- [ ] “可行路径仍存在，但有 blocker” 这种状态是否能被表达

### 4.4 Verify / Handoff Review

- [ ] 是否能显式表达 unsupported commitment、missing evidence、policy conflict
- [ ] handoff state 是否足够小且可复用，而不是再次携带大段原文摘要
- [ ] 最终输出是否能追溯回前面的结构化状态

### 4.5 Tool / Artifact Review

- [ ] 工具原始结果是否先保存为 artifact
- [ ] 是否只把真正会复用的工具结论下沉为 state
- [ ] tool-aware stage 的成本与副作用是否在 metadata 中可见
- [ ] `tool_intent` 是否表达了真正的工具目的，而不是泛化成“都叫 retrieval”
- [ ] `post_tool_policy` 是否保证工具结果不会无边界地流入 suffix
- [ ] 如果发生 `re_retrieval`，后续是否真的回到了 abstract / compression 状态

### 4.6 Experiment Review

- [ ] 这个 skill 是否可以清楚地派生出 `minimal`
- [ ] 是否可以构造语义对齐的 `full`
- [ ] 是否可以构造端到端 `raw`
- [ ] 如果 minimal 失真，是否能判断是 schema 不足、检索不足、工具不足，还是模型本身问题

---

## 5. 反模式清单

每次 review 时，都应该显式检查下面这些反模式是否出现。

- [ ] 负向 blocker 很多，但没有保留正向可行路径
- [ ] anchor state 直接写成答案草稿
- [ ] 通过添加越来越多 case-specific proof 字段来修补失败样例
- [ ] 把数据集标签当成 workflow 本体
- [ ] 用一大段摘要冒充结构化状态
- [ ] suffix 虽然名义上读 state，实际上仍依赖大原文
- [ ] suffix 名义上只补一次检索，实际上把新 artifact 一路带到终点
- [ ] 把“客户请求”“系统判断”“下一步动作”塞进一个字段

---

## 6. minimal / full / raw 的 review 口径

为了避免实验结论漂移，三种口径应保持如下约束。

### 6.1 minimal

- [ ] 前缀阶段读取原始大证据
- [ ] suffix 主要依赖结构化状态
- [ ] 允许的补充回读必须显式声明

### 6.2 full

- [ ] 与 minimal 共享同一 workflow 主干
- [ ] 与 minimal 共享同一输出 schema
- [ ] 差异只应主要体现在 suffix 可见证据更多

### 6.3 raw

- [ ] 不应偷偷复用 anchor state 作为关键瓶颈
- [ ] 应代表“不做结构化最小传递”的端到端参考线
- [ ] raw 不是为了做差，而是为了回答结构化是否值得

---

## 7. 新 Skill 合入前的最终问题

在把一个新 skill 放进主实验线前，至少回答下面几个问题：

1. 它的 anchor boundary 在哪里？
2. 这些 anchor 输出是否真的能支撑 suffix？
3. suffix 是否真的减少了重复原始证据传递？
4. schema 是否表达了“事实 / 路径 / 决策 / 动作 / 交接”的分层？
5. 它是否只是对现有测试集调参后的产物？
6. 如果换一份同类但字段命名不同的数据，它还能跑吗？
7. `minimal / full / raw` 的差异是否足以支持实验结论？

如果这些问题无法得到清楚回答，就不应该急着把它当成“结构化传递有效”的证据。
