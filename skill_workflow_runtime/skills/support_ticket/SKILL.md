---
name: support-ticket-skill
description: "A skill-defined support ticket workflow with reusable intermediate state."
---

# Support Ticket Skill

This skill defines a compact support ticket workflow that turns raw ticket evidence
into reusable state and then drives downstream decision and response stages
from that state.

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
but vary the surrounding evidence fields. It prefers an already bundled
`context_pack`, then falls back to common snapshot-plus-policy combinations.

### support_demo
- match: id, customer_message, order_snapshot, policy_excerpt
- map.task_id: id
- map.customer_message: customer_message
- map.context_pack: concat(order_snapshot, policy_excerpt)

Use this profile for small demo-style tasks that separate operational snapshot
and policy excerpt into two fields.

### support_realistic
- match: ticket_id, customer_message, account_context, source_pack
- map.task_id: ticket_id
- map.customer_message: customer_message
- map.context_pack: concat(account_context, source_pack)

Use this profile for the realistic support ticket dataset, where supporting
context and policy material arrive as mixed corpus text.

## Constraints

### minimal_state_handoff

After the anchor boundary, downstream stages should prefer reusable structured
state over rereading the original ticket evidence.

### blocker_before_commitment

If the preferred path is still viable but blocked, downstream stages must carry
both the blocker and the still-valid path instead of collapsing the case into a
hard denial.

### no_unsupported_promise

Final customer-facing output must not promise an unsupported remedy before the
required blocker is cleared or the required review completes.

## Output Schemas

### support.ticket_intake_state
- issue_type: enum[billing, account_access, damaged_item, delivery_issue, general_support]
- customer_goal: string
- urgency: enum[high, normal]
- missing_information: list[string]

Compact intake state that captures the customer intent and obvious evidence
gaps without carrying the full raw ticket forward.

### support.policy_evidence_state
- policy_signals: list[enum[customer_proof_required, specialist_review_required, standard_remedy_available, approval_not_guaranteed, limited_concession_available, identity_verification_required]]
- evidence_factors: list[enum[missing_customer_evidence, frontline_action_available, specialist_queue_required, high_time_sensitivity]]
- preferred_resolution_path: enum[replacement, refund, escalation, goodwill_credit, account_recovery]
- blocking_requirements: list[enum[customer_evidence, customer_verification, internal_review]]
- candidate_actions: list[string]
- risk_flags: list[string]

Reusable policy and evidence abstraction for downstream decision stages.

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
both the likely resolution path and the unmet blocking requirements. A blocking
requirement means the case can still move forward after that requirement is
satisfied; it is not itself a denial.

### eligibility_assessment
- executor: llm.json_stage
- stage_type: decision
- workflow_phase: route
- anchor_stage: false
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: policy_signals, evidence_factors, preferred_resolution_path, blocking_requirements
- reads.artifacts: none
- writes: eligibility_decision, eligibility_rationale
- output_schema: support.eligibility_state
- writes.artifacts: none

Decide the eligibility state from the reusable policy and evidence signals
without rereading the original ticket evidence. Use `denied` only when policy
itself blocks the requested remedy even if missing requirements were later
satisfied. Use `needs_info` when the likely resolution path remains available
but one or more blocking requirements are still unmet.

### resolution_plan
- executor: llm.json_stage
- stage_type: plan
- workflow_phase: execute
- anchor_stage: false
- criticality: high
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: issue_type, eligibility_decision, preferred_resolution_path, blocking_requirements, missing_information, candidate_actions
- reads.artifacts: none
- writes: resolution_category, resolution_steps
- output_schema: support.resolution_state
- writes.artifacts: none

Choose the operational resolution path from structured state without
rereading the original ticket evidence. If the case is `needs_info` but the
preferred path remains `replacement`, `refund`, `goodwill_credit`, or
`account_recovery` once blockers are cleared, keep that operational path and
make the first steps about collecting the blocker.

### resolution_verification
- executor: llm.json_stage
- stage_type: review
- workflow_phase: verify
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: eligibility_decision, resolution_category, resolution_steps, blocking_requirements, missing_information, policy_signals, risk_flags
- reads.artifacts: none
- writes: handoff_status, response_guardrails
- output_schema: support.verification_state
- writes.artifacts: none

Verify that the planned resolution is safe to hand off. Preserve whether the
case is ready, blocked, or escalation-bound, and spell out guardrails that the
response stage must respect.

### response_refinement
- executor: llm.json_stage
- stage_type: review
- workflow_phase: verify
- anchor_stage: false
- criticality: low
- optional_stage: true
- tool_mode: none
- prompt_cost_hint: 120
- reads.task: none
- reads.state: urgency, eligibility_decision, resolution_category, missing_information, blocking_requirements, handoff_status, response_guardrails, risk_flags
- reads.artifacts: none
- writes: refinement_focus, refined_response_notes
- output_schema: support.response_refinement_state
- writes.artifacts: none

Produce optional response-shaping guidance from structured state only. This
stage should add value mainly on urgent, blocked, escalation, or high-risk
cases, without rereading the original ticket evidence.

### response_draft
- executor: llm.json_stage
- stage_type: response
- workflow_phase: handoff
- anchor_stage: false
- criticality: medium
- optional_stage: false
- tool_mode: none
- reads.task: none
- reads.state: customer_goal, eligibility_decision, resolution_category, resolution_steps, handoff_status, response_guardrails, refinement_focus, refined_response_notes
- reads.artifacts: none
- writes: response_message
- output_schema: support.response_state
- writes.artifacts: none

Draft the final customer-facing response from structured state without
rereading the original ticket evidence.
