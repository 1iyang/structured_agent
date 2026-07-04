# Skill-Defined Workflow Runtime

This README uses the same core terms as the root README:

- `support ticket workflow`
- `bug triage workflow`
- `minimal-transfer`
- `state-sufficient boundary`

This folder is a fully isolated prototype for defining workflows with
`SKILL.md` packages and executing tasks from those definitions.

It does not import or depend on the current `structured_agent/` code path.

## What this prototype does

- loads a skill package from `skills/<name>/SKILL.md`
- validates the stage graph and state dependencies
- executes stages in manifest order
- passes only declared task fields and state fields into each stage
- records a stage-by-stage trace
- records stage-level token usage for task/state/artifact views and LLM prompts

This is intentionally narrow. It is meant to prove the shape of:

1. skill-defined workflow contracts
2. runtime execution from those contracts
3. task execution isolated from the current experiment codebase

For the formal design write-up and schema review criteria, see:

- [docs/unified_skill_design_paradigm.md](/Users/loren/Desktop/code/agent/structured_agent/docs/unified_skill_design_paradigm.md)
- [docs/skill_schema_lint_review_checklist.md](/Users/loren/Desktop/code/agent/structured_agent/docs/skill_schema_lint_review_checklist.md)
- [docs/external_benchmark_validation_plan.md](/Users/loren/Desktop/code/agent/structured_agent/docs/external_benchmark_validation_plan.md)

## Theoretical Positioning

This prototype is best understood as a **research vehicle for explicit workflow contracts**, not as proof that skill packaging alone improves efficiency.

The core claim is deliberately narrow:

> a skill can make stage boundaries, evidence dependencies, reusable state, and tool usage explicit enough that a runtime can enforce minimal-transfer execution instead of blindly carrying full history forward.

That is different from the stronger and less defensible claim:

> if a workflow is written as a skill, token usage will automatically go down.

The distinction matters.

### Weak value vs. strong value

There are two levels of value in this design.

#### Weak value: workflow organization

At a minimum, a skill package gives us:

- explicit stage order
- explicit task/state/artifact reads
- explicit output schemas
- explicit tool contracts
- explicit stage metadata

That alone is already useful engineering structure, but it does **not** guarantee lower token cost or better behavior.

#### Strong value: dependency-boundary control

The stronger value only appears when the skill contract actually changes the information flow:

- large raw evidence is consumed once in a small number of anchor stages
- those anchor stages produce reusable structured state
- downstream stages are state-sufficient and do not repeatedly reread the full raw evidence
- strategy layers can act on stage metadata instead of inferring workflow shape from prompts

Only in this stronger case does skill-defined execution become meaningfully connected to lower transfer cost, better controllability, or future stage-level routing strategies.

### What would make this design meaningless

The design would have limited research significance if:

- the heavy backbone of the workflow is unchanged
- downstream stages still depend on the same large raw context
- structured state is just a verbose restatement of the raw input
- skill metadata exists, but no execution or routing behavior changes because of it

In that case, the skill package is mainly a cleaner way to describe the workflow, not a real change in system behavior.

### What would make this design meaningful

The design becomes meaningful when it can support a stronger experimental statement:

> after a workflow reaches a state-sufficient boundary, later stages can continue operating primarily on declared structured state rather than full history or full evidence bundles.

This is the real theoretical role of the skill contract in this project:

- not "skills are magic"
- but "skills provide an executable place to define state-sufficient boundaries"

The project now formalizes this into a shared skill design chain:

`Normalize -> Observe -> Abstract -> Route -> Execute -> Verify -> Handoff`

with `Constraints` and `Memory / Provenance` treated as cross-cutting layers.
The runtime does not require each skill to expose all seven phases as separate
stages, but the schema and stage design should preserve these distinctions so
that minimal-transfer experiments remain interpretable.

### Current interpretation of this prototype

So the right interpretation of the current runtime is:

- it already proves that workflow structure, tools, artifacts, schemas, and stage semantics can be lifted into skill-owned metadata
- it does **not yet** prove that every skillified workflow has practical efficiency gains
- it is therefore a good substrate for testing whether a given workflow truly benefits from minimal-transfer execution

That is why the main question going forward is not:

> can we represent a workflow as a skill?

but rather:

> when a workflow is represented as a skill, does that representation expose state-sufficient boundaries that materially reduce repeated evidence transfer?

The repository now also carries a machine-readable benchmark expansion registry
for the next validation layer:

- [skill_workflow_runtime/skill_runtime/benchmark_registry.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/skill_runtime/benchmark_registry.py)

You can export the current plan into project-local JSON and Markdown with:

```bash
python3 skill_workflow_runtime/export_benchmark_plan.py
```

The first public benchmark adapter is now in place for `MULocBench`:

- [benchmark_adapters/mulocbench.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/benchmark_adapters/mulocbench.py)
- [export_mulocbench_tasks.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/export_mulocbench_tasks.py)
- [summarize_mulocbench_runs.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/summarize_mulocbench_runs.py)
- [run_mulocbench_localization_experiments.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/run_mulocbench_localization_experiments.py)

### FAQ-style clarifications

#### If the backbone of the workflow does not change, does skillization still matter?

Yes, but mainly at the level of workflow organization and inspectability.

Skillization becomes practically meaningful only when it changes the information flow:

- heavy evidence is consumed in a small number of anchor stages
- later stages become state-sufficient
- repeated raw-evidence transfer is actually reduced

Without that, the main benefit is a cleaner contract, not necessarily lower cost.

#### Why restrict the scope to task-specific workflows?

Because the intended setting is not arbitrary open-ended agency. It is a narrower class of problems where:

- the task family is stable
- the stage structure is known
- useful state-sufficient boundaries may exist

This restriction makes the hypothesis testable and keeps the claims aligned with the current evidence.

#### If the workflows are task-specific, why use LLMs at all?

Not all task-specific workflows need LLMs.

For fully rule-based, low-ambiguity tasks, direct code is often simpler and better.

This project instead targets workflows that are structurally stable but still require:

- semantic compression
- evidence integration
- soft judgment
- natural-language generation

That is why the support ticket workflow and the bug triage workflow remain good examples: they are not open-ended, but they are also not fully reducible to fixed rules.

#### Why use skills instead of only code constraints?

Direct code constraints can certainly implement a single workflow.

The role of skills here is different: they lift stage boundaries, state interfaces, artifact dependencies, and tool usage into an explicit contract that the runtime can inspect and enforce.

So the claim is not:

> skills are inherently better than code

but rather:

> for LLM-dependent task-specific workflows, skills provide a better control surface for expressing state boundaries and minimal-transfer execution than burying those decisions inside implementation details.

## Layout

```text
skill_workflow_runtime/
  run_skill_workflow.py
  run_support_budget_experiments.py
  skill_runtime/
    loader.py
    models.py
    runtime.py
    builtins.py
  skills/
    support_ticket/SKILL.md
    support_ticket_full_evidence/SKILL.md
    support_ticket_raw_context_baseline/SKILL.md
    bug_triage/SKILL.md
    bug_triage_suffix_reretrieval_demo/SKILL.md
  tasks/
    support_ticket_demo.json
    support_ticket_alias_profile_demo.json
    support_ticket_realistic_profile_demo.json
    support_ticket_account_recovery_complex_demo.json
    support_ticket_low_risk_ready_demo.json
    bug_triage_demo.json
    bug_triage_alias_profile_demo.json
    bug_triage_labeled_profile_demo.json
    bug_triage_complex_demo.json
    bug_triage_suffix_reretrieval_demo.json
    bug_triage_suffix_reretrieval_skip_demo.json
```

## Skill format

Each skill is a Markdown file with YAML frontmatter plus structured workflow
sections.

Frontmatter defines:

- `name`
- `description`

Body sections define:

- `## Canonical Task`
- `## Input Profiles`
- `## Constraints`
- `## Tools`
- `## Artifacts`
- `## Output Schemas`
- `## Workflow`

Each input profile is declared as a `###` heading and includes bullet fields:

- `match`
- `map.<canonical_field>`

Each workflow stage is declared as a `###` heading inside `## Workflow` and
includes bullet fields:

- `executor`
- `stage_type`
- `workflow_phase`
- `anchor_stage`
- `criticality`
- `optional_stage`
- `tool_mode`
- `tool_intent`
- `post_tool_policy`
- `prompt_cost_hint`
- `reads.task`
- `reads.state`
- `reads.artifacts`
- `uses.tools`
- `writes`
- `output_schema`
- `writes.artifacts`

Any free text under a profile heading becomes profile notes. Any free text under
a stage heading becomes the stage instruction.

Stage metadata lets the runtime preserve workflow semantics in the trace instead
of leaving them implicit in prose. The current prototype recognizes:

- `stage_type`: `intake`, `evidence`, `retrieval`, `compression`, `decision`, `plan`, `review`, `response`
- `workflow_phase`: `observe`, `abstract`, `route`, `execute`, `verify`, `handoff`
- `anchor_stage`: whether the stage is a reusable raw-to-state boundary
- `criticality`: `high`, `medium`, or `low`
- `optional_stage`: whether the workflow can skip the stage in future strategy layers
- `tool_mode`: `none`, `read_only`, or `side_effecting`
- `tool_intent`: `none`, `retrieval`, `re_retrieval`, `lookup`, or `side_effect`
- `post_tool_policy`: `none`, `requires_compression`, or `direct_state`
- `prompt_cost_hint`: an optional static prompt-cost prior for later strategy layers

Tool-aware stages now have an explicit contract beyond `tool_mode`:

- `tool_intent` says why the tool is being opened
- `post_tool_policy` says how the workflow is expected to return to reusable
  state after the tool call
- if a suffix stage declares `tool_intent = re_retrieval`, the next stage must
  be a non-tool compression stage that rereads the produced artifacts and
  returns the flow to compact state before later suffix reasoning continues

At runtime these metadata fields are lifted into explicit stage groups:

- `anchor_stages`: reusable raw-to-state boundary stages
- `prefix_stages`: all stages up to and including the last anchor stage
- `suffix_stages`: all stages after the anchor boundary
- `optional_stages`: stages that future strategies may skip
- `stage_type_groups`: a grouped view keyed by `stage_type`
- `workflow_phase_groups`: a grouped view keyed by the unified skill phase chain

The runtime:

1. scores input profiles and chooses the best profile that can fully
   normalize the task
2. normalizes the raw task into a canonical task with rule-based mappings
3. validates the workflow graph, tool usage, and artifact contracts
4. enforces monotonic workflow-phase order and no duplicate state writes
5. executes stages only against canonical task fields, prior state, and
   declared artifacts
6. records state and artifact provenance so later analysis can trace which
   stage produced each reusable field

The runtime also exposes a first strategy interface. The current default
strategy executes every stage unchanged, but it already receives:

- workflow-level stage groups
- per-stage metadata
- per-stage snapshots containing completed stages, remaining stages, and the
  currently available state/artifact keys

This keeps the execution loop ready for later rule-based policy plugins without
rewriting the workflow contract.

There is now also a first explicit policy plugin:

- `default`: execute every stage
- `suffix-reretrieval-policy`: decide whether optional suffix re-retrieval
  stages should run, based on the currently available structured state and
  artifact availability rather than prompt-local improvisation
- `support-response-budget-policy`: decide whether optional support response
  refinement should run, based on structured handoff complexity plus remaining
  prompt budget

The policy plugin can also take a simple budget gate through
`--strategy-budget-threshold`.

In the current prototype, this threshold is interpreted as a prompt-token
budget ceiling for opening optional suffix branches:

- the runtime tracks consumed prompt tokens stage by stage
- the runtime estimates the prompt-token cost of the contiguous optional branch
  starting at the current stage
- skills may also provide a static `prompt_cost_hint` for optional stages
- the strategy reads the current structured state plus consumed prompt tokens
- the current branch-cost model is `max(runtime_estimator, skill_metadata_hint)`
- if the structured state requests `re_retrieve` but the remaining prompt
  budget is smaller than that combined branch-cost estimate, the optional
  suffix branch stays closed

This is intentionally still simple, but it is enough to start a first
`state + budget` joint-decision experiment.

Normalization rules support a small expression language:

- direct field paths such as `ticket_id`
- `coalesce(a, b, c)` for field aliases and defaults
- `concat(a, b, c)` for merged text context
- `listify(x)` for list-like canonical fields such as repo scopes or
  artifact arrays

Output schemas provide typed state contracts for each stage. Current runtime
validation supports:

- `string`
- `integer`
- `number`
- `boolean`
- `enum[a, b, c]`
- `list[string]`
- `list[enum[a, b, c]]`

## Built-in executors

This prototype uses a small built-in executor registry. The manifest names the
executor, and the runtime dispatches to the matching function.

Current built-ins cover two sample domains plus a small set of generic helpers:

- `artifact.*`
- `tool.*`
- `support.*`
- `bug.*`

The point is not the heuristics themselves. The point is that the workflow is
defined by the skill package, while execution is handled by a generic runtime.

## Run the demos

Support:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/support_ticket \
  --task skill_workflow_runtime/tasks/support_ticket_demo.json
```

If `--output` is omitted, the runtime writes the run record into a
project-local path under `runs/skill_runtime/<skill>/`. When a non-default
strategy or a budget threshold is used, the filename now includes those
settings so different experiment runs do not overwrite each other.

The support skill now uses the generic `llm.json_stage` executor for the full
support chain. The bug skill now has a complete four-stage prefix contract:

- `signal_extractor -> llm.json_stage`
- `query_expansion -> llm.json_stage`
- `high_recall_retrieval -> tool.retrieve_artifacts`
- `evidence_screen -> llm.json_stage`

By default the runtime uses a deterministic offline backend so the prototype
remains runnable without external services.

For support experiments, there are now three directly comparable skills:

- `support_ticket`: suffix stages read only reusable state
- `support_ticket_full_evidence`: the same suffix stages keep rereading the original `context_pack`
- `support_ticket_raw_context_baseline`: no anchor reuse; the workflow keeps the support stages explicit while rereading raw context end to end

This makes it possible to test whether explicit state boundaries actually
reduce repeated evidence transfer in the suffix, and whether the structured
prefix is worth paying for relative to a simpler raw-context pipeline.

The support ticket workflows now also carry an explicit optional suffix branch:

- `response_refinement`: an optional review-stage branch that adds extra
  response-shaping guidance for urgent, blocked, escalation, or risk-sensitive
  cases

This gives support a clean `state + budget` experiment surface:

- under no budget limit, all three skills can open the refinement branch on
  complex cases
- under a shared crossover budget, the minimal-transfer skill can still afford
  the branch while the full-evidence and raw-context baselines cannot
- on low-risk ready cases, the branch stays closed even with ample budget,
  because the structured state does not justify the extra pass

The support skills now also expose a clearer semantic chain:

- `ticket_intake -> observe`
- `policy_evidence -> abstract`
- `eligibility_assessment -> route`
- `resolution_plan -> execute`
- `resolution_verification / response_refinement -> verify`
- `response_draft -> handoff`

and the bug skill now exposes:

- `signal_extractor -> observe`
- `query_expansion / high_recall_retrieval / evidence_screen -> abstract`
- `hypothesis_shortlist -> route`
- `triage_decision -> execute`
- `decision_verification -> verify`
- `fix_plan -> handoff`

Support using a different external task shape but the same skill:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/support_ticket \
  --task skill_workflow_runtime/tasks/support_ticket_realistic_profile_demo.json
```

Support using a third alias-heavy external task shape:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/support_ticket \
  --task skill_workflow_runtime/tasks/support_ticket_alias_profile_demo.json
```

Support using the stronger raw-context end-to-end baseline:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/support_ticket_raw_context_baseline \
  --task skill_workflow_runtime/tasks/support_ticket_realistic_profile_demo.json
```

Support state + budget experiment matrix:

```bash
python3 skill_workflow_runtime/run_support_budget_experiments.py
```

This writes both:

- `runs/skill_runtime/support_budget_experiments/support_budget_summary.json`
- `runs/skill_runtime/support_budget_experiments/support_budget_summary.md`

The summary automatically derives:

- a `tight` budget that closes the optional refinement branch everywhere
- a shared `crossover` budget that keeps the minimal-transfer skill open while
  closing the full-evidence and raw-context baselines when such a crossover
  exists
- an `open_all` budget that reopens the branch for every skill

Support state + budget experiments on local vLLM and GPU 1:

```bash
python3 skill_workflow_runtime/run_support_budget_experiments.py \
  --llm-mode local-vllm \
  --llm-model /data/model/qwen-4b \
  --cuda-visible-devices 1
```

Bug triage:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/bug_triage \
  --task skill_workflow_runtime/tasks/bug_triage_demo.json
```

Bug triage using a labeled-style external task shape but the same skill:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/bug_triage \
  --task skill_workflow_runtime/tasks/bug_triage_labeled_profile_demo.json
```

Bug triage using a different field vocabulary but the same skill:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/bug_triage \
  --task skill_workflow_runtime/tasks/bug_triage_alias_profile_demo.json
```

Positive suffix re-retrieval contract demo:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/bug_triage_suffix_reretrieval_demo \
  --task skill_workflow_runtime/tasks/bug_triage_suffix_reretrieval_demo.json
```

This demo intentionally shows the full explicit subloop:

- materialize a follow-up artifact pool in the prefix
- reopen retrieval once in the suffix with `tool_intent = re_retrieval`
- immediately pass the new artifact bundle through a compression stage
- finish from compressed state rather than carrying the suffix artifact bundle to the end

Strategy-gated suffix re-retrieval demo, branch opens:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/bug_triage_suffix_reretrieval_demo \
  --task skill_workflow_runtime/tasks/bug_triage_suffix_reretrieval_demo.json \
  --strategy suffix-reretrieval-policy
```

Strategy-gated suffix re-retrieval demo, branch stays closed:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/bug_triage_suffix_reretrieval_demo \
  --task skill_workflow_runtime/tasks/bug_triage_suffix_reretrieval_skip_demo.json \
  --strategy suffix-reretrieval-policy
```

Strategy-gated suffix re-retrieval demo, branch suppressed by budget:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/bug_triage_suffix_reretrieval_demo \
  --task skill_workflow_runtime/tasks/bug_triage_suffix_reretrieval_demo.json \
  --strategy suffix-reretrieval-policy \
  --strategy-budget-threshold 1
```

Bug triage directly against the current realistic dataset shape:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/bug_triage \
  --task examples/bug_triage_issues_realistic.json \
  --task-index 0
```

## LLM executor modes

The runtime supports two LLM executor modes:

- `deterministic` (default)
- `local-vllm`

Deterministic mode is an offline backend intended for local validation of the
workflow contract and schema enforcement.

To use a local vLLM-backed JSON executor such as a local Qwen checkpoint:

```bash
python3 skill_workflow_runtime/run_skill_workflow.py \
  --skill skill_workflow_runtime/skills/support_ticket \
  --task skill_workflow_runtime/tasks/support_ticket_demo.json \
  --llm-mode local-vllm \
  --llm-model /data/model/qwen-4b \
  --cuda-visible-devices 1 \
  --gpu-memory-utilization 0.55 \
  --max-num-seqs 8 \
  --enforce-eager
```

For this offline skill runtime, the local-vLLM backend now uses memory-safer
defaults than a serving-oriented setup:

- `enforce_eager = true`
- `max_num_seqs = 16`

That is intentional. This runtime executes one small workflow stage at a time,
so high serving concurrency and aggressive graph warmup usually hurt stability
more than they help throughput.

You can also provide these through the environment instead of CLI flags:

- `SKILL_RUNTIME_LLM_MODE`
- `SKILL_RUNTIME_LLM_MODEL`
- `SKILL_RUNTIME_MAX_MODEL_LEN`
- `SKILL_RUNTIME_TENSOR_PARALLEL_SIZE`
- `SKILL_RUNTIME_GPU_MEMORY_UTILIZATION`
- `CUDA_VISIBLE_DEVICES`
- `SKILL_RUNTIME_MAX_NUM_SEQS`
- `SKILL_RUNTIME_MAX_NUM_BATCHED_TOKENS`
- `SKILL_RUNTIME_ENFORCE_EAGER`
- `SKILL_RUNTIME_ENABLE_PREFIX_CACHING`
- `SKILL_RUNTIME_LLM_MAX_OUTPUT_TOKENS`

This runtime no longer calls a remote or HTTP-served API for stage execution.
When `local-vllm` is selected, it instantiates the model locally and runs each
JSON stage directly against that local engine.

If the requested startup profile fails with a memory-related engine-init error,
the backend now retries with more conservative profiles before giving up. The
active profile is recorded in the run output and printed by the CLI.

### Local vLLM troubleshooting

If startup fails with a message like `Free memory on device ... is less than
desired GPU memory utilization`, the problem is GPU pressure rather than a
workflow bug.

If startup fails with a warmup message about `dummy requests`, the issue is
usually that the local engine is still trying to reserve too much serving-style
concurrency for an offline experiment run. In that case, `max_num_seqs` is the
first knob to lower.

In that case:

1. Run `nvidia-smi` on the server and check which processes are holding VRAM.
2. Free enough VRAM for the selected checkpoint to load.
3. Keep `--enforce-eager` enabled for offline workflow runs.
4. Lower `--max-num-seqs` if the engine still fails during warmup.
5. Retry the same command.
6. If the machine is shared, switch to a smaller or quantized local model.
7. After VRAM pressure is reduced, optionally lower `--max-model-len` to trim
   KV-cache usage.

Lowering `--gpu-memory-utilization` alone does not solve the problem when the
device has only a very small amount of truly free VRAM at startup.

## Token accounting

Every run now records:

- per-stage `task_view_tokens`
- per-stage `state_view_tokens`
- per-stage `artifact_view_tokens`
- per-stage `state_output_tokens`
- per-stage `artifact_output_tokens`
- per-stage `prompt_tokens` for `llm.json_stage`

The run-level summary also aggregates:

- `canonical_task_tokens`
- `input_view_tokens_total`
- `output_tokens_total`
- `prompt_tokens_total`
- `prefix_*`, `suffix_*`, and `anchor_*` prompt/input totals

This is intended to support experiments that compare minimal-transfer skills
against both full-evidence suffix baselines and raw-context end-to-end
baselines without leaving the skill runtime.

## Design intent

This prototype is designed around three layers:

1. **Evidence layer**
   - task inputs and tool-like artifacts
2. **State abstraction layer**
   - stages that turn evidence into reusable state
3. **Workflow reasoning layer**
   - later stages that consume declared state dependencies

The next step after this prototype would be adding:

- pluggable LLM runners
- artifact stores
- stage policies and dynamic strategy selection
