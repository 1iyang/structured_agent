"""Support ticket workflow for state-transfer experiments."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .schemas import (
    SLOPolicyConfig,
    StageDependencyContract,
    StateDependencySpec,
    StateTransferMethodArtifacts,
    StoredStateArtifact,
    SupportCompressedMemory,
    SupportEligibilityState,
    SupportIntakeState,
    SupportPolicyEvidenceState,
    SupportResolutionPlanState,
    SupportResponseDraftState,
    SupportTicket,
    SupportTicketQualityMetrics,
    SupportTicketResult,
)
from .state_transfer import StructuredStateStore, routing_metrics_delta
from .transfer_methods import (
    InitialStructuredStage,
    RoutedStructuredStage,
    TransferMethodRuntime,
    default_history_compression_config,
    execute_compressed_history_carry,
    execute_full_history_carry,
    execute_structured_fixed_deps,
)
from .utils import compact_json, count_tokens_simple


class SupportPromptBuilder:
    """Prompt builder for the support ticket workflow."""

    ISSUE_TYPE_OPTIONS = (
        "damaged_item",
        "billing_refund",
        "delivery_delay",
        "account_access",
        "warranty_claim",
        "policy_exception",
        "unknown",
    )
    ELIGIBILITY_OPTIONS = (
        "approved",
        "denied",
        "needs_info",
        "partial",
        "escalate",
    )
    RESOLUTION_OPTIONS = (
        "refund",
        "replacement",
        "troubleshoot",
        "account_recovery",
        "goodwill_credit",
        "policy_denial",
        "clarification_request",
        "escalation",
    )
    POLICY_LABEL_OPTIONS = (
        "standard_remedy_available",
        "customer_proof_required",
        "timing_or_posting_uncertain",
        "identity_verification_required",
        "frontline_permission_limited",
        "limited_concession_available",
        "core_order_or_service_still_active",
        "coverage_not_available",
        "paid_alternative_available",
        "specialist_review_required",
        "urgent_escalation_required",
        "approval_not_guaranteed",
    )
    EVIDENCE_FACTOR_OPTIONS = (
        "missing_customer_evidence",
        "frontline_action_available",
        "limited_concession_available",
        "requested_remedy_not_supported",
        "specialist_queue_required",
        "high_time_sensitivity",
    )

    @classmethod
    def _enum_text(cls, values: Sequence[str]) -> str:
        return ", ".join(values)

    @classmethod
    def _eligibility_rubric(cls) -> str:
        return (
            "Eligibility taxonomy:\n"
            "- approved: the requested relief, or a standard customer-aligned remedy, can be carried out now on current facts.\n"
            "- denied: the requested relief is out of policy and there is no active narrower remedy or review path to offer.\n"
            "- needs_info: missing customer proof or concrete details are the main blocker to adjudicating the request.\n"
            "- partial: only a narrower remedy than the customer requested is currently supported.\n"
            "- escalate: queue ownership should move to a specialist or urgent team now because frontline cannot own the next decision.\n"
            "Boundary rules:\n"
            "- If both specialist review and missing customer evidence apply, prefer needs_info when the immediate blocker is the missing evidence.\n"
            "- Prefer escalate only when specialist routing is itself the current decision, not merely a possible later follow-up.\n"
            "- When policy supports only a smaller concession, such as a shipping-fee credit or a wait-and-verify billing path, prefer partial over approved or denied."
        )

    @classmethod
    def _resolution_rubric(cls) -> str:
        return (
            "Resolution taxonomy:\n"
            "- refund: grant the standard refund now.\n"
            "- replacement: create the replacement flow now.\n"
            "- troubleshoot: resolve the issue through non-compensation support steps.\n"
            "- account_recovery: run the standard access-verification or recovery path.\n"
            "- goodwill_credit: give a limited concession, such as a shipping-fee refund or service credit, while the main order or service stays intact.\n"
            "- policy_denial: deny the requested remedy and point to the best policy-safe alternative.\n"
            "- clarification_request: ask for evidence, waiting time, or final posting confirmation before any monetary remedy is promised.\n"
            "- escalation: specialist or urgent handoff is the primary operational next step.\n"
            "Boundary rules:\n"
            "- If eligibility is partial and a limited concession can be granted now, prefer goodwill_credit.\n"
            "- If eligibility is partial and the customer must first provide proof or wait for bank posting, prefer clarification_request.\n"
            "- Do not use policy_denial when a narrower concession or specialist review path still exists.\n"
            "- Use escalation when a specialist queue must own the next step; use account_recovery when the main path is identity verification and restoration, even if manual review may also be needed."
        )

    @classmethod
    def _evidence_factor_rubric(cls) -> str:
        return (
            "Policy-label taxonomy:\n"
            "- standard_remedy_available: policy allows a standard customer-aligned remedy when the required facts are satisfied.\n"
            "- customer_proof_required: photos, documents, or other customer-provided evidence are required before adjudication.\n"
            "- timing_or_posting_uncertain: bank posting, settlement timing, or timing windows limit what can be promised immediately.\n"
            "- identity_verification_required: account access or ownership recovery depends on verification signals.\n"
            "- frontline_permission_limited: frontline support cannot directly perform the requested protected action.\n"
            "- limited_concession_available: policy supports only a narrower remedy than the full requested outcome.\n"
            "- core_order_or_service_still_active: the main order, shipment, or service remains active, so only partial relief is available now.\n"
            "- coverage_not_available: warranty, refund, or standard coverage does not apply on current facts.\n"
            "- paid_alternative_available: the best policy-safe next step is a paid alternative or self-service path.\n"
            "- specialist_review_required: a specialist or exception queue must review or decide the case.\n"
            "- urgent_escalation_required: active outage, event timing, or other urgency requires prioritized escalation.\n"
            "- approval_not_guaranteed: the case may be reviewable, but the customer should not be told approval is likely or automatic.\n"
            "\n"
            "Evidence-factor taxonomy:\n"
            "- missing_customer_evidence: the next decision still depends on documents, proof, or customer confirmation not yet present.\n"
            "- frontline_action_available: frontline already has enough authority and information to take a standard next action.\n"
            "- limited_concession_available: policy supports some narrower customer-aligned remedy even if the full requested outcome is unavailable.\n"
            "- requested_remedy_not_supported: the exact remedy requested by the customer is not allowed on current facts.\n"
            "- specialist_queue_required: a specialist or protected queue must own the adjudication or system change.\n"
            "- high_time_sensitivity: the situation is unusually urgent because of active outage, imminent event, or similar operational pressure."
        )

    @classmethod
    def _issue_type_rubric(cls) -> str:
        return (
            "Issue-type taxonomy:\n"
            "- damaged_item: arrival damage, defective delivery, broken product on receipt.\n"
            "- billing_refund: charges, renewals, duplicate billing, payment holds, refund eligibility.\n"
            "- delivery_delay: shipping window misses, tracking delays, in-transit arrival complaints.\n"
            "- account_access: login failures, reset problems, SSO outages, verification-gated recovery.\n"
            "- warranty_claim: out-of-box or post-delivery product failures framed as warranty coverage questions.\n"
            "- policy_exception: requests for case-by-case exceptions to a normally final rule, such as non-refundable event passes.\n"
            "- unknown: only use when none of the above fits cleanly."
        )

    @staticmethod
    def build_ticket_intake(customer_message: str, account_context: str) -> str:
        return f"""You are the `ticket_intake` skill.

Your job is to turn the raw customer ticket into a reusable structured intake state.
Do not decide eligibility or draft the final reply yet.

Requirements:
- Output valid JSON only.
- `issue_type` must be exactly one of: {SupportPromptBuilder._enum_text(SupportPromptBuilder.ISSUE_TYPE_OPTIONS)}.
- `customer_goal` should describe what the customer is trying to achieve.
- `customer_emotion` should capture the tone at a high level.
- `hard_constraints` should capture policy or experience constraints that later stages must respect.
- `account_facts` should contain relevant concrete facts from the visible account context.
- `missing_information` should contain only facts that truly block a confident decision.
- `tone_guidance` should contain short directions for the final response tone.

{SupportPromptBuilder._issue_type_rubric()}

Schema:
{{
  "issue_type": enum,
  "customer_goal": string,
  "customer_emotion": string,
  "hard_constraints": [string],
  "account_facts": [string],
  "missing_information": [string],
  "tone_guidance": [string]
}}

Customer message:
{customer_message}

Account context:
{account_context or "(no additional account context)"}
"""

    @staticmethod
    def build_policy_evidence(ticket: SupportTicket, context_text: str) -> str:
        return f"""You are the `policy_evidence` skill.

You will receive a structured intake state and one mixed source pack containing policy excerpts, account notes, FAQ fragments, and prior support guidance.

Extract the smallest structured evidence state that later stages can reason over without rereading the entire pack.

Requirements:
- Output valid JSON only.
- `applied_policy_labels` must contain only labels from this list: {SupportPromptBuilder._enum_text(SupportPromptBuilder.POLICY_LABEL_OPTIONS)}.
- `evidence_factors` must contain only labels from this list: {SupportPromptBuilder._enum_text(SupportPromptBuilder.EVIDENCE_FACTOR_OPTIONS)}.
- `relevant_policy_points` should contain short factual policy statements grounded in the provided context.
- `account_findings` should summarize the most decision-relevant account or order facts.
- `candidate_actions` should list plausible support actions that appear supported by the evidence.
- `risk_flags` should capture policy ambiguity, missing proof, or exception risk.

{SupportPromptBuilder._evidence_factor_rubric()}

Schema:
{{
  "evidence_summary": string,
  "applied_policy_labels": [enum],
  "evidence_factors": [enum],
  "relevant_policy_points": [string],
  "account_findings": [string],
  "candidate_actions": [string],
  "risk_flags": [string]
}}

Ticket:
{ticket.ticket_id}

Customer message:
{ticket.customer_message}

Upstream context carried into this stage:
{context_text or "(no additional context)"}
"""

    @staticmethod
    def build_eligibility_assessment(ticket: SupportTicket, context_text: str) -> str:
        return f"""You are the `eligibility_assessment` skill.

You will receive the structured intake state and structured policy evidence. Decide the current best eligibility outcome.

Requirements:
- Output valid JSON only.
- `eligibility_decision` must be exactly one of: {SupportPromptBuilder._enum_text(SupportPromptBuilder.ELIGIBILITY_OPTIONS)}.
- `blocking_factors` should capture concrete reasons the request cannot proceed cleanly yet.
- `required_checks` should capture what must be verified before final resolution.
- `approved_actions` should list actions that appear allowed by the evidence.
- `confidence` should be a number between 0 and 1.

{SupportPromptBuilder._eligibility_rubric()}

Schema:
{{
  "eligibility_decision": enum,
  "decision_rationale": string,
  "blocking_factors": [string],
  "required_checks": [string],
  "approved_actions": [string],
  "confidence": number
}}

Ticket:
{ticket.ticket_id}

Customer message:
{ticket.customer_message}

Upstream context carried into this stage:
{context_text or "(no additional context)"}
"""

    @staticmethod
    def build_resolution_plan(ticket: SupportTicket, context_text: str) -> str:
        return f"""You are the `resolution_plan` skill.

You will receive structured intake, structured policy evidence, and a structured eligibility decision.
Produce the internal support plan that the team should follow.

Requirements:
- Output valid JSON only.
- `resolution_category` must be exactly one of: {SupportPromptBuilder._enum_text(SupportPromptBuilder.RESOLUTION_OPTIONS)}.
- `internal_actions` should list what the support team should do next.
- `customer_steps` should list what the customer needs to do, if anything.
- `escalation_needed` should be true only when a higher-touch queue is genuinely needed.
- `commitments` should contain promises that the final response may safely make.

{SupportPromptBuilder._resolution_rubric()}

Schema:
{{
  "resolution_category": enum,
  "internal_actions": [string],
  "customer_steps": [string],
  "escalation_needed": boolean,
  "escalation_reason": string,
  "commitments": [string]
}}

Ticket:
{ticket.ticket_id}

Customer message:
{ticket.customer_message}

Upstream context carried into this stage:
{context_text or "(no additional context)"}
"""

    @staticmethod
    def build_response_draft(ticket: SupportTicket, context_text: str) -> str:
        return f"""You are the `response_draft` skill.

You will receive structured intake, eligibility, and resolution-plan state. Draft the customer-facing reply.

Requirements:
- Output valid JSON only.
- Keep the message concise, polite, and operationally safe.
- Do not promise anything beyond the provided commitments.
- If more information is required, ask clearly for the exact missing items.

Schema:
{{
  "response_subject": string,
  "response_message": string,
  "follow_up_window": string,
  "commitments": [string]
}}

Ticket:
{ticket.ticket_id}

Customer message:
{ticket.customer_message}

Upstream context carried into this stage:
{context_text or "(no additional context)"}
"""

    @staticmethod
    def build_dependency_plan(ticket: SupportTicket, stage_name: str) -> str:
        return f"""You are the `structured_dependency_planner` skill.

This workflow does not use planner-selected optional reads for the current experiment.

Return valid JSON only.

Schema:
{{
  "selected_reads": [],
  "notes": [string]
}}

Ticket:
{ticket.ticket_id}

Current stage:
{stage_name}
"""

    @staticmethod
    def build_history_compression(
        ticket: SupportTicket,
        stage_name: str,
        history_text: str,
    ) -> str:
        return f"""You are the `history_compressor` skill.

Compress the support ticket workflow's running history into structured working memory for the next stage.

Keep:
- the issue type and customer goal
- the hard constraints and account facts that matter
- the applied policy labels that matter
- the evidence factors that control taxonomy boundaries
- the key policy points and candidate actions
- the current eligibility stance and next-step notes

Drop:
- repeated policy prose
- duplicated paraphrases
- details that no longer matter for the next stage

Requirements:
- Output valid JSON only.
- Keep fields concise.

Schema:
{{
  "issue_type": enum,
  "customer_goal": string,
  "hard_constraints": [string],
  "applied_policy_labels": [enum],
  "evidence_factors": [enum],
  "relevant_policy_points": [string],
  "candidate_actions": [string],
  "eligibility_decision": enum,
  "resolution_category": enum,
  "escalation_needed": boolean,
  "notes_for_next_stage": [string]
}}

Ticket:
{ticket.ticket_id}

Next stage:
{stage_name}

Current running history:
{history_text}
"""


class SupportTicketTransferAdapter:
    """Task-specific semantics for support ticket transfer."""

    def __init__(self, prompts: SupportPromptBuilder) -> None:
        self.prompts = prompts

    @staticmethod
    def _bullet_lines(items: Sequence[str], empty_text: str = "N/A") -> str:
        values = [item.strip() for item in items if item and item.strip()]
        if not values:
            return empty_text
        return "\n".join(f"- {item}" for item in values)

    @staticmethod
    def _json_view(payload: Dict) -> str:
        return compact_json(payload)

    def _render_compressed_memory(self, memory: SupportCompressedMemory) -> str:
        return self._json_view(memory.model_dump())

    @staticmethod
    def _source_pack_text(source_pack: str) -> str:
        return source_pack.strip()

    def _source_pack_artifact(self, ticket: SupportTicket, method_name: str) -> StoredStateArtifact:
        payload = self._source_pack_text(ticket.source_pack)
        return StoredStateArtifact(
            artifact_id=f"{method_name}:source_pack",
            method_name=method_name,
            stage_name="source_pack",
            artifact_type="source_pack",
            label="source_pack",
            payload_format="text",
            payload=payload,
            views={"full_text": payload},
        )

    def _intake_artifact(self, intake: SupportIntakeState, method_name: str) -> StoredStateArtifact:
        payload = intake.model_dump()
        summary = "\n".join(
            [
                f"issue_type: {intake.issue_type}",
                f"customer_goal: {intake.customer_goal}",
                f"customer_emotion: {intake.customer_emotion}",
                f"hard_constraints:\n{self._bullet_lines(intake.hard_constraints)}",
                f"account_facts:\n{self._bullet_lines(intake.account_facts)}",
                f"missing_information:\n{self._bullet_lines(intake.missing_information)}",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:ticket_intake",
            method_name=method_name,
            stage_name="ticket_intake",
            artifact_type="ticket_intake",
            label="ticket_intake",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def _policy_evidence_artifact(
        self,
        evidence: SupportPolicyEvidenceState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = evidence.model_dump()
        summary = "\n".join(
            [
                f"evidence_summary: {evidence.evidence_summary}",
                f"applied_policy_labels: {', '.join(evidence.applied_policy_labels) or 'N/A'}",
                f"evidence_factors: {', '.join(evidence.evidence_factors) or 'N/A'}",
                f"relevant_policy_points:\n{self._bullet_lines(evidence.relevant_policy_points)}",
                f"account_findings:\n{self._bullet_lines(evidence.account_findings)}",
                f"candidate_actions:\n{self._bullet_lines(evidence.candidate_actions)}",
                f"risk_flags:\n{self._bullet_lines(evidence.risk_flags)}",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:policy_evidence",
            method_name=method_name,
            stage_name="policy_evidence",
            artifact_type="policy_evidence",
            label="policy_evidence",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def _eligibility_artifact(
        self,
        eligibility: SupportEligibilityState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = eligibility.model_dump()
        summary = "\n".join(
            [
                f"eligibility_decision: {eligibility.eligibility_decision}",
                f"decision_rationale: {eligibility.decision_rationale}",
                f"blocking_factors:\n{self._bullet_lines(eligibility.blocking_factors)}",
                f"required_checks:\n{self._bullet_lines(eligibility.required_checks)}",
                f"approved_actions:\n{self._bullet_lines(eligibility.approved_actions)}",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:eligibility_state",
            method_name=method_name,
            stage_name="eligibility_assessment",
            artifact_type="eligibility_state",
            label="eligibility_state",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def _plan_artifact(
        self,
        plan: SupportResolutionPlanState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = plan.model_dump()
        summary = "\n".join(
            [
                f"resolution_category: {plan.resolution_category}",
                f"internal_actions:\n{self._bullet_lines(plan.internal_actions)}",
                f"customer_steps:\n{self._bullet_lines(plan.customer_steps)}",
                f"escalation_needed: {plan.escalation_needed}",
                f"commitments:\n{self._bullet_lines(plan.commitments)}",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:resolution_plan",
            method_name=method_name,
            stage_name="resolution_plan",
            artifact_type="resolution_plan",
            label="resolution_plan",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def _response_artifact(
        self,
        response: SupportResponseDraftState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = response.model_dump()
        summary = "\n".join(
            [
                f"response_subject: {response.response_subject}",
                f"follow_up_window: {response.follow_up_window or 'N/A'}",
                f"commitments:\n{self._bullet_lines(response.commitments)}",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:response_draft",
            method_name=method_name,
            stage_name="response_draft",
            artifact_type="response_draft",
            label="response_draft",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def build_structured_store(self, ticket: SupportTicket, method_name: str) -> StructuredStateStore:
        store = StructuredStateStore(method_name)
        store.add(self._source_pack_artifact(ticket, method_name))
        return store

    def build_history_seed(self, ticket: SupportTicket) -> str:
        sections = [f"[customer_message]\n{ticket.customer_message.strip()}"]
        if ticket.account_context.strip():
            sections.append(f"[account_context]\n{ticket.account_context.strip()}")
        sections.append(f"[source_pack]\n{self._source_pack_text(ticket.source_pack)}")
        return "\n\n".join(section for section in sections if section.strip())

    def initial_stage(self) -> InitialStructuredStage[SupportTicket]:
        return InitialStructuredStage(
            stage_name="ticket_intake",
            result_field="intake_state",
            schema_model=SupportIntakeState,
            prompt_builder=lambda ticket: self.prompts.build_ticket_intake(
                ticket.customer_message,
                ticket.account_context,
            ),
            artifact_builder=self._intake_artifact,
            max_tokens=320,
            notes=("Parsed the raw ticket into a reusable intake state.",),
        )

    def routed_stages(self) -> Sequence[RoutedStructuredStage[SupportTicket]]:
        return (
            RoutedStructuredStage(
                stage_name="policy_evidence",
                result_field="evidence_state",
                schema_model=SupportPolicyEvidenceState,
                prompt_builder=lambda ticket, context: self.prompts.build_policy_evidence(ticket, context),
                artifact_builder=self._policy_evidence_artifact,
                max_tokens=560,
            ),
            RoutedStructuredStage(
                stage_name="eligibility_assessment",
                result_field="eligibility_state",
                schema_model=SupportEligibilityState,
                prompt_builder=lambda ticket, context: self.prompts.build_eligibility_assessment(ticket, context),
                artifact_builder=self._eligibility_artifact,
                max_tokens=360,
            ),
            RoutedStructuredStage(
                stage_name="resolution_plan",
                result_field="plan_state",
                schema_model=SupportResolutionPlanState,
                prompt_builder=lambda ticket, context: self.prompts.build_resolution_plan(ticket, context),
                artifact_builder=self._plan_artifact,
                max_tokens=360,
            ),
            RoutedStructuredStage(
                stage_name="response_draft",
                result_field="response_state",
                schema_model=SupportResponseDraftState,
                prompt_builder=lambda ticket, context: self.prompts.build_response_draft(ticket, context),
                artifact_builder=self._response_artifact,
                max_tokens=460,
            ),
        )

    @staticmethod
    def fixed_dependency_contracts() -> Dict[str, StageDependencyContract]:
        return {
            "policy_evidence": StageDependencyContract(
                stage_name="policy_evidence",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="ticket_intake",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="source_pack",
                        view_name="full_text",
                        required=True,
                        max_items=1,
                    ),
                ],
            ),
            "eligibility_assessment": StageDependencyContract(
                stage_name="eligibility_assessment",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="ticket_intake",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="policy_evidence",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                ],
            ),
            "resolution_plan": StageDependencyContract(
                stage_name="resolution_plan",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="ticket_intake",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="policy_evidence",
                        view_name="summary",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="eligibility_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                ],
            ),
            "response_draft": StageDependencyContract(
                stage_name="response_draft",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="ticket_intake",
                        view_name="summary",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="eligibility_state",
                        view_name="summary",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="resolution_plan",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                ],
            ),
        }

    @staticmethod
    def planner_optional_specs(stage_name: str) -> Sequence[StateDependencySpec]:
        del stage_name
        return ()

    def build_planner_prompt(
        self,
        ticket: SupportTicket,
        stage_name: str,
        available_reads_text: str,
    ) -> str:
        del available_reads_text
        return self.prompts.build_dependency_plan(ticket, stage_name)

    def build_history_compression_prompt(
        self,
        ticket: SupportTicket,
        next_stage_name: str,
        history_text: str,
    ) -> str:
        return self.prompts.build_history_compression(ticket, next_stage_name, history_text)

    def history_compression_config(self):
        return default_history_compression_config(
            schema_model=SupportCompressedMemory,
            render_model=self._render_compressed_memory,
            skip_stage_names=("policy_evidence",),
        )


class SupportTicketWorkflow:
    """Run the support ticket state-transfer experiment."""

    def __init__(self, runner) -> None:
        self.runner = runner
        self.prompts = SupportPromptBuilder()
        self.adapter = SupportTicketTransferAdapter(self.prompts)
        self.runtime = TransferMethodRuntime(
            runner,
            token_counter=getattr(runner, "count_tokens", count_tokens_simple),
        )

    @staticmethod
    def load_tickets(path: str) -> List[SupportTicket]:
        source = Path(path).expanduser().resolve()
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".jsonl":
            payloads = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            raw = json.loads(text)
            payloads = raw["tickets"] if isinstance(raw, dict) and "tickets" in raw else raw
        return [SupportTicket.model_validate(item) for item in payloads]

    @staticmethod
    def _ticket_dir(base_output_dir: Path, ticket_id: str) -> Path:
        safe_name = re.sub(r"[^0-9A-Za-z._-]+", "_", ticket_id).strip("._") or "ticket"
        return base_output_dir / safe_name

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _set_completion_proxy(artifacts: StateTransferMethodArtifacts) -> None:
        artifacts.completion_proxy = min(
            float(bool(artifacts.intake_state)) * 0.16
            + float(bool(artifacts.evidence_state)) * 0.24
            + float(bool(artifacts.eligibility_state)) * 0.20
            + float(bool(artifacts.plan_state)) * 0.20
            + float(bool(artifacts.response_state)) * 0.20,
            1.0,
        )

    def _save_ticket_inputs(self, ticket: SupportTicket, ticket_dir: Path) -> None:
        self._write_json(ticket_dir / "input.json", ticket.model_dump())

    def _save_context_snapshots(
        self,
        ticket_dir: Path,
        structured_store: StructuredStateStore,
        history_seed: str,
    ) -> None:
        source_artifacts = structured_store.list("source_pack")
        source_pack = source_artifacts[0].views.get("full_text", "") if source_artifacts else ""
        self._write_text(ticket_dir / "raw_source_pack.txt", source_pack)
        self._write_text(ticket_dir / "full_history_seed.txt", history_seed)

    @staticmethod
    def _normalize_eval_text(text: str) -> str:
        return re.sub(r"[\s\W_]+", "", (text or "").lower())

    def _mention_coverage(self, targets: Sequence[str], corpus_parts: Sequence[str]) -> Optional[float]:
        values = [target.strip() for target in targets if target and target.strip()]
        if not values:
            return None
        normalized_corpus = self._normalize_eval_text("\n".join(part for part in corpus_parts if part))
        if not normalized_corpus:
            return 0.0
        hits = 0
        for target in values:
            normalized_target = self._normalize_eval_text(target)
            if normalized_target and normalized_target in normalized_corpus:
                hits += 1
        return hits / len(values)

    @staticmethod
    def _label_coverage(targets: Sequence[str], predictions: Sequence[str]) -> Optional[float]:
        gold = [str(value).strip() for value in targets if str(value).strip()]
        if not gold:
            return None
        predicted = {str(value).strip() for value in predictions if str(value).strip()}
        if not predicted:
            return 0.0
        hits = sum(1 for label in gold if label in predicted)
        return hits / len(gold)

    def _evaluate_method(
        self,
        ticket: SupportTicket,
        artifacts: StateTransferMethodArtifacts,
    ) -> SupportTicketQualityMetrics:
        reference = ticket.reference_answer
        intake = artifacts.intake_state or {}
        eligibility = artifacts.eligibility_state or {}
        plan = artifacts.plan_state or {}
        response = artifacts.response_state or {}
        evidence = artifacts.evidence_state or {}
        metrics = SupportTicketQualityMetrics(
            reference_available=reference is not None,
            final_issue_type=str(intake.get("issue_type") or "").strip(),
            final_eligibility_decision=str(eligibility.get("eligibility_decision") or "").strip(),
            final_resolution_category=str(plan.get("resolution_category") or "").strip(),
            final_escalation_needed=(
                bool(plan.get("escalation_needed"))
                if "escalation_needed" in plan
                else None
            ),
        )
        response_parts = [
            str(response.get("response_subject") or ""),
            str(response.get("response_message") or ""),
            "\n".join(str(value) for value in response.get("commitments") or []),
        ]
        metrics.response_commitment_coverage = self._mention_coverage(
            [str(value) for value in plan.get("commitments") or []],
            response_parts,
        )
        if reference is None:
            return metrics

        issue_type_key = self._normalize_eval_text(metrics.final_issue_type)
        gold_issue_type_key = self._normalize_eval_text(reference.issue_type)
        eligibility_key = self._normalize_eval_text(metrics.final_eligibility_decision)
        gold_eligibility_key = self._normalize_eval_text(reference.eligibility_decision)
        resolution_key = self._normalize_eval_text(metrics.final_resolution_category)
        gold_resolution_key = self._normalize_eval_text(reference.resolution_category)
        escalation_value = bool(metrics.final_escalation_needed)

        metrics.issue_type_match = bool(issue_type_key and issue_type_key == gold_issue_type_key)
        metrics.eligibility_match = bool(eligibility_key and eligibility_key == gold_eligibility_key)
        metrics.resolution_match = bool(resolution_key and resolution_key == gold_resolution_key)
        metrics.escalation_match = bool(metrics.final_escalation_needed is not None and escalation_value == reference.escalation_needed)
        metrics.exact_match = bool(
            metrics.issue_type_match
            and metrics.eligibility_match
            and metrics.resolution_match
            and metrics.escalation_match
        )
        metrics.policy_label_coverage = self._label_coverage(
            [str(value) for value in reference.key_policy_labels],
            [str(value) for value in evidence.get("applied_policy_labels") or []],
        )
        return metrics

    def run(
        self,
        tickets_file: str,
        artifact_output_dir: str,
        policy_config: Optional[SLOPolicyConfig] = None,
    ) -> List[SupportTicketResult]:
        policy = policy_config or SLOPolicyConfig(total_slo_seconds=8.0)
        tickets = self.load_tickets(tickets_file)
        artifact_base = Path(artifact_output_dir).expanduser().resolve()

        results: List[SupportTicketResult] = []
        for ticket in tickets:
            ticket_dir = self._ticket_dir(artifact_base, ticket.ticket_id)
            self._save_ticket_inputs(ticket, ticket_dir)

            structured_fixed_deps = StateTransferMethodArtifacts(method_name="structured_fixed_deps")
            full_history_carry = StateTransferMethodArtifacts(method_name="full_history_carry")
            compressed_history_carry = StateTransferMethodArtifacts(method_name="compressed_history_carry")

            structured_fixed_store = self.adapter.build_structured_store(ticket, "structured_fixed_deps")
            history_seed = self.adapter.build_history_seed(ticket)
            self._save_context_snapshots(ticket_dir, structured_fixed_store, history_seed)

            execute_structured_fixed_deps(
                task=ticket,
                policy_config=policy,
                artifacts=structured_fixed_deps,
                adapter=self.adapter,
                runtime=self.runtime,
                store=structured_fixed_store,
            )
            self._set_completion_proxy(structured_fixed_deps)

            execute_full_history_carry(
                task=ticket,
                policy_config=policy,
                artifacts=full_history_carry,
                adapter=self.adapter,
                runtime=self.runtime,
                history_seed=history_seed,
            )
            self._set_completion_proxy(full_history_carry)

            execute_compressed_history_carry(
                task=ticket,
                policy_config=policy,
                artifacts=compressed_history_carry,
                adapter=self.adapter,
                runtime=self.runtime,
                history_seed=history_seed,
            )
            self._set_completion_proxy(compressed_history_carry)

            structured_fixed_quality = self._evaluate_method(ticket, structured_fixed_deps)
            full_history_quality = self._evaluate_method(ticket, full_history_carry)
            compressed_history_quality = self._evaluate_method(ticket, compressed_history_carry)

            result = SupportTicketResult(
                ticket_id=ticket.ticket_id,
                customer_message=ticket.customer_message,
                source_pack_chars=len(ticket.source_pack),
                source_pack_token_estimate=count_tokens_simple(ticket.source_pack),
                policy_config=policy,
                structured_fixed_deps=structured_fixed_deps,
                full_history_carry=full_history_carry,
                compressed_history_carry=compressed_history_carry,
                structured_fixed_deps_quality=structured_fixed_quality,
                full_history_carry_quality=full_history_quality,
                compressed_history_carry_quality=compressed_history_quality,
                structured_fixed_deps_vs_full_history_metrics_delta=routing_metrics_delta(
                    structured_fixed_deps,
                    full_history_carry,
                ),
                compressed_history_carry_vs_full_history_metrics_delta=routing_metrics_delta(
                    compressed_history_carry,
                    full_history_carry,
                ),
            )
            self._write_json(ticket_dir / "support_ticket_result.json", asdict(result))
            results.append(result)

        return results
