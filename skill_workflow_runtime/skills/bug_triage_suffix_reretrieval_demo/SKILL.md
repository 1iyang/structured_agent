---
name: bug-triage-suffix-reretrieval-demo
description: "A positive demo skill that reopens retrieval in the suffix, immediately compresses the new artifact pool, and then returns to compact handoff state."
---

# Bug Triage Suffix Re-retrieval Demo

This skill is a focused positive example for the explicit suffix
`re_retrieval -> compression -> handoff` contract.

It starts from the same bug-style evidence abstraction used by the main bug
skill, then performs one follow-up retrieval subloop in the suffix when the
initial candidate pool is still missing the likely shared helper.

## Canonical Task

- task_id
- title
- description
- expected_behavior
- log_excerpt
- stacktrace_excerpt
- repo_subpaths
- repository_artifacts
- followup_repository_artifacts

## Input Profiles

### reretrieval_demo
- match: task_id, title, description, repository_artifacts, followup_repository_artifacts
- map.task_id: coalesce(task_id, issue_id, id, "bug-reretrieval-demo")
- map.title: coalesce(title, issue_title, summary)
- map.description: coalesce(description, issue_body, body, problem_description)
- map.expected_behavior: coalesce(expected_behavior, expected, desired_behavior, "Expected behavior not provided.")
- map.log_excerpt: coalesce(log_excerpt, logs, error_log, "No log excerpt provided.")
- map.stacktrace_excerpt: coalesce(stacktrace_excerpt, stacktrace, traceback, "No stacktrace provided.")
- map.repo_subpaths: listify(coalesce(repo_subpaths, []))
- map.repository_artifacts: listify(coalesce(repository_artifacts, []))
- map.followup_repository_artifacts: listify(coalesce(followup_repository_artifacts, []))

Use this profile for self-contained bug tasks that provide both an initial
artifact pool and a dedicated follow-up pool for suffix re-retrieval.

## Constraints

### retrieval_before_decision

Do not choose the final primary file until the initial candidate pool has been
compressed into reusable state.

### allow_suffix_read_only_tool_calls

This skill intentionally demonstrates a controlled suffix re-retrieval loop.

### allow_suffix_heavy_artifact_reads

The follow-up compression stage intentionally rereads the suffix artifact pool
before returning to compact state.

## Tools

### bug.local_repo_search
- kind: read_only

Fallback local repository search if the task does not provide artifact pools.

## Artifacts

### retrieved_artifacts

Initial high-recall artifact pool consumed by the prefix compression stage.

### followup_retrieved_artifacts

Suffix follow-up artifact pool that must be compressed immediately after the
re-retrieval stage.

### followup_candidate_pool

Task-provided follow-up artifact pool materialized in the prefix so the suffix
does not reopen raw task payloads directly.

## Output Schemas

### bug.signal_extractor_state
- bug_summary: string
- error_messages: list[string]
- file_hints: list[string]
- symbol_hints: list[string]
- suspected_modules: list[string]

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

### bug.followup_pool_state
- followup_pool_summary: string

### bug.followup_query_state
- retrieval_decision: enum[re_retrieve, proceed]
- followup_exact_queries: list[string]
- followup_semantic_queries: list[string]
- followup_rationale: string

### bug.followup_retrieval_state
- followup_retrieval_summary: string

### bug.followup_compression_state
- refined_candidates: list[string]
- refined_evidence_labels: list[string]
- compression_notes: list[string]

### bug.followup_decision_state
- primary_file: string
- decision_status: enum[supported, weak_evidence]
- decision_source: enum[base_evidence, refreshed_evidence]
- decision_rationale: string

### bug.handoff_state
- handoff_summary: string
- next_actions: list[string]

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

Extract stable bug signals from the raw issue text and traces.

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

Expand the extracted signals into an initial retrieval view.

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

Retrieve the initial candidate artifact pool.

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

Compress the initial artifact pool into reusable candidate state.

### followup_pool_seed
- executor: artifact.seed_from_task
- stage_type: retrieval
- workflow_phase: abstract
- anchor_stage: true
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: followup_repository_artifacts
- reads.state: none
- reads.artifacts: none
- writes: followup_pool_summary
- output_schema: bug.followup_pool_state
- writes.artifacts: followup_candidate_pool

Materialize the follow-up artifact pool before the workflow enters the suffix.

### followup_query_expansion
- executor: llm.json_stage
- stage_type: decision
- workflow_phase: route
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: bug_summary, file_hints, candidate_files, applied_evidence_labels, missing_evidence
- reads.artifacts: none
- writes: retrieval_decision, followup_exact_queries, followup_semantic_queries, followup_rationale
- output_schema: bug.followup_query_state

If the initial candidate pool still misses the shared helper path, prepare one
explicit suffix re-retrieval query set.

### suffix_retrieval_refresh
- executor: tool.retrieve_artifacts
- stage_type: retrieval
- workflow_phase: execute
- anchor_stage: false
- criticality: medium
- optional_stage: true
- tool_mode: read_only
- tool_intent: re_retrieval
- post_tool_policy: requires_compression
- reads.task: repo_subpaths
- reads.state: followup_exact_queries, followup_semantic_queries
- reads.artifacts: followup_candidate_pool
- uses.tools: bug.local_repo_search
- writes: followup_retrieval_summary
- output_schema: bug.followup_retrieval_state
- writes.artifacts: followup_retrieved_artifacts

Run a single suffix re-retrieval pass against the follow-up artifact pool.

### followup_evidence_compression
- executor: llm.json_stage
- stage_type: compression
- workflow_phase: execute
- anchor_stage: false
- criticality: medium
- optional_stage: true
- tool_mode: none
- prompt_cost_hint: 380
- reads.task: none
- reads.state: retrieval_decision, followup_rationale, followup_retrieval_summary
- reads.artifacts: followup_retrieved_artifacts
- writes: refined_candidates, refined_evidence_labels, compression_notes
- output_schema: bug.followup_compression_state

Immediately compress the suffix artifact pool back into compact reusable state.

### followup_decision
- executor: llm.json_stage
- stage_type: decision
- workflow_phase: verify
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: candidate_files, applied_evidence_labels, refined_candidates, refined_evidence_labels, compression_notes
- reads.artifacts: none
- writes: primary_file, decision_status, decision_source, decision_rationale
- output_schema: bug.followup_decision_state

Choose the final primary file from the refreshed suffix candidate state.

### handoff_plan
- executor: llm.json_stage
- stage_type: response
- workflow_phase: handoff
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: primary_file, decision_status, decision_rationale
- reads.artifacts: none
- writes: handoff_summary, next_actions
- output_schema: bug.handoff_state

Return the final result as a compact handoff package.
