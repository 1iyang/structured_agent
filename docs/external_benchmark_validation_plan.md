# External Benchmark Validation Plan

## 1. Purpose

This document turns the current benchmark-expansion idea into a concrete
repository-level plan.

The goal is not to replace the current support and bug workflows. The goal is
to strengthen the overall claim with a layered validation strategy:

1. keep the current internal workflows as the main mechanism tests
2. add a localization-focused public benchmark for bug retrieval quality
3. add public end-to-end benchmarks to test whether the current skill runtime
   and minimal-transfer claims transfer beyond self-built tasks

## 2. Working Thesis

The project should keep making one narrow claim:

> skill-defined workflow contracts matter when they expose state-sufficient
> boundaries that reduce repeated evidence transfer and enable explicit
> budget-aware execution choices.

This means benchmark expansion should support the existing thesis, not replace
it with a broader and weaker "skill systems in general" story.

## 3. Validation Layers

### 3.1 Main Mechanism Validation

Use the current internal workflows as the primary evidence source:

- support ticket workflow
- bug triage workflow

These remain the best place to test:

- anchor-to-suffix state boundaries
- minimal structured transfer versus fuller baselines
- suffix optional-stage control
- tool-aware suffix loops

### 3.2 Localization Validation

Use `MULocBench` as a bug-side public benchmark that isolates:

- retrieval recall
- candidate generation quality
- primary-file localization

This matters because end-to-end bug metrics can hide a simple retrieval failure.
The current repository already treats this separation as important in the bug
workflow metrics.

### 3.3 External Transfer Validation

Use public benchmarks to test whether the current skill-defined workflow runtime still
helps outside the self-built tasks:

- `tau-Knowledge` for document-heavy support-like workflows
- `SWE-bench` for end-to-end repository workflows
- `Multi-SWE-bench` for cross-language repository workflows

## 4. Benchmark Roles

### 4.1 tau-Knowledge

Use this benchmark to validate the support-side claim under heavier public
document workloads.

It is a good fit because it keeps the important shape of the current support
story:

- known workflow
- large evidence bundles
- tool-adjacent reasoning
- later stages that should not repeatedly reread the full evidence pack

In this repository, `tau-Knowledge` should answer:

- does a public document-heavy workflow still expose stable state-sufficient
  boundaries?
- does the skill contract materially change later-stage information flow?

### 4.2 MULocBench

Use this benchmark to validate the bug-side retrieval and localization layer.

In this repository, `MULocBench` should answer:

- can the retrieval prefix recover the gold file into the candidate pool?
- once the gold file is present, does structured suffix reasoning localize it
  correctly?

This benchmark should not be treated as a full replacement for the current bug
workflow. Its role is intentionally narrower.

The repository now includes a first adapter layer for this benchmark:

- [skill_workflow_runtime/benchmark_adapters/mulocbench.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/benchmark_adapters/mulocbench.py)
- [skill_workflow_runtime/export_mulocbench_tasks.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/export_mulocbench_tasks.py)
- [skill_workflow_runtime/summarize_mulocbench_runs.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/summarize_mulocbench_runs.py)
- [skill_workflow_runtime/run_mulocbench_localization_experiments.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/run_mulocbench_localization_experiments.py)

The adapter currently assumes one of two setups:

1. you export raw benchmark records and convert them into the current bug-skill
   task format
2. you also maintain local copies of benchmark repositories, and the adapter
   writes `repo_subpaths` that point to those local checkouts

Example conversion command:

```bash
python3 skill_workflow_runtime/export_mulocbench_tasks.py \
  --input /path/to/mulocbench.json \
  --output runs/skill_runtime/mulocbench/tasks.json \
  --repos-root /path/to/mulocbench_repos \
  --repo-layout nested
```

There is now also a one-command first-batch runner:

```bash
python3 skill_workflow_runtime/run_mulocbench_localization_experiments.py \
  --input /path/to/mulocbench.json \
  --repos-root /path/to/mulocbench_repos \
  --repo-layout nested \
  --limit 8
```

After running the bug skill over the exported tasks, you can summarize the
localization metrics with:

```bash
python3 skill_workflow_runtime/summarize_mulocbench_runs.py \
  --task-file runs/skill_runtime/mulocbench/tasks.json \
  --runs-dir runs/skill_runtime/mulocbench/runs \
  --output runs/skill_runtime/mulocbench/summary.json
```

### 4.3 SWE-bench / Multi-SWE-bench

Use these benchmarks to validate the end-to-end repository-workflow claim.

In this repository, they should answer:

- do minimal-transfer claims survive on public issue-to-repo workflows?
- is structured state useful beyond isolated localization?
- do the skill-defined workflow runtime and minimal-transfer ideas transfer across repository languages?

## 5. What Changes in the Experiment Design

The current design does not need a rewrite. It needs a layered extension.

The revised structure should be:

1. main mechanism validation:
   - current support ticket workflow
   - current bug triage workflow
2. localization validation:
   - `MULocBench`
3. external transfer validation:
   - `tau-Knowledge`
   - `SWE-bench`
   - `Multi-SWE-bench`

This means:

- the internal workflows remain the main claim-bearing experiments
- public benchmarks become external validation layers
- bug localization and bug end-to-end workflow should be evaluated separately

## 6. What Should Stay Fixed

When public benchmarks are added, several parts of the current framing should
stay fixed:

- the claim remains about minimal-transfer workflow execution, not generic skill
  usefulness
- internal support and bug tasks remain the main mechanism tests
- benchmark adapters should map public tasks into canonical workflow contracts
  rather than rewrite the thesis around benchmark-specific prompts
- bug retrieval metrics should remain separated from downstream decision metrics

## 7. Repository Integration

The repository now carries this benchmark-expansion plan in two forms:

1. narrative documentation in this file and in the root README
2. a machine-readable registry in
   [skill_workflow_runtime/skill_runtime/benchmark_registry.py](/Users/loren/Desktop/code/agent/structured_agent/skill_workflow_runtime/skill_runtime/benchmark_registry.py)

The registry is meant to become the single structured source of truth for:

- benchmark role
- validation layer
- integration status
- recommended metrics
- required adapter work

## 8. Recommended Next Steps

1. finish the current support and bug internal experiment passes
2. add the first public-benchmark adapter for `MULocBench`
3. add the support-side public adapter for `tau-Knowledge`
4. only after that, move to `SWE-bench` / `Multi-SWE-bench`

This order keeps the benchmark expansion aligned with the current research
story instead of letting the benchmark tail wag the thesis dog.
