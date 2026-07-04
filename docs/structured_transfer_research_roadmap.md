# Structured Transfer Research Roadmap

## 1. Core Research Question

This project is not mainly about whether structured input is better than raw input.

The core question is:

> When a workflow is known in advance, can a multi-stage agent replace full-history stage-to-stage transfer with minimal structured state, and then choose execution strategies under SLO constraints to reduce token and latency cost without meaningfully hurting quality?

This breaks into two sub-questions:

1. Is structured transfer itself effective on realistic multi-stage workflows?
2. If it is effective, can we build a stage-aware plugin that chooses transfer strategy online under remaining budget constraints?

---

## 2. Big Picture

The idea has two layers.

### 2.1 Research Layer

The problem can be framed as a constrained sequential decision problem.

- **State**:
  - current workflow stage
  - remaining SLO budget
  - remaining stages
  - raw input size
  - structured state size
  - structured state coverage
  - risk and missing-information signals

- **Action**:
  - use full history
  - use structured-only transfer
  - use structured transfer with minimal backread
  - reduce output budget
  - skip optional stage
  - enter conservative execution mode

- **Constraints**:
  - total SLO budget
  - required stages cannot be skipped
  - required raw-evidence stages must still read the needed raw input
  - downstream stages must receive their required dependency contract

- **Objective**:
  - reduce context transfer
  - reduce prompt/token cost
  - reduce latency
  - while keeping quality within an acceptable drop bound

### 2.2 System Layer

The implementation should be a skill-defined workflow runtime on top of the reasoning engine.

- the skill defines the workflow contract
- the runtime tracks stage execution and stored state
- the plugin reads current stage and remaining budget
- the plugin chooses a rule-based execution strategy

At the current stage of the project, this system layer should be implemented with explicit rules rather than a learned optimizer.

---

## 3. Working Thesis

The current working thesis is:

> Structured transfer is most valuable when raw evidence must be read early, but later stages can mostly operate on distilled state.

This implies:

- not every workflow will benefit equally
- long workflows are more favorable than short ones
- workflows with repeated downstream reasoning are more favorable than workflows dominated by one heavy raw-evidence stage
- the usefulness of structured transfer depends on whether the structured state captures reusable decision-relevant evidence rather than just producing compressed summaries

---

## 4. Why This Is Not Just a Prompting Project

The project should not be framed as a prompt trick.

The main contribution is the combination of:

1. explicit workflow stages
2. explicit intermediate state
3. explicit transfer contracts
4. explicit budget-aware execution choice

The important distinction is:

- not "make the model clever enough to infer what matters"
- but "make the runtime explicit about what later stages need"

This is why the skill/workflow contract matters so much.

---

## 5. Research Plan by Phase

### Phase 1: Prove Static Structured Transfer Is Worth Doing

Goal:

Compare fixed transfer strategies on realistic multi-stage workflows:

- `full_history_carry`
- `structured_fixed_deps`
- `compressed_history_carry`

Primary question:

> Does minimal structured transfer reduce context and cost without unacceptable quality loss?

Status:

- already underway
- enough evidence exists to keep pursuing the idea

Expected outputs:

- per-workflow transfer reports
- per-stage context and latency traces
- workflow-specific quality metrics

---

### Phase 2: Identify When Structured Transfer Helps

Goal:

Move from "does it help somewhere?" to "under what workflow conditions does it help?"

Key factors:

- number of stages
- whether raw evidence is concentrated in early stages
- whether later stages can reuse structured evidence
- whether structured state is sufficient for downstream decisions

This phase should produce a clearer criterion:

> Structured transfer helps when the workflow contains one or more expensive early evidence stages followed by multiple downstream reasoning stages that do not need to repeatedly reread raw context.

Status:

- partially supported by existing support ticket workflow results
- still needs stronger bug triage workflow stabilization around retrieval and taxonomy

---

### Phase 3: Stabilize Task Taxonomy and State Design

Goal:

Reduce measurement noise caused by fuzzy labels or overly answer-like state design.

What to improve:

- tighter enum boundaries
- evidence-level intermediate state rather than answer-level hints
- quality metrics that measure preserved evidence and commitments, not just surface text overlap

This phase is especially important for the support ticket workflow and the bug triage workflow, where category boundaries can otherwise dominate the results.

Status:

- active
- support ticket workflow already moved to stricter enums and policy-label coverage

---

### Phase 4: Add a Rule-Based Budget-Aware Plugin

Goal:

Keep the model focused on task execution, while letting a plugin choose the execution strategy online.

The plugin should read:

- current stage
- remaining SLO
- stages left
- required vs optional context
- structured state sufficiency
- risk signals

The plugin should choose:

- `raw_required_only`
- `structured_only`
- `structured_plus_minimal_backread`
- reduced output budget
- optional-stage skipping
- conservative mode

Primary question:

> Can a rule-based online plugin outperform a static transfer strategy under budget constraints?

Status:

- implemented in the skill-defined workflow runtime
- archived support ticket `state + budget` experiments already show crossover budgets where the minimal-transfer skill can open an optional suffix branch while fuller baselines cannot
- still needs local-vllm and public-benchmark replication

---

### Phase 5: Lift Workflow Definitions into Skills

Goal:

Stop treating workflows as hand-coded one-offs and move the workflow contract into skill packages.

The skill should define:

- stage graph
- schema per stage
- required dependency contract
- optional reads
- stage metadata
- budget hints

Primary question:

> Can the same runtime and plugin operate across multiple skills with only skill-level workflow definitions changing?

Status:

- partially implemented
- skill manifests, stage metadata, artifact / tool contracts, and strategy interfaces are already in place in `skill_workflow_runtime/`
- still needs broader benchmark-facing skill coverage

---

### Phase 6: Consider Formal Optimization

Goal:

Only after enough traces exist, revisit whether to build a more formal constrained optimizer.

Possible future directions:

- cost model
- quality-risk model
- constrained search
- contextual bandit
- CMDP-style controller

Current advice:

- do not start here
- first collect enough evidence that the rule-based policy space is meaningful

---

## 6. What Current Experiments Already Show

### 6.1 What Is Already Supported

The current experiments are strong enough to justify continuing.

The strongest evidence so far comes from the support ticket workflow:

- prompt tokens reduced by roughly half
- transferred context reduced by well over half
- downstream transferred context reduced by roughly three quarters
- wall time improved
- quality did not collapse and in some runs slightly improved

This supports the claim that:

> On long workflows where later stages mostly consume distilled state, structured transfer is effective.

### 6.2 What Is Not Yet Proven

Current experiments do **not** yet show that:

- structured transfer is universally faster
- all workflows benefit equally
- a dynamic plugin already beats static strategies
- the framework is already fully general

Earlier exploratory runs already showed an important counterexample:

- structured transfer can reduce token/context cost
- but end-to-end latency may still be dominated by an early heavy raw-evidence stage

So the current evidence supports continuing, but not over-claiming.

---

## 7. What the Current Evidence Means

The correct interpretation is not:

> structured transfer always wins

It is:

> structured transfer is worth pursuing because it is already effective on at least one realistic long workflow, and the conditions under which it helps are becoming clear.

That is enough to continue into the next stage of the project.

---

## 8. Recommended Immediate Next Steps

### Step 1: Archive local-vllm support ticket workflow runs

The support ticket workflow is currently the cleanest place to complete a real-model archive because:

- it has a long stage graph
- it has a clear split between raw-evidence stages and structured downstream stages
- it directly exposes downstream transfer savings

Priority:

- re-run the support ticket workflow with local vLLM
- preserve `state + budget` summaries under project-local output paths
- compare deterministic and local-vllm budget decisions on the same cases

### Step 2: Stabilize bug triage retrieval and localization

Bug triage now needs quality stabilization more than another transfer-layer rewrite.

Priority:

- raise candidate recall
- reduce within-candidate primary-file mistakes
- keep localization metrics separated from downstream taxonomy metrics

### Step 3: Extend external validation

Use the current layered plan:

- `MULocBench` for retrieval / primary-file localization
- `tau-Knowledge` for document-heavy support-like workflows
- `SWE-bench / Multi-SWE-bench` for end-to-end repository workflows

Question:

> do the current minimal-transfer and state-sufficient-boundary claims survive on public tasks?

### Step 4: Keep the claim narrow and cumulative

Do not widen the thesis faster than the evidence:

- keep the support ticket workflow and bug triage workflow as the main mechanism tests
- treat public benchmarks as layered external validation
- keep the claim about minimal-transfer execution, not generic skill usefulness

---

## 9. What to Avoid Right Now

The following are likely premature:

- building a formal optimizer before collecting enough traces
- claiming generality before cross-workflow plugin validation
- making structured state too close to final labels
- adding too many strategy choices before the core transfer gains are stable

The current priority is not maximal cleverness.

The current priority is:

> establish a clean, believable chain from known workflow -> structured state -> lower downstream cost -> acceptable quality.

---

## 10. Decision on Whether to Continue

Yes, the project should continue.

The current experiments are sufficient to show that structured transfer is not just a theoretical idea; it already delivers meaningful downstream efficiency gains on at least one realistic long workflow.

However, further validation is still required before making stronger claims about:

- dynamic strategy superiority
- cross-workflow generality
- formal constrained optimization

So the right move is:

1. continue
2. stay focused on the support ticket workflow and the bug triage workflow
3. strengthen bug retrieval / localization and archive local-vllm runs next
4. defer formal optimization until later

---

## 11. Current Project Position

The project is currently best described as:

> an investigation into workflow-aware structured state transfer for multi-stage agents under budget constraints, with early evidence of effectiveness on long downstream-structured workflows.

That is a solid place to be.
