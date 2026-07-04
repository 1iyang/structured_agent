---
name: support-ticket-full-evidence-baseline
description: "A support ticket workflow baseline whose suffix stages continue to read the full context pack."
---

# Support Ticket Full-Evidence Baseline

This skill mirrors the support ticket workflow but allows suffix stages to continue
reading the original `context_pack`. It exists as an experimental baseline for
measuring whether state-only suffix execution actually reduces repeated
evidence transfer.

## Canonical Task

- task_id
- customer_message
- context_pack

## Input Profiles

### support_generic
- match: customer_message
- map.task_id: coalesce(task_id, ticket_id, id, case_id, request_id, "support-task")
- map.customer_message: coalesce(customer_message, message, user_message, request_text)
- map.context_pack: coalesce(context_pack, supporting_context, concat(account_context, source_pack), concat(order_snapshot, policy_excerpt), concat(order_context, policy_text), concat(metadata_context, policy_excerpt), source_pack, policy_excerpt, account_context, order_snapshot, policy_text, context)

Use this profile for same-domain support inputs that keep the customer message
but vary the surrounding evidence fields.

### support_demo
- match: id, customer_message, order_snapshot, policy_excerpt
- map.task_id: id
- map.customer_message: customer_message
- map.context_pack: concat(order_snapshot, policy_excerpt)

### support_realistic
- match: ticket_id, customer_message, account_context, source_pack
- map.task_id: ticket_id
- map.customer_message: customer_message
- map.context_pack: concat(account_context, source_pack)

## Constraints

### shared_anchor_contract

This baseline shares the same anchor-state contract as the minimal workflow so
quality deltas can be attributed mainly to suffix evidence access.

### allow_suffix_raw_task_reads

This baseline intentionally allows route / execute / verify / handoff stages to
keep reading the original task evidence so it can serve as a full-evidence
reference line.

### blocker_before_commitment

If the preferred path is still viable but blocked, downstream stages must keep
that path visible instead of collapsing the case into a denial.

### no_unsupported_promise

Final customer-facing output must not promise an unsupported remedy before the
required blocker is cleared or the required review completes.

## Output Schemas

### support.ticket_intake_state
- issue_type: enum[billing, account_access, damaged_item, delivery_issue, general_support]
- customer_goal: string
- urgency: enum[high, normal]
- missing_information: list[string]

### support.policy_evidence_state
- policy_signals: list[enum[customer_proof_required, specialist_review_required, standard_remedy_available, approval_not_guaranteed, limited_concession_available, identity_verification_required]]
- evidence_factors: list[enum[missing_customer_evidence, frontline_action_available, specialist_queue_required, high_time_sensitivity]]
- preferred_resolution_path: enum[replacement, refund, escalation, goodwill_credit, account_recovery]
- blocking_requirements: list[enum[customer_evidence, customer_verification, internal_review]]
- candidate_actions: list[string]
- risk_flags: list[string]

### support.eligibility_state
- eligibility_decision: enum[approved, needs_info, escalate, partial, denied]
- eligibility_rationale: string

### support.resolution_state
- resolution_category: enum[clarification_request, escalation, goodwill_credit, policy_denial, account_recovery, replacement, refund]
- resolution_steps: list[string]

### support.verification_state
- handoff_status: enum[ready, blocked, escalate]
- response_guardrails: list[string]

### support.response_refinement_state
- refinement_focus: list[enum[blocker_clarity, policy_guardrails, urgency_tone, escalation_expectation, risk_disclosure]]
- refined_response_notes: list[string]

### support.response_state
- response_message: string

## Workflow

### ticket_intake
- executor: llm.json_stage
- stage_type: intake
- workflow_phase: observe
- anchor_stage: true
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: customer_message, context_pack
- reads.state: none
- reads.artifacts: none
- writes: issue_type, customer_goal, urgency, missing_information
- output_schema: support.ticket_intake_state
- writes.artifacts: none

Turn the raw ticket into a compact intake state.

### policy_evidence
- executor: llm.json_stage
- stage_type: evidence
- workflow_phase: abstract
- anchor_stage: true
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: customer_message, context_pack
- reads.state: issue_type, urgency, missing_information
- reads.artifacts: none
- writes: policy_signals, evidence_factors, preferred_resolution_path, blocking_requirements, candidate_actions, risk_flags
- output_schema: support.policy_evidence_state
- writes.artifacts: none

Extract reusable policy and evidence signals from the raw material. Preserve
both the likely resolution path and the unmet blocking requirements so later
stages can distinguish "not yet ready" from "not allowed".

### eligibility_assessment
- executor: llm.json_stage
- stage_type: decision
- workflow_phase: route
- anchor_stage: false
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: customer_message, context_pack
- reads.state: policy_signals, evidence_factors, preferred_resolution_path, blocking_requirements
- reads.artifacts: none
- writes: eligibility_decision, eligibility_rationale
- output_schema: support.eligibility_state
- writes.artifacts: none

Baseline suffix stage that still has access to the original evidence pack
while producing the same structured decision schema. Use `denied` only when
policy blocks the remedy even after blockers are cleared. Use `needs_info`
when the likely path remains available but blockers are still unmet.

### resolution_plan
- executor: llm.json_stage
- stage_type: plan
- workflow_phase: execute
- anchor_stage: false
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: customer_message, context_pack
- reads.state: issue_type, eligibility_decision, preferred_resolution_path, blocking_requirements, missing_information, candidate_actions
- reads.artifacts: none
- writes: resolution_category, resolution_steps
- output_schema: support.resolution_state
- writes.artifacts: none

Baseline suffix stage that continues to consult the original evidence pack. If
the case is `needs_info` but the preferred path remains available after the
blockers are cleared, keep that operational path and make the first steps about
collecting the blocker.

### resolution_verification
- executor: llm.json_stage
- stage_type: review
- workflow_phase: verify
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: customer_message, context_pack
- reads.state: eligibility_decision, resolution_category, resolution_steps, blocking_requirements, missing_information, policy_signals, risk_flags
- reads.artifacts: none
- writes: handoff_status, response_guardrails
- output_schema: support.verification_state
- writes.artifacts: none

Baseline verification stage that still has access to the original evidence pack
while producing the same guardrail contract for the response stage.

### response_refinement
- executor: llm.json_stage
- stage_type: review
- workflow_phase: verify
- anchor_stage: false
- criticality: low
- optional_stage: true
- tool_mode: none
- prompt_cost_hint: 260
- reads.task: customer_message, context_pack
- reads.state: urgency, eligibility_decision, resolution_category, missing_information, blocking_requirements, handoff_status, response_guardrails, risk_flags
- reads.artifacts: none
- writes: refinement_focus, refined_response_notes
- output_schema: support.response_refinement_state
- writes.artifacts: none

Optional refinement stage that still rereads the original evidence pack before
the final response is drafted. It exists as the full-evidence budget baseline
for comparison against the state-only variant.

### response_draft
- executor: llm.json_stage
- stage_type: response
- workflow_phase: handoff
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: customer_message, context_pack
- reads.state: customer_goal, eligibility_decision, resolution_category, resolution_steps, handoff_status, response_guardrails, refinement_focus, refined_response_notes
- reads.artifacts: none
- writes: response_message
- output_schema: support.response_state
- writes.artifacts: none

Baseline suffix response stage with continued access to the full evidence pack.
