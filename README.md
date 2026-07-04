# Structured Agent

这个仓库围绕一条明确的研究主线展开：

> 对于已知多阶段 workflow，是否可以把阶段间传输从 `full history` 改成最小结构化状态，也就是实现最小必要传递（`minimal-transfer`），在尽量不伤质量的前提下降低上下文传输、prompt token 和端到端时延。

当前项目包含两层代码：

- `structured_agent/`：第一阶段主实验代码，直接实现 support ticket workflow 和 bug triage workflow，并对比三种状态传递策略。
- `skill_workflow_runtime/`：第二阶段 `skill-defined workflow runtime` 原型（下文简称 `skill runtime`），用 `SKILL.md` 显式声明 workflow contract、stage metadata、artifact / tool contract 和策略接口。

仓库当前已经清理为一条统一研究线，不再保留无关的 recommendation 分支或其他历史试验支线。

## 当前阶段结论

当前归档结果集中在：

- `runs/report_ready/report_0629/`
- `runs/report_ready/report_0701/`
- `runs/report_ready/report_0702/`

阶段性结论可以概括为三点。

### 1. 结构化状态传递在 support 上已经成立

归档报告：

- `runs/report_ready/report_0701/support_internal_report.md`

主结果：

- Structured-FixedDeps prompt tokens：`42027`
- Full-History Carry prompt tokens：`76188`
- Structured-FixedDeps downstream context reduction vs Full-History：`0.733`
- Structured-FixedDeps exact match rate：`0.625`
- Full-History Carry exact match rate：`0.500`
- Structured-FixedDeps eligibility match rate：`0.875`
- Full-History Carry eligibility match rate：`0.625`

结论：

- 在 support 任务上，结构化传递已经不只是“更省”，而是“更省且整体更稳”。
- 当前主要误差不在输入理解，而在 `eligibility / escalation / resolution_category` 的边界。

### 2. 结构化状态传递在 bug triage 上效率成立，但质量结论尚未完全收口

归档报告：

- `runs/report_ready/report_0629/bug_internal_report.md`
- `runs/report_ready/report_0629/bug_internal_report_with_review.md`

主结果：

- Structured-FixedDeps prompt tokens：`42478`
- Full-History Carry prompt tokens：`97328`
- Structured-FixedDeps downstream context reduction vs Full-History：`0.667`
- Structured-FixedDeps primary-file match rate：`0.625`
- Full-History Carry primary-file match rate：`0.750`
- 三种方法的 gold primary-file candidate recall 都是 `0.750`

结论：

- bug triage 上，“结构化传递显著降低上下文成本”已经成立。
- 但质量瓶颈仍然主要卡在：
  - candidate recall 不足
  - candidate 已召回时的 primary-file 决策边界
  - root-cause taxonomy 和 evidence-label 评估口径

### 3. `state + budget` 联合策略已经在 `skill-defined workflow runtime` 上跑通

归档结果：

- `runs/report_ready/report_0702/skill_runtime/support_budget_experiments/support_budget_summary.md`

主结果：

- `support_ticket` 在高风险样本上的 required budget 明显低于 full-evidence 和 raw-context baseline
- crossover budget 下，minimal structured skill 可以打开 optional suffix branch，而 full/raw baseline 打不开
- low-risk case 下，三者都能仅靠 state 正常跳过 optional branch

结论：

- 插件基于 `state + remaining budget` 做阶段策略选择是可行的。
- 结构化状态不会自动带来效率收益，但它会直接改变 optional branch 的预算可达边界。

## 仓库结构

当前建议按下面的方式理解仓库。

### 1. 第一阶段主实验代码

- `structured_agent/`
- `run_support_workflow.py`
- `run_bug_triage_workflow.py`

这条链路负责：

- support ticket workflow
- bug triage workflow
- `structured_fixed_deps / full_history_carry / compressed_history_carry` 三种状态传递对比
- 归档主报告的复现

### 2. 第二阶段 skill runtime 原型

- `skill_workflow_runtime/`

这条链路和 `structured_agent/` 隔离，不复用旧 workflow 实现，主要负责：

- 从 `SKILL.md` 读取 workflow
- 显式执行 stage graph
- 记录 task / state / artifact / prompt token 使用
- 暴露 stage metadata、anchor / suffix / optional 分组
- 支持 budget-aware 策略
- 支持 tool-aware suffix re-retrieval 子环

### 3. 数据与归档结果

- `examples/`：第一阶段 workflow 的输入样本和带标签主实验集
- `skill_workflow_runtime/tasks/`：skill runtime demo 和小规模实验任务
- `runs/report_ready/`：当前整理后的归档结果

### 4. 研究文档

- `docs/structured_transfer_research_roadmap.md`
- `docs/unified_skill_design_paradigm.md`
- `docs/skill_schema_lint_review_checklist.md`
- `docs/external_benchmark_validation_plan.md`
- `docs/skill_workflow_runtime_README.md`

## 研究定位

这个项目的理论定位需要刻意避免一个过强说法：

> “只要把 workflow 写成 skill，系统就会天然更高效。”

当前更准确的主张是：

> skill 的价值在于把 stage boundary、state dependency、artifact provenance 和 tool contract 显式化，从而让最小必要传递（`minimal-transfer`）成为可定义、可检查、可执行的 workflow contract。

因此，项目当前最适合的问题范围是：

- `task-specific`
- `known workflow`
- 存在状态充分边界（`state-sufficient boundary`）

也就是：

- 输入仍然是混合文本证据
- 阶段内部仍然需要语义压缩、证据整合、模糊判断或自然语言生成
- 但 workflow 结构本身是稳定的

典型任务就是：

- support ticket workflow
- local-repository bug triage workflow

如果任务本身已经高度结构化、规则稳定、输出空间可枚举，那么直接代码约束通常更简单，不一定需要 LLM，也不一定需要 skill contract。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .
```

最常用依赖包括：

- `vllm`
- `torch`
- `transformers`
- `pydantic`
- `numpy`

## 运行入口

### A. 第一阶段主实验：support ticket workflow

输入样本：

- `examples/support_tickets_realistic_labeled.json`

运行示例：

```bash
CUDA_VISIBLE_DEVICES=1 python run_support_workflow.py \
  --model /data/model/qwen-4b \
  --tickets-file examples/support_tickets_realistic_labeled.json \
  --output runs/local/support_report.md \
  --gpu-memory-utilization 0.55 \
  --slo-seconds 20
```

这条链路会输出：

- 主报告
- 对应 artifacts 目录
- 每个 ticket 的中间状态和结果 JSON

### B. 第一阶段主实验：bug triage workflow

输入样本：

- `examples/bug_triage_issues_realistic.json`

运行示例：

```bash
CUDA_VISIBLE_DEVICES=1 python run_bug_triage_workflow.py \
  --model /data/model/qwen-4b \
  --repo-root . \
  --issues-file examples/bug_triage_issues_realistic.json \
  --output runs/local/bug_report.md \
  --gpu-memory-utilization 0.55 \
  --slo-seconds 20
```

如果需要 review ablation：

```bash
CUDA_VISIBLE_DEVICES=1 python run_bug_triage_workflow.py \
  --model /data/model/qwen-4b \
  --repo-root . \
  --issues-file examples/bug_triage_issues_realistic.json \
  --output runs/local/bug_report_with_review.md \
  --gpu-memory-utilization 0.55 \
  --slo-seconds 20 \
  --include-review
```

### C. 第二阶段原型：运行单个 skill workflow

最常用入口：

- `skill_workflow_runtime/run_skill_workflow.py`

示例：

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/support_ticket \
  --task skill_workflow_runtime/tasks/support_ticket_realistic_profile_demo.json \
  --llm-mode deterministic
```

如果要跑本地模型：

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/support_ticket \
  --task skill_workflow_runtime/tasks/support_ticket_realistic_profile_demo.json \
  --llm-mode local-vllm \
  --llm-model /data/model/qwen-4b \
  --cuda-visible-devices 1 \
  --gpu-memory-utilization 0.55 \
  --max-num-seqs 1 \
  --enforce-eager
```

### D. 第二阶段原型：support ticket `state + budget` experiments

入口：

- `skill_workflow_runtime/run_support_budget_experiments.py`

示例：

```bash
python3 skill_workflow_runtime/run_support_budget_experiments.py \
  --llm-mode deterministic \
  --output runs/local/support_budget_summary.json \
  --markdown-output runs/local/support_budget_summary.md
```

### E. 第二阶段原型：MULocBench localization experiments

入口：

- `skill_workflow_runtime/run_mulocbench_localization_experiments.py`

这条链路用于把 bug 侧的公开 benchmark 扩展拆成：

- retrieval / candidate recall / primary-file localization 验证层

## 输入数据

### 第一阶段 workflow 数据

Support：

- `examples/support_tickets.json`
- `examples/support_tickets_labeled.json`
- `examples/support_tickets_realistic.json`
- `examples/support_tickets_realistic_labeled.json`

Bug：

- `examples/bug_triage_issues.json`
- `examples/bug_triage_issues_realistic.json`

### 第二阶段 skill runtime 数据

- `skill_workflow_runtime/tasks/support_ticket_realistic_profile_demo.json`
- `skill_workflow_runtime/tasks/support_ticket_account_recovery_complex_demo.json`
- `skill_workflow_runtime/tasks/support_ticket_low_risk_ready_demo.json`
- `skill_workflow_runtime/tasks/bug_triage_labeled_profile_demo.json`
- `skill_workflow_runtime/tasks/bug_triage_suffix_reretrieval_demo.json`
- `skill_workflow_runtime/tasks/bug_triage_suffix_reretrieval_skip_demo.json`

## 归档结果说明

当前 `runs/` 已整理为只保留 `report_ready/`。

其中最重要的归档是：

- `runs/report_ready/report_0629/bug_internal_report.md`
- `runs/report_ready/report_0629/bug_internal_report_with_review.md`
- `runs/report_ready/report_0701/support_internal_report.md`
- `runs/report_ready/report_0702/skill_runtime/support_budget_experiments/support_budget_summary.md`

如果你只想快速了解当前实验状态，优先看上面四份。

## 当前项目边界

当前最重要的边界有三条。

### 1. `compressed_history_carry` 还不是强基线

现有归档结果里，compressed-history 在 support 和 bug 两边都没有形成稳定收益：

- support 上 compression applied rate 是 `0.000`
- bug 上 compression applied rate 也很低

所以当前有效主线不是“动态压缩 running history”，而是：

- anchor stages 消化大证据
- 产出可复用结构化状态
- suffix stages 主要依赖最小必要状态继续推进

### 2. bug triage 的核心短板仍是 retrieval + taxonomy

当前 bug 结果已经说明：

- 结构化传递本身有效降低成本
- 但端到端质量还受 candidate recall 和 root-cause taxonomy 限制

因此，bug 侧后续重点不是再卷传输层，而是：

- retrieval / candidate generation
- primary-file decision boundary
- root-cause taxonomy
- evidence-label evaluation

### 3. 当前归档的 budget 实验以 deterministic 为主

`report_0702` 中已经有完整 budget 机制验证，但主要还是 deterministic 结果。  
本地模型 `local-vllm` 已完成代码接入和若干单样本调试，但还没有形成和 `report_ready` 同等级的系统归档结果。

## 公开 benchmark 扩展

当前扩展策略不是替换自建 workflow，而是在其上增加分层外部验证。

### 已接入

- `MULocBench`

当前已有：

- `skill_workflow_runtime/benchmark_adapters/mulocbench.py`
- `skill_workflow_runtime/export_mulocbench_tasks.py`
- `skill_workflow_runtime/summarize_mulocbench_runs.py`
- `skill_workflow_runtime/run_mulocbench_localization_experiments.py`

### 下一步计划

- `tau-Knowledge`：文档密集型 support / knowledge workflow 外部验证
- `SWE-bench / Multi-SWE-bench`：更完整的 repo workflow 外部验证

为此，仓库中还提供了 benchmark registry：

- `skill_workflow_runtime/skill_runtime/benchmark_registry.py`

导出当前 benchmark plan：

```bash
python3 skill_workflow_runtime/export_benchmark_plan.py
```

## 相关文档

建议按下面顺序阅读。

### 总体研究路线

- `docs/structured_transfer_research_roadmap.md`

### skill 统一设计范式

- `docs/unified_skill_design_paradigm.md`

### schema lint / review 原则

- `docs/skill_schema_lint_review_checklist.md`

### skill runtime 研究说明

- `docs/skill_workflow_runtime_README.md`
- `skill_workflow_runtime/README.md`

### 外部 benchmark 扩展计划

- `docs/external_benchmark_validation_plan.md`

## 一句话总结

当前项目已经完成了第一轮有效性验证：

- support 上，最小结构化状态传递已经证明“更省且不更差”
- bug triage 上，最小结构化状态传递已经证明“显著更省”，质量瓶颈主要转移到 retrieval 和 taxonomy
- skill runtime 上，`state + budget` 联合策略以及 tool-aware suffix subloop 已经跑通

因此，当前最值得继续推进的，不是再做一层新的 workflow 包装，而是：

- 补 bug 检索与定位的质量验证
- 补 local-vllm 归档结果
- 补公开 benchmark 外部验证
