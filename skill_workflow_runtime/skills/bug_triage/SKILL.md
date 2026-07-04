---
name: bug-triage-skill
description: "A skill-defined bug triage workflow with shared evidence abstraction before decision stages."
---

# Bug Triage Skill

This skill defines a bug triage workflow that extracts signals, expands
retrieval views, retrieves a candidate evidence pool, compresses that evidence,
and then performs downstream triage reasoning from structured state.

## Canonical Task

- task_id
- title
- description
- expected_behavior
- log_excerpt
- stacktrace_excerpt
- repo_subpaths
- repository_artifacts

## Input Profiles

### bug_generic
- match: title, description
- map.task_id: coalesce(task_id, issue_id, id, bug_id, case_id, "bug-task")
- map.title: coalesce(title, issue_title, summary)
- map.description: coalesce(description, issue_body, body, problem_description)
- map.expected_behavior: coalesce(expected_behavior, expected, desired_behavior, acceptance_criteria, "Expected behavior not provided.")
- map.log_excerpt: coalesce(log_excerpt, logs, error_log, trace_excerpt, "No log excerpt provided.")
- map.stacktrace_excerpt: coalesce(stacktrace_excerpt, stacktrace, traceback, stack_trace, "No stacktrace provided.")
- map.repo_subpaths: listify(coalesce(repo_subpaths, repository_scope, search_roots, code_scope, []))
- map.repository_artifacts: listify(coalesce(repository_artifacts, retrieved_artifacts, code_artifacts, []))

Use this profile for same-domain bug inputs where the wording is similar but
field names differ. It can normalize either repo-scope tasks or tasks that
arrive with precomputed repository artifacts.

### bug_demo
- match: id, title, description, expected_behavior, log_excerpt, stacktrace_excerpt, repository_artifacts
- map.task_id: id
- map.title: title
- map.description: description
- map.expected_behavior: expected_behavior
- map.log_excerpt: log_excerpt
- map.stacktrace_excerpt: stacktrace_excerpt
- map.repo_subpaths: listify([])
- map.repository_artifacts: listify(repository_artifacts)

Use this profile for self-contained bug triage tasks that already include
candidate repository artifacts.

### bug_labeled_artifacts
- match: issue_id, title, description, expected_behavior, log_excerpt, stacktrace_excerpt, repository_artifacts
- map.task_id: issue_id
- map.title: title
- map.description: description
- map.expected_behavior: expected_behavior
- map.log_excerpt: log_excerpt
- map.stacktrace_excerpt: stacktrace_excerpt
- map.repo_subpaths: listify([])
- map.repository_artifacts: listify(repository_artifacts)

Use this profile for labeled bug datasets that still provide artifact evidence
but carry legacy issue identifiers and extra offline-evaluation fields.

### bug_realistic_repo_scope
- match: issue_id, title, description, expected_behavior, log_excerpt, stacktrace_excerpt, repo_subpaths
- map.task_id: issue_id
- map.title: title
- map.description: description
- map.expected_behavior: expected_behavior
- map.log_excerpt: log_excerpt
- map.stacktrace_excerpt: stacktrace_excerpt
- map.repo_subpaths: listify(repo_subpaths)
- map.repository_artifacts: listify([])

Use this profile for the current realistic bug dataset, where the task defines
repo scope and the runtime must retrieve repository artifacts through a tool.

## Constraints

### retrieval_before_decision

Primary-file selection should happen only after a shared candidate pool has
been retrieved and compressed into reusable evidence state.

### candidate_grounding

Downstream triage stages should prefer files grounded in the screened candidate
pool rather than inventing a new fix locus from scratch.

### verify_before_fix_plan

Before producing the final fix plan, the workflow should explicitly check
whether the current decision is supported, weakly grounded, or still evidence
limited.

## Tools

### bug.local_repo_search
- kind: read_only

Search the local repository within the declared `repo_subpaths` and return
candidate file artifacts with snippets and lightweight scores.

## Artifacts

### retrieved_artifacts

High-recall candidate repository artifacts produced by the retrieval stage and
consumed by later evidence-compression stages.

## Output Schemas

### bug.signal_extractor_state
- bug_summary: string
- error_messages: list[string]
- file_hints: list[string]
- symbol_hints: list[string]
- suspected_modules: list[string]

Stable signal abstraction from raw issue text and traces.

### bug.query_expansion_state
- exact_queries: list[string]
- symbol_queries: list[string]
- semantic_queries: list[string]
- graph_queries: list[string]

### bug.retrieval_state
- retrieval_summary: string

### bug.evidence_screen_state
- candidate_files: list[string]
- applied_evidence_labels: list[string]
- missing_evidence: list[string]

### bug.hypothesis_state
- hypotheses: list[string]

### bug.triage_decision_state
- primary_file: string
- root_cause_category: enum[configuration, state_management, error_recovery, artifact_lifecycle, unknown]
- decision_rationale: string

### bug.fix_plan_state
- fix_plan: list[string]

### bug.verification_state
- decision_status: enum[supported, weak_evidence, needs_more_retrieval]
- review_notes: list[string]

## Workflow

### signal_extractor
- executor: llm.json_stage
- stage_type: evidence
- workflow_phase: observe
- anchor_stage: true
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: title, description, log_excerpt, stacktrace_excerpt
- reads.state: none
- reads.artifacts: none
- writes: bug_summary, error_messages, file_hints, symbol_hints, suspected_modules
- output_schema: bug.signal_extractor_state

Extract stable bug signals from the issue and traces.

### query_expansion
- executor: llm.json_stage
- stage_type: retrieval
- workflow_phase: abstract
- anchor_stage: true
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: bug_summary, error_messages, file_hints, symbol_hints, suspected_modules
- reads.artifacts: none
- writes: exact_queries, symbol_queries, semantic_queries, graph_queries
- output_schema: bug.query_expansion_state

Expand the extracted signals into retrieval-friendly query views.

### high_recall_retrieval
- executor: tool.retrieve_artifacts
- stage_type: retrieval
- workflow_phase: abstract
- anchor_stage: true
- criticality: high
- optional_stage: false
- tool_mode: read_only
- tool_intent: retrieval
- post_tool_policy: requires_compression
- reads.task: repository_artifacts, repo_subpaths
- reads.state: exact_queries, symbol_queries, semantic_queries, graph_queries
- reads.artifacts: none
- uses.tools: bug.local_repo_search
- writes: retrieval_summary
- output_schema: bug.retrieval_state
- writes.artifacts: retrieved_artifacts

Retrieve a high-recall pool of candidate code artifacts.

### evidence_screen
- executor: llm.json_stage
- stage_type: compression
- workflow_phase: abstract
- anchor_stage: true
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: bug_summary, retrieval_summary
- reads.artifacts: retrieved_artifacts
- writes: candidate_files, applied_evidence_labels, missing_evidence
- output_schema: bug.evidence_screen_state
- writes.artifacts: none

Compress the retrieved evidence into reusable decision state.

### hypothesis_shortlist
- executor: bug.hypothesis_shortlist
- stage_type: decision
- workflow_phase: route
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: candidate_files, applied_evidence_labels
- writes: hypotheses
- output_schema: bug.hypothesis_state
- reads.artifacts: none
- writes.artifacts: none

Create a shortlist of root-cause hypotheses.

### triage_decision
- executor: bug.triage_decision
- stage_type: decision
- workflow_phase: execute
- anchor_stage: false
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: candidate_files, applied_evidence_labels, hypotheses
- writes: primary_file, root_cause_category, decision_rationale
- output_schema: bug.triage_decision_state
- reads.artifacts: none
- writes.artifacts: none

Select the primary fix locus and root-cause category.

### decision_verification
- executor: bug.decision_verification
- stage_type: review
- workflow_phase: verify
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: candidate_files, applied_evidence_labels, missing_evidence, primary_file, root_cause_category, decision_rationale
- writes: decision_status, review_notes
- output_schema: bug.verification_state
- reads.artifacts: none
- writes.artifacts: none

Check whether the current triage decision is sufficiently grounded in the
screened evidence pool before turning it into a final fix plan.

### fix_plan
- executor: bug.fix_plan
- stage_type: plan
- workflow_phase: handoff
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: primary_file, root_cause_category, decision_status, review_notes
- writes: fix_plan
- output_schema: bug.fix_plan_state
- reads.artifacts: none
- writes.artifacts: none

Turn the triage decision into a concrete fix plan.
