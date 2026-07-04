from __future__ import annotations

import gc
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol

from .models import OutputFieldDefinition, OutputSchemaDefinition, StageDefinition

try:
    from vllm import LLM, SamplingParams
except ImportError:  # pragma: no cover - handled by runtime error when local mode is used
    LLM = None  # type: ignore[assignment]
    SamplingParams = None  # type: ignore[assignment]


class JsonGenerationBackend(Protocol):
    def generate_json(
        self,
        *,
        stage: StageDefinition,
        instruction: str,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
    ) -> Dict[str, Any]:
        ...

    def count_tokens(self, text: str) -> int:
        ...

    def close(self) -> None:
        ...


def _combined_text(*parts: object) -> str:
    return "\n".join(str(part) for part in parts if part)


def _dedupe_preserve(items: List[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _first_sentence(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", stripped, maxsplit=1)
    return parts[0].strip()


def _customer_supplied_visual_proof(text: str) -> bool:
    lowered = text.lower()
    negative_phrases = [
        "have not sent photos",
        "haven't sent photos",
        "have not sent photo",
        "haven't sent photo",
        "did not send photos",
        "didn't send photos",
        "did not send photo",
        "didn't send photo",
        "not attached photos",
        "not attached photo",
        "no photos yet",
        "no photo yet",
        "without photos",
        "without photo",
    ]
    if any(phrase in lowered for phrase in negative_phrases):
        return False
    proof_phrases = [
        "attached photo",
        "attached photos",
        "attached picture",
        "attached pictures",
        "attached image",
        "attached images",
        "uploaded photo",
        "uploaded photos",
        "shared photo",
        "shared photos",
        "sent photo",
        "sent photos",
        "see photo",
        "see photos",
        "here are photos",
    ]
    return any(phrase in lowered for phrase in proof_phrases)


def _parse_enum_values(type_spec: str) -> List[str]:
    normalized = type_spec.strip()
    if normalized.startswith("enum[") and normalized.endswith("]"):
        inner = normalized[len("enum[") : -1]
        return [item.strip() for item in inner.split(",") if item.strip()]
    if normalized.startswith("list[enum[") and normalized.endswith("]]"):
        inner = normalized[len("list[enum[") : -2]
        return [item.strip() for item in inner.split(",") if item.strip()]
    return []


def _default_value_for_field(field: OutputFieldDefinition) -> Any:
    type_spec = field.type_spec.strip()
    enum_values = _parse_enum_values(type_spec)
    if enum_values:
        return enum_values[0] if not type_spec.startswith("list[") else []
    if type_spec == "string":
        return ""
    if type_spec == "integer":
        return 0
    if type_spec == "number":
        return 0.0
    if type_spec == "boolean":
        return False
    if type_spec.startswith("list["):
        return []
    return None


def _validate_typed_value(value: Any, type_spec: str) -> bool:
    normalized = type_spec.strip()
    if normalized == "string":
        return isinstance(value, str)
    if normalized == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized == "boolean":
        return isinstance(value, bool)
    if normalized.startswith("enum[") and normalized.endswith("]"):
        options = [item.strip() for item in normalized[len("enum[") : -1].split(",") if item.strip()]
        return isinstance(value, str) and value in options
    if normalized == "list[string]":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if normalized.startswith("list[enum[") and normalized.endswith("]]"):
        options = [
            item.strip()
            for item in normalized[len("list[enum[") : -2].split(",")
            if item.strip()
        ]
        return isinstance(value, list) and all(isinstance(item, str) and item in options for item in value)
    if normalized.startswith("list["):
        return isinstance(value, list)
    return True


def _truncate_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _single_string_output_field(output_schema: OutputSchemaDefinition) -> str | None:
    if len(output_schema.fields) != 1:
        return None
    field = next(iter(output_schema.fields.values()))
    if field.type_spec.strip() != "string":
        return None
    return field.name


def render_json_stage_prompt(
    *,
    stage: StageDefinition,
    instruction: str,
    task_view: Dict[str, Any],
    state_view: Dict[str, Any],
    artifact_view: Dict[str, Any],
    output_schema: OutputSchemaDefinition,
    prompt_header: str,
) -> str:
    single_string_field = _single_string_output_field(output_schema)
    if single_string_field is not None:
        return "\n\n".join(
            [
                prompt_header,
                "Return exactly one JSON object and nothing else.",
                f'Output format: {{"{single_string_field}": "<final text for this stage>"}}',
                "Write the field value as the final answer text for this stage, not as analysis or instructions.",
                "Do not describe the schema, the prompt, or the workflow.",
                f"Stage: {stage.name}",
                f"Instruction:\n{instruction or 'No extra instruction provided.'}",
                f"Task view:\n{json.dumps(task_view, ensure_ascii=False, indent=2)}",
                f"State view:\n{json.dumps(state_view, ensure_ascii=False, indent=2)}",
                f"Artifact view:\n{json.dumps(artifact_view, ensure_ascii=False, indent=2)}",
            ]
        )

    required_keys = list(output_schema.fields.keys())
    input_only_keys: List[str] = []
    for source in (task_view, state_view, artifact_view):
        for key in source.keys():
            if key in output_schema.fields or key in input_only_keys:
                continue
            input_only_keys.append(key)
    disallowed_hint = ", ".join(input_only_keys[:12])
    if len(input_only_keys) > 12:
        disallowed_hint += ", ..."
    return "\n\n".join(
        [
            prompt_header,
            "Return exactly one JSON object and nothing else.",
            "The JSON object must match the output schema at the top level.",
            f"Required top-level keys: {', '.join(required_keys) or '(none)'}",
            "Do not wrap it inside keys like state, output, result, data, or response.",
            "Do not echo the task view, state view, artifact view, or a previous stage output.",
            (
                f"Do not emit input-only keys such as: {disallowed_hint}."
                if disallowed_hint
                else "Do not emit any top-level keys that are not in the output schema."
            ),
            f"Stage: {stage.name}",
            f"Instruction:\n{instruction or 'No extra instruction provided.'}",
            f"Output schema:\n{render_output_schema(output_schema)}",
            f"Task view:\n{json.dumps(task_view, ensure_ascii=False, indent=2)}",
            f"State view:\n{json.dumps(state_view, ensure_ascii=False, indent=2)}",
            f"Artifact view:\n{json.dumps(artifact_view, ensure_ascii=False, indent=2)}",
        ]
    )


def render_output_schema(output_schema: OutputSchemaDefinition) -> str:
    lines = [f"- {field.name}: {field.type_spec}" for field in output_schema.fields.values()]
    return "\n".join(lines)


def approximate_count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))


def _env_flag(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {raw_value}")


def _env_optional_int(name: str) -> int | None:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return None
    normalized = raw_value.strip()
    if normalized == "" or normalized.lower() == "none":
        return None
    return int(normalized)


class DeterministicJsonBackend:
    token_count_mode = "approximate"

    def _support_ticket_intake(
        self,
        task_view: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
    ) -> Dict[str, Any]:
        customer_message = str(task_view.get("customer_message", ""))
        context_pack = str(task_view.get("context_pack", ""))
        message_text = customer_message.lower()
        context_text = context_pack.lower()
        text = _combined_text(customer_message, context_pack).lower()

        if any(token in message_text for token in ["broken", "damaged", "replacement"]):
            issue_type = "damaged_item"
        elif any(token in message_text for token in ["login", "password", "account", "sso"]):
            issue_type = "account_access"
        elif any(token in message_text for token in ["late", "delay", "shipping", "delivery"]):
            issue_type = "delivery_issue"
        elif any(token in message_text for token in ["refund", "chargeback", "double charge", "charged twice"]):
            issue_type = "billing"
        elif any(token in context_text for token in ["refund", "chargeback", "double charge", "charged twice"]):
            issue_type = "billing"
        elif any(token in context_text for token in ["login", "password", "account", "sso"]):
            issue_type = "account_access"
        elif any(token in context_text for token in ["broken", "damaged", "replacement"]):
            issue_type = "damaged_item"
        elif any(token in context_text for token in ["late", "delay", "shipping", "delivery"]):
            issue_type = "delivery_issue"
        else:
            issue_type = "general_support"

        urgency = "high" if any(token in text for token in ["urgent", "outage", "blocked", "trip"]) else "normal"
        missing_information: List[str] = []
        if "order" not in text and issue_type in {"billing", "delivery_issue", "damaged_item"}:
            missing_information.append("order_id")
        if issue_type == "damaged_item" and not _customer_supplied_visual_proof(customer_message):
            missing_information.append("damage_proof")

        return {
            "issue_type": issue_type,
            "customer_goal": _first_sentence(customer_message) or "Resolve the support request.",
            "urgency": urgency,
            "missing_information": missing_information,
        }

    def _support_policy_evidence(
        self,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        text = _combined_text(task_view.get("context_pack"), task_view.get("customer_message")).lower()
        issue_type = str(state_view.get("issue_type", ""))
        missing_information = list(state_view.get("missing_information", []))

        policy_signals: List[str] = []
        if "proof required" in text or "documentation required" in text or "damage is confirmed" in text:
            policy_signals.append("customer_proof_required")
        if "manager review" in text or "specialist review" in text:
            policy_signals.append("specialist_review_required")
        if "frontline can approve" in text or "frontline may issue" in text or "eligible for replacement" in text:
            policy_signals.append("standard_remedy_available")
        if "non-refundable" in text or "not refundable" in text:
            policy_signals.append("approval_not_guaranteed")
        if "shipping fee credit" in text or "small credit" in text:
            policy_signals.append("limited_concession_available")
        if "identity verification" in text or "verify identity" in text:
            policy_signals.append("identity_verification_required")

        evidence_factors: List[str] = []
        if missing_information:
            evidence_factors.append("missing_customer_evidence")
        if "immediately" in text or "frontline" in text and "cannot" not in text:
            evidence_factors.append("frontline_action_available")
        if "specialist" in text:
            evidence_factors.append("specialist_queue_required")
        if "urgent" in text or "outage" in text or "trip" in text:
            evidence_factors.append("high_time_sensitivity")

        if issue_type == "billing":
            candidate_actions = ["refund_review", "clarify_charge_state"]
        elif issue_type == "account_access":
            candidate_actions = ["identity_verification", "specialist_escalation"]
        elif issue_type == "damaged_item":
            candidate_actions = ["replacement_if_stocked", "collect_damage_proof"]
        elif issue_type == "delivery_issue":
            candidate_actions = ["shipping_credit", "set_delivery_expectation"]
        else:
            candidate_actions = ["clarify_request", "general_escalation"]

        if issue_type == "billing":
            preferred_resolution_path = "refund"
        elif issue_type == "account_access":
            preferred_resolution_path = "account_recovery"
        elif issue_type == "damaged_item":
            preferred_resolution_path = "replacement" if "in stock" in text or "replacement" in text else "refund"
        elif issue_type == "delivery_issue":
            preferred_resolution_path = "goodwill_credit"
        else:
            preferred_resolution_path = "escalation"

        blocking_requirements: List[str] = []
        if issue_type == "damaged_item" and "missing_customer_evidence" in evidence_factors:
            blocking_requirements.append("customer_evidence")
        if "identity_verification_required" in policy_signals:
            blocking_requirements.append("customer_verification")
        if "specialist_review_required" in policy_signals or "specialist_queue_required" in evidence_factors:
            blocking_requirements.append("internal_review")

        risk_flags: List[str] = []
        if "medical exception" in text:
            risk_flags.append("exception_policy")
        if "enterprise" in text and "sso" in text:
            risk_flags.append("tenant_wide_impact")

        return {
            "policy_signals": _dedupe_preserve(policy_signals),
            "evidence_factors": _dedupe_preserve(evidence_factors),
            "preferred_resolution_path": preferred_resolution_path,
            "blocking_requirements": _dedupe_preserve(blocking_requirements),
            "candidate_actions": _dedupe_preserve(candidate_actions),
            "risk_flags": _dedupe_preserve(risk_flags),
        }

    def _support_eligibility(
        self,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        signals = set(state_view.get("policy_signals", []))
        factors = set(state_view.get("evidence_factors", []))
        blockers = set(state_view.get("blocking_requirements", []))
        preferred_path = str(state_view.get("preferred_resolution_path", ""))
        text = _combined_text(task_view.get("customer_message"), task_view.get("context_pack")).lower()

        if (
            "internal_review" in blockers
            or "specialist_review_required" in signals
            or "specialist_queue_required" in factors
            or "specialist review" in text
            or "manager review" in text
        ):
            decision = "escalate"
        elif blockers or "missing_customer_evidence" in factors:
            decision = "needs_info"
        elif preferred_path == "goodwill_credit" and "approval_not_guaranteed" in signals and "limited_concession_available" in signals:
            decision = "partial"
        elif "approval_not_guaranteed" in signals and "standard_remedy_available" not in signals:
            decision = "denied"
        else:
            decision = "approved"

        rationale_parts = [
            (
                "Decision based on "
                f"signals={sorted(signals)}, factors={sorted(factors)}, "
                f"blockers={sorted(blockers)}, preferred_path={preferred_path or 'unknown'}."
            )
        ]
        if text:
            rationale_parts.append("Original evidence was also available to this stage.")
        return {
            "eligibility_decision": decision,
            "eligibility_rationale": " ".join(rationale_parts),
        }

    def _support_resolution(
        self,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        decision = state_view.get("eligibility_decision")
        issue_type = state_view.get("issue_type")
        preferred_path = str(state_view.get("preferred_resolution_path", ""))
        blockers = list(state_view.get("blocking_requirements", []))
        actions = set(state_view.get("candidate_actions", []))
        text = _combined_text(task_view.get("customer_message"), task_view.get("context_pack")).lower()

        if decision == "needs_info":
            if preferred_path in {"replacement", "refund", "goodwill_credit", "account_recovery"}:
                category = preferred_path
                missing_information = list(state_view.get("missing_information", []))
                if missing_information:
                    blocker_summary = ", ".join(missing_information)
                elif blockers:
                    blocker_summary = ", ".join(blockers)
                else:
                    blocker_summary = "the remaining requirement"
                steps = [
                    f"Collect {blocker_summary}.",
                    f"Continue with the {preferred_path} path once the blocker is cleared.",
                ]
            else:
                category = "clarification_request"
                steps = ["Collect the missing evidence.", "Hold off on irreversible promises."]
        elif decision == "escalate":
            category = "escalation"
            steps = ["Route the case to the specialist queue.", "Explain the next checkpoint."]
        elif decision == "partial":
            category = "goodwill_credit"
            steps = ["Offer the limited concession allowed by policy.", "Explain why the full request is not supported."]
        elif decision == "denied":
            category = "policy_denial"
            steps = ["Decline the requested remedy.", "Point to the relevant policy constraint."]
        elif issue_type == "account_access":
            category = "account_recovery"
            steps = ["Verify identity.", "Complete the recovery workflow."]
        elif "replacement_if_stocked" in actions:
            category = "replacement"
            steps = ["Check current stock.", "Issue the replacement if available."]
        else:
            category = "refund"
            steps = ["Approve the standard remedy.", "Close the request after confirmation."]

        if category == "refund" and "non-refundable" in text:
            category = "policy_denial"
            steps = [
                "Explain the policy constraint from the source material.",
                "Offer the nearest policy-safe alternative if available.",
            ]

        return {
            "resolution_category": category,
            "resolution_steps": steps,
        }

    def _support_response(
        self,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        handoff_status = state_view.get("handoff_status", "ready")
        guardrails = list(state_view.get("response_guardrails", []))
        goal = state_view.get("customer_goal", "your request")
        decision = state_view.get("eligibility_decision", "pending")
        category = state_view.get("resolution_category", "clarification_request")
        steps = state_view.get("resolution_steps", [])
        refinement_focus = [
            str(item).strip()
            for item in state_view.get("refinement_focus", []) or []
            if str(item).strip()
        ]
        refined_notes = [
            str(item).strip()
            for item in state_view.get("refined_response_notes", []) or []
            if str(item).strip()
        ]
        response = (
            f"We reviewed {goal}. Current status: {decision}. "
            f"Next path: {category}. "
            f"Planned steps: {' '.join(steps)}"
        )
        if handoff_status != "ready":
            response += f" Handoff status: {handoff_status}."
        if guardrails:
            response += f" Guardrails: {' '.join(guardrails)}"
        text = _combined_text(task_view.get("customer_message"), task_view.get("context_pack")).lower()
        if "urgent" in text or "outage" in text or "trip" in text:
            response += " We also considered the urgency signaled in the original evidence."
        if refinement_focus:
            response += f" Refinement focus: {', '.join(refinement_focus)}."
        if refined_notes:
            response += f" Response notes: {' '.join(refined_notes)}"
        return {
            "response_message": response,
        }

    def _support_resolution_verification(
        self,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        decision = str(state_view.get("eligibility_decision", ""))
        category = str(state_view.get("resolution_category", ""))
        blockers = list(state_view.get("blocking_requirements", []))
        missing_information = list(state_view.get("missing_information", []))
        risk_flags = list(state_view.get("risk_flags", []))
        text = _combined_text(task_view.get("customer_message"), task_view.get("context_pack")).lower()

        response_guardrails: List[str] = []
        if decision == "needs_info":
            handoff_status = "blocked"
            if missing_information:
                response_guardrails.append(
                    f"State the missing requirement before confirming {category}: {', '.join(missing_information)}."
                )
            elif blockers:
                response_guardrails.append(
                    f"Do not confirm {category} until blockers are cleared: {', '.join(blockers)}."
                )
            else:
                response_guardrails.append("Do not promise irreversible action until the blocker is cleared.")
        elif decision == "escalate":
            handoff_status = "escalate"
            response_guardrails.append("Explain that specialist handling is required before final resolution.")
        else:
            handoff_status = "ready"

        if "approval_not_guaranteed" in state_view.get("policy_signals", []):
            response_guardrails.append("Avoid absolute promises until the policy exception is confirmed.")
        if "trip" in text or "urgent" in text:
            response_guardrails.append("Acknowledge urgency without committing to unsupported expedite steps.")
        for risk_flag in risk_flags:
            response_guardrails.append(f"Surface the operational risk flag: {risk_flag}.")

        return {
            "handoff_status": handoff_status,
            "response_guardrails": _dedupe_preserve(response_guardrails),
        }

    def _support_response_refinement(
        self,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        urgency = str(state_view.get("urgency", "")).strip()
        decision = str(state_view.get("eligibility_decision", "")).strip()
        category = str(state_view.get("resolution_category", "")).strip()
        handoff_status = str(state_view.get("handoff_status", "")).strip()
        missing_information = [
            str(item).strip()
            for item in state_view.get("missing_information", []) or []
            if str(item).strip()
        ]
        blockers = [
            str(item).strip()
            for item in state_view.get("blocking_requirements", []) or []
            if str(item).strip()
        ]
        response_guardrails = [
            str(item).strip()
            for item in state_view.get("response_guardrails", []) or []
            if str(item).strip()
        ]
        risk_flags = [
            str(item).strip()
            for item in state_view.get("risk_flags", []) or []
            if str(item).strip()
        ]
        text = _combined_text(task_view.get("customer_message"), task_view.get("context_pack")).lower()

        refinement_focus: List[str] = []
        refined_response_notes: List[str] = []

        if decision == "needs_info" or handoff_status == "blocked" or missing_information or blockers:
            refinement_focus.append("blocker_clarity")
            blocker_text = ", ".join(missing_information or blockers or ["the required blocker"])
            refined_response_notes.append(
                f"Lead with the remaining blocker before describing the {category or 'planned'} path: {blocker_text}."
            )
        if response_guardrails:
            refinement_focus.append("policy_guardrails")
            refined_response_notes.append("Keep the response aligned with the existing policy guardrails.")
        if urgency == "high" or "trip" in text or "urgent" in text or "outage" in text:
            refinement_focus.append("urgency_tone")
            refined_response_notes.append(
                "Acknowledge urgency while avoiding unsupported speed or outcome commitments."
            )
        if decision == "escalate" or handoff_status == "escalate":
            refinement_focus.append("escalation_expectation")
            refined_response_notes.append(
                "Explain the specialist handoff checkpoint and avoid implying immediate completion."
            )
        if risk_flags:
            refinement_focus.append("risk_disclosure")
            refined_response_notes.append(
                f"Surface the operational risk factors explicitly: {', '.join(risk_flags)}."
            )

        return {
            "refinement_focus": _dedupe_preserve(refinement_focus),
            "refined_response_notes": _dedupe_preserve(refined_response_notes),
        }

    def _support_case_assessment(
        self,
        task_view: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
    ) -> Dict[str, Any]:
        intake_state = self._support_ticket_intake(task_view, output_schema)
        policy_state = self._support_policy_evidence(task_view, intake_state)
        eligibility_state = self._support_eligibility(task_view, {**intake_state, **policy_state})
        return {
            **intake_state,
            **policy_state,
            **eligibility_state,
        }

    def _bug_signal_extractor(
        self,
        task_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        text = _combined_text(
            task_view.get("title"),
            task_view.get("description"),
            task_view.get("log_excerpt"),
            task_view.get("stacktrace_excerpt"),
        ).lower()
        error_messages = _dedupe_preserve(re.findall(r"[A-Za-z_]+(?:Error|Exception)", text))
        file_hints = _dedupe_preserve(re.findall(r"[A-Za-z0-9_/]+\.(?:py|ts|js|json)", text))
        symbol_hints = _dedupe_preserve(
            re.findall(r"(?:in|function|method)\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        )

        suspected_modules: List[str] = []
        for token in ["json", "state", "summary", "report", "prompt", "token", "artifact", "config"]:
            if token in text:
                suspected_modules.append(token)

        return {
            "bug_summary": str(task_view.get("title", "")).strip(),
            "error_messages": error_messages,
            "file_hints": file_hints,
            "symbol_hints": symbol_hints,
            "suspected_modules": _dedupe_preserve(suspected_modules),
        }

    def _bug_query_expansion(
        self,
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        exact_queries = _dedupe_preserve(
            list(state_view.get("error_messages", [])) + list(state_view.get("file_hints", []))
        )
        symbol_queries = _dedupe_preserve(list(state_view.get("symbol_hints", [])))
        semantic_queries = _dedupe_preserve(
            [state_view.get("bug_summary", "")]
            + [f"{module} handling" for module in state_view.get("suspected_modules", [])]
        )
        graph_queries = _dedupe_preserve(
            [hint.rsplit("/", 1)[0] for hint in state_view.get("file_hints", []) if "/" in hint]
        )

        return {
            "exact_queries": [query for query in exact_queries if query],
            "symbol_queries": [query for query in symbol_queries if query],
            "semantic_queries": [query for query in semantic_queries if query],
            "graph_queries": [query for query in graph_queries if query],
        }

    def _bug_evidence_screen(
        self,
        state_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        retrieved = list(artifact_view.get("retrieved_artifacts", []))
        candidate_files = [artifact.get("path", "") for artifact in retrieved[:3]]
        text = _combined_text(state_view.get("bug_summary"), state_view.get("retrieval_summary")).lower()
        labels: List[str] = []
        if "config" in text or any("config" in file for file in candidate_files[:1]):
            labels.append("config_path")
        if any("state" in file for file in candidate_files):
            labels.append("state_update_path")
        if any("summary" in file or "report" in file for file in candidate_files):
            labels.append("reporting_path")
        if any("utils" in file for file in candidate_files):
            labels.append("shared_helper_path")
        if any("engine" in file for file in candidate_files):
            labels.append("runtime_callsite_path")

        missing_evidence: List[str] = []
        if not candidate_files:
            missing_evidence.append("No candidate files matched the available evidence.")

        return {
            "candidate_files": _dedupe_preserve([item for item in candidate_files if item]),
            "applied_evidence_labels": _dedupe_preserve(labels) or ["generic_code_path"],
            "missing_evidence": missing_evidence,
        }

    def _bug_followup_query_expansion(
        self,
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        file_hints = [str(item).strip() for item in state_view.get("file_hints", []) if str(item).strip()]
        candidate_files = set(str(item).strip() for item in state_view.get("candidate_files", []) if str(item).strip())
        applied_labels = set(str(item).strip() for item in state_view.get("applied_evidence_labels", []))
        missing_hints = [item for item in file_hints if item not in candidate_files]

        exact_queries = missing_hints[:2]
        semantic_queries: List[str] = []
        if "shared_helper_path" not in applied_labels:
            semantic_queries.append("shared helper recovery path")
        if not exact_queries and not candidate_files and state_view.get("bug_summary"):
            semantic_queries.append(str(state_view.get("bug_summary")))

        retrieval_decision = "re_retrieve" if exact_queries or semantic_queries else "proceed"
        rationale_bits: List[str] = []
        if exact_queries:
            rationale_bits.append(f"Missing explicit file hints: {exact_queries}.")
        if "shared_helper_path" not in applied_labels:
            rationale_bits.append("Initial evidence did not confirm the shared helper path.")
        if not rationale_bits:
            rationale_bits.append("Current evidence is already sufficient.")

        return {
            "retrieval_decision": retrieval_decision,
            "followup_exact_queries": _dedupe_preserve(exact_queries),
            "followup_semantic_queries": _dedupe_preserve(semantic_queries),
            "followup_rationale": " ".join(rationale_bits),
        }

    def _bug_followup_evidence_compression(
        self,
        state_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        retrieved = list(artifact_view.get("followup_retrieved_artifacts", []))
        refined_candidates = [artifact.get("path", "") for artifact in retrieved[:3] if artifact.get("path")]
        labels: List[str] = []
        if any("utils" in path or "helper" in path for path in refined_candidates):
            labels.append("shared_helper_path")
        if any("handler" in path or "engine" in path for path in refined_candidates):
            labels.append("runtime_callsite_path")
        if any("json" in path or "parser" in path for path in refined_candidates):
            labels.append("parser_path")
        notes: List[str] = []
        if refined_candidates:
            notes.append(
                f"Follow-up retrieval promoted {refined_candidates[0]} as the strongest refreshed candidate."
            )
        else:
            notes.append("Follow-up retrieval did not add stronger candidates.")
        return {
            "refined_candidates": _dedupe_preserve(refined_candidates),
            "refined_evidence_labels": _dedupe_preserve(labels) or ["generic_code_path"],
            "compression_notes": notes,
        }

    def _bug_followup_decision(
        self,
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        refined_candidates = [
            str(item).strip()
            for item in (state_view.get("refined_candidates") or [])
            if str(item).strip()
        ]
        base_candidates = [
            str(item).strip()
            for item in (state_view.get("candidate_files") or [])
            if str(item).strip()
        ]
        if refined_candidates:
            candidate_pool = refined_candidates
            labels = set(
                str(item).strip()
                for item in (state_view.get("refined_evidence_labels") or [])
            )
            decision_source = "refreshed_evidence"
        else:
            candidate_pool = base_candidates
            labels = set(
                str(item).strip()
                for item in (state_view.get("applied_evidence_labels") or [])
            )
            decision_source = "base_evidence"
        primary_file = candidate_pool[0] if candidate_pool else ""
        helper_candidate = next(
            (path for path in candidate_pool if "utils" in path or "helper" in path),
            "",
        )
        if helper_candidate:
            primary_file = helper_candidate
        decision_status = "supported" if helper_candidate or "shared_helper_path" in labels else "weak_evidence"
        rationale = (
            f"Follow-up decision selected {primary_file or 'no file'} from refreshed candidates="
            f"{candidate_pool} with labels={sorted(labels)} via {decision_source}."
        )
        return {
            "primary_file": primary_file,
            "decision_status": decision_status,
            "decision_source": decision_source,
            "decision_rationale": rationale,
        }

    def _bug_handoff_plan(
        self,
        state_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        primary_file = str(state_view.get("primary_file", "")).strip()
        decision_status = str(state_view.get("decision_status", "")).strip() or "weak_evidence"
        decision_source = str(state_view.get("decision_source", "")).strip() or "base_evidence"
        next_actions: List[str] = []
        if primary_file:
            next_actions.append(f"Inspect and patch {primary_file}.")
            next_actions.append(f"Add a regression test around the refreshed {decision_status} path.")
        else:
            next_actions.append("Capture another retrieval pass before proposing a patch.")
        summary = (
            f"Suffix re-retrieval concluded with status={decision_status}, "
            f"source={decision_source}, and primary_file={primary_file or 'unresolved'}."
        )
        return {
            "handoff_summary": summary,
            "next_actions": next_actions,
        }

    def generate_json(
        self,
        *,
        stage: StageDefinition,
        instruction: str,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
    ) -> Dict[str, Any]:
        if stage.name == "ticket_intake":
            result = self._support_ticket_intake(task_view, output_schema)
        elif stage.name == "policy_evidence":
            result = self._support_policy_evidence(task_view, state_view)
        elif stage.name == "eligibility_assessment":
            result = self._support_eligibility(task_view, state_view)
        elif stage.name == "resolution_plan":
            result = self._support_resolution(task_view, state_view)
        elif stage.name == "resolution_verification":
            result = self._support_resolution_verification(task_view, state_view)
        elif stage.name == "response_refinement":
            result = self._support_response_refinement(task_view, state_view)
        elif stage.name == "response_draft":
            result = self._support_response(task_view, state_view)
        elif stage.name == "case_assessment":
            result = self._support_case_assessment(task_view, output_schema)
        elif stage.name == "signal_extractor":
            result = self._bug_signal_extractor(task_view)
        elif stage.name == "query_expansion":
            result = self._bug_query_expansion(state_view)
        elif stage.name == "evidence_screen":
            result = self._bug_evidence_screen(state_view, artifact_view)
        elif stage.name == "followup_query_expansion":
            result = self._bug_followup_query_expansion(state_view)
        elif stage.name == "followup_evidence_compression":
            result = self._bug_followup_evidence_compression(state_view, artifact_view)
        elif stage.name == "followup_decision":
            result = self._bug_followup_decision(state_view)
        elif stage.name == "handoff_plan":
            result = self._bug_handoff_plan(state_view)
        else:
            result = {}

        for field_name, field in output_schema.fields.items():
            if field_name not in result:
                result[field_name] = _default_value_for_field(field)
        return result

    def count_tokens(self, text: str) -> int:
        return approximate_count_tokens(text)

    def close(self) -> None:
        return None


@dataclass
class OutputCandidateAssessment:
    candidate: Dict[str, Any]
    projected: Dict[str, Any]
    missing_fields: List[str]
    invalid_fields: List[str]
    extra_fields: List[str]

    @property
    def valid(self) -> bool:
        return not self.missing_fields and not self.invalid_fields


@dataclass
class LocalVLLMJsonBackend:
    model: str
    max_model_len: int = 8192
    tensor_parallel_size: int = 1
    gpu_memory_utilization: float = 0.6
    max_num_seqs: int = 16
    max_num_batched_tokens: int | None = None
    enforce_eager: bool = True
    enable_prefix_caching: bool = True
    temperature: float = 0.0
    top_p: float = 1.0
    max_output_tokens: int = 768
    _llm: Any = None
    _startup_attempts: List[Dict[str, Any]] = field(default_factory=list)
    _active_engine_profile: str | None = None
    _active_engine_kwargs: Dict[str, Any] | None = None
    token_count_mode = "model_tokenizer"

    def _base_engine_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_model_len": self.max_model_len,
            "tensor_parallel_size": self.tensor_parallel_size,
            "gpu_memory_utilization": self.gpu_memory_utilization,
            "enable_prefix_caching": self.enable_prefix_caching,
            "enforce_eager": self.enforce_eager,
        }
        if self.max_num_seqs is not None:
            kwargs["max_num_seqs"] = self.max_num_seqs
        if self.max_num_batched_tokens is not None:
            kwargs["max_num_batched_tokens"] = self.max_num_batched_tokens
        return kwargs

    def _build_startup_profiles(self) -> List[tuple[str, Dict[str, Any]]]:
        base = self._base_engine_kwargs()
        profiles: List[tuple[str, Dict[str, Any]]] = [("requested", base)]

        def bounded(current: int | None, fallback: int) -> int:
            if current is None:
                return fallback
            return min(current, fallback)

        def bounded_batched_tokens(fallback: int) -> int:
            current = base.get("max_num_batched_tokens")
            if current is None:
                return fallback
            return min(int(current), fallback)

        profiles.extend(
            [
                (
                    "memory_safe",
                    {
                        **base,
                        "max_num_seqs": bounded(base.get("max_num_seqs"), 8),
                        "enforce_eager": True,
                    },
                ),
                (
                    "memory_safe_low",
                    {
                        **base,
                        "max_num_seqs": bounded(base.get("max_num_seqs"), 4),
                        "gpu_memory_utilization": min(
                            float(base.get("gpu_memory_utilization", self.gpu_memory_utilization)),
                            0.5,
                        ),
                        "max_num_batched_tokens": bounded_batched_tokens(4096),
                        "enforce_eager": True,
                    },
                ),
                (
                    "memory_safe_tight",
                    {
                        **base,
                        "max_num_seqs": bounded(base.get("max_num_seqs"), 2),
                        "gpu_memory_utilization": min(
                            float(base.get("gpu_memory_utilization", self.gpu_memory_utilization)),
                            0.42,
                        ),
                        "max_num_batched_tokens": bounded_batched_tokens(2048),
                        "enforce_eager": True,
                    },
                ),
                (
                    "memory_safe_minimal",
                    {
                        **base,
                        "max_num_seqs": bounded(base.get("max_num_seqs"), 1),
                        "gpu_memory_utilization": min(
                            float(base.get("gpu_memory_utilization", self.gpu_memory_utilization)),
                            0.38,
                        ),
                        "max_num_batched_tokens": bounded_batched_tokens(1024),
                        "enforce_eager": True,
                    },
                ),
            ]
        )

        deduped: List[tuple[str, Dict[str, Any]]] = []
        seen = set()
        for name, profile in profiles:
            signature = json.dumps(profile, sort_keys=True)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append((name, profile))
        return deduped

    def _visible_cuda_device_index(self) -> int:
        raw_value = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
        if not raw_value:
            return 0
        first_token = raw_value.split(",")[0].strip()
        if not first_token:
            return 0
        try:
            return int(first_token)
        except ValueError:
            return 0

    def _probe_free_gpu_memory_gib(self) -> tuple[float, float] | None:
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,memory.free,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            return None

        visible_index = self._visible_cuda_device_index()
        for line in completed.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 3:
                continue
            try:
                gpu_index = int(parts[0])
                free_mib = float(parts[1])
                total_mib = float(parts[2])
            except ValueError:
                continue
            if gpu_index != visible_index:
                continue
            return free_mib / 1024.0, total_mib / 1024.0
        return None

    def _preflight_profile_memory_error(self, engine_kwargs: Dict[str, Any]) -> str | None:
        probe = self._probe_free_gpu_memory_gib()
        if probe is None:
            return None
        free_gib, total_gib = probe
        requested_util = float(engine_kwargs.get("gpu_memory_utilization", self.gpu_memory_utilization))
        requested_gib = total_gib * requested_util
        if free_gib + 0.05 >= requested_gib:
            return None
        return (
            "Free memory on device "
            f"({free_gib:.2f}/{total_gib:.2f} GiB) on startup is less than desired GPU memory utilization "
            f"({requested_util:.2f}, {requested_gib:.2f} GiB). "
            "Decrease GPU memory utilization or reduce GPU memory used by other processes."
        )

    def _is_retryable_startup_error(self, message: str) -> bool:
        normalized = message.lower()
        return any(
            token in normalized
            for token in [
                "engine core initialization failed",
                "cuda out of memory",
                "out of memory",
                "warming up sampler",
                "free memory on device",
            ]
        )

    def _match_memory_error(self, message: str) -> re.Match[str] | None:
        return re.search(
            r"Free memory on device \(([\d.]+)/([\d.]+) GiB\).*gpu memory utilization "
            r"\(([\d.]+), ([\d.]+) GiB\)",
            message,
            flags=re.IGNORECASE,
        )

    def _startup_attempts_summary(self) -> str:
        if not self._startup_attempts:
            return "No startup attempts were recorded."
        lines = ["Attempted startup profiles:"]
        for attempt in self._startup_attempts:
            config = attempt.get("config", {})
            lines.append(
                "- "
                f"{attempt.get('profile')}: status={attempt.get('status')}, "
                f"gpu_memory_utilization={config.get('gpu_memory_utilization')}, "
                f"max_num_seqs={config.get('max_num_seqs')}, "
                f"enforce_eager={config.get('enforce_eager')}, "
                f"max_model_len={config.get('max_model_len')}"
            )
        return "\n".join(lines)

    def _tightest_profile_memory_target(self) -> tuple[str, float]:
        profiles = self._build_startup_profiles()
        profile_name, profile = min(
            profiles,
            key=lambda item: float(item[1].get("gpu_memory_utilization", self.gpu_memory_utilization)),
        )
        return profile_name, float(profile.get("gpu_memory_utilization", self.gpu_memory_utilization))

    def describe_runtime(self) -> Dict[str, Any]:
        return {
            "backend": "local-vllm",
            "model": self.model,
            "active_profile": self._active_engine_profile,
            "active_config": dict(self._active_engine_kwargs or {}),
            "startup_attempts": [dict(attempt) for attempt in self._startup_attempts],
        }

    def _best_effort_call(self, obj: Any, method_names: List[str]) -> None:
        for method_name in method_names:
            method = getattr(obj, method_name, None)
            if not callable(method):
                continue
            try:
                method()
            except TypeError:
                continue
            except Exception:
                continue
            return

    def close(self) -> None:
        llm = self._llm
        self._llm = None
        self._active_engine_profile = None
        self._active_engine_kwargs = None
        if llm is not None:
            llm_engine = getattr(llm, "llm_engine", None)
            engine_core = getattr(llm_engine, "engine_core", None) if llm_engine is not None else None
            for obj, methods in [
                (engine_core, ["shutdown", "close"]),
                (llm_engine, ["shutdown", "close"]),
                (llm, ["shutdown", "close"]),
            ]:
                if obj is None:
                    continue
                self._best_effort_call(obj, methods)
            del llm
        gc.collect()
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
        except Exception:
            pass

    def _format_startup_error(self, exc: Exception) -> RuntimeError:
        message = str(exc)
        attempt_summary = self._startup_attempts_summary()
        memory_match = self._match_memory_error(message)
        if memory_match is None:
            for attempt in reversed(self._startup_attempts):
                attempt_error = str(attempt.get("error", "") or "")
                candidate_match = self._match_memory_error(attempt_error)
                if candidate_match is None:
                    continue
                memory_match = candidate_match
                message = attempt_error
                break
        sampler_match = re.search(r"warming up sampler with (\d+) dummy requests", message, flags=re.IGNORECASE)
        if sampler_match:
            return RuntimeError(
                "Local vLLM backend failed during sampler warmup because the offline engine "
                "was still trying to reserve too much concurrency for this workflow.\n"
                f"Model: {self.model}\n"
                f"Requested max_model_len: {self.max_model_len}\n\n"
                f"{attempt_summary}\n\n"
                "What this means:\n"
                "- This runtime executes one small stage at a time, so high serving-style concurrency is unnecessary.\n"
                "- The failing profile tried to warm up the sampler with too many dummy requests.\n\n"
                "Recommended next steps:\n"
                "1. Keep `enforce_eager` enabled for this offline workflow.\n"
                "2. Lower `max_num_seqs` further if the GPU is still tight.\n"
                "3. Lower `gpu_memory_utilization` if the model shares the device with other jobs.\n\n"
                f"Original vLLM error:\n{message}"
            )
        if not memory_match:
            return RuntimeError(
                "Local vLLM backend failed to start.\n"
                f"Model: {self.model}\n"
                f"max_model_len: {self.max_model_len}\n"
                f"gpu_memory_utilization: {self.gpu_memory_utilization}\n"
                f"max_num_seqs: {self.max_num_seqs}\n"
                f"enforce_eager: {self.enforce_eager}\n\n"
                f"{attempt_summary}\n\n"
                f"Original error:\n{message}"
            )

        free_gib = float(memory_match.group(1))
        total_gib = float(memory_match.group(2))
        requested_util = float(memory_match.group(3))
        requested_gib = float(memory_match.group(4))
        available_util = free_gib / total_gib if total_gib else 0.0
        tightest_profile_name, tightest_profile_util = self._tightest_profile_memory_target()
        tightest_profile_gib = total_gib * tightest_profile_util

        fallback_message = ""
        if available_util < tightest_profile_util:
            fallback_message = (
                "Current free VRAM is below the runtime's tightest configured startup profile, so this run "
                "cannot succeed on the present device state.\n"
                f"- Tightest profile: {tightest_profile_name} "
                f"(gpu_memory_utilization={tightest_profile_util:.2f}, about {tightest_profile_gib:.2f} GiB)\n"
                f"- Current free-memory ceiling: about {available_util:.2f} utilization "
                f"({free_gib:.2f} GiB)\n\n"
            )

        return RuntimeError(
            "Local vLLM backend failed to start because the GPU does not have enough free VRAM.\n"
            f"Model: {self.model}\n"
            f"Free VRAM at startup: {free_gib:.2f} / {total_gib:.2f} GiB\n"
            f"Requested gpu_memory_utilization: {requested_util:.2f} "
            f"(about {requested_gib:.2f} GiB)\n"
            f"Current free-memory ceiling: about {available_util:.2f} utilization\n\n"
            f"{attempt_summary}\n\n"
            f"{fallback_message}"
            "What this means:\n"
            "- This is an environment resource issue, not a workflow-schema issue.\n"
            "- Lowering gpu_memory_utilization only helps if enough VRAM is actually free.\n"
            "- With this little free VRAM, a 4B local model is unlikely to start until other GPU jobs are released.\n\n"
            "Recommended next steps:\n"
            "1. Run `nvidia-smi` and stop the processes occupying the GPU.\n"
            "2. Retry after enough VRAM is free.\n"
            "3. Lower `max_num_seqs` because this offline workflow does not need high serving-style concurrency.\n"
            "4. If the machine is shared, switch to a smaller or quantized local model.\n"
            "5. After VRAM pressure is resolved, you can additionally reduce `--max-model-len` to shrink KV-cache cost.\n\n"
            f"Original vLLM error:\n{message}"
        )

    def _ensure_llm(self) -> Any:
        # Keep local model startup lazy so schema checks and deterministic smoke tests stay lightweight.
        if self._llm is not None:
            return self._llm
        if LLM is None or SamplingParams is None:
            raise RuntimeError(
                "vLLM is not installed. Install requirements and use a local model path "
                "before selecting local-vllm mode."
            )
        last_error: Exception | None = None
        self._startup_attempts = []
        self._active_engine_profile = None
        self._active_engine_kwargs = None
        for profile_name, engine_kwargs in self._build_startup_profiles():
            attempt = {
                "profile": profile_name,
                "config": dict(engine_kwargs),
                "status": "running",
            }
            self._startup_attempts.append(attempt)
            preflight_memory_error = self._preflight_profile_memory_error(engine_kwargs)
            if preflight_memory_error is not None:
                attempt["status"] = "skipped"
                attempt["error"] = preflight_memory_error
                last_error = RuntimeError(preflight_memory_error)
                continue
            try:
                self._llm = LLM(**engine_kwargs)
                attempt["status"] = "succeeded"
                self._active_engine_profile = profile_name
                self._active_engine_kwargs = dict(engine_kwargs)
                return self._llm
            except Exception as exc:  # pragma: no cover - depends on machine state / GPU availability
                attempt["status"] = "failed"
                attempt["error"] = str(exc)
                last_error = exc
                if not self._is_retryable_startup_error(str(exc)):
                    raise self._format_startup_error(exc) from exc
        if last_error is None:
            raise RuntimeError("Local vLLM backend failed to start for an unknown reason.")
        raise self._format_startup_error(last_error) from last_error
        return self._llm

    def _render_schema(self, output_schema: OutputSchemaDefinition) -> str:
        return render_output_schema(output_schema)

    def _decode_first_json_object(self, text: str) -> Dict[str, Any]:
        decoder = json.JSONDecoder()
        exact_error: Exception | None = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}.")
        except (json.JSONDecodeError, ValueError) as exc:
            exact_error = exc

        start = 0
        while True:
            start = text.find("{", start)
            if start < 0:
                break
            try:
                parsed, _ = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                start += 1
                continue
            if isinstance(parsed, dict):
                return parsed
            start += 1

        if exact_error is not None:
            raise exact_error
        raise json.JSONDecodeError("No JSON object found", text, 0)

    def _extract_json_objects(self, content: str) -> List[Dict[str, Any]]:
        stripped = content.strip()
        candidates: List[str] = [stripped]

        if stripped.startswith("```"):
            unfenced = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
            unfenced = re.sub(r"\s*```$", "", unfenced)
            candidates.append(unfenced.strip())

        fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
        candidates.extend(block.strip() for block in fenced_blocks if block.strip())

        parsed_objects: List[Dict[str, Any]] = []
        seen_serializations = set()
        last_error: Exception | None = None
        decoder = json.JSONDecoder()

        def add_candidate(parsed: Dict[str, Any]) -> None:
            serialized = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
            if serialized in seen_serializations:
                return
            seen_serializations.add(serialized)
            parsed_objects.append(parsed)

        for candidate_text in candidates:
            try:
                add_candidate(self._decode_first_json_object(candidate_text))
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
            start = 0
            while True:
                start = candidate_text.find("{", start)
                if start < 0:
                    break
                try:
                    parsed, _ = decoder.raw_decode(candidate_text, start)
                except json.JSONDecodeError:
                    start += 1
                    continue
                if isinstance(parsed, dict):
                    add_candidate(parsed)
                start += 1

        if parsed_objects:
            return parsed_objects
        if last_error is not None:
            raise last_error
        raise json.JSONDecodeError("No JSON object found", stripped, 0)

    def _extract_json_object(self, content: str) -> Dict[str, Any]:
        return self._extract_json_objects(content)[0]

    def _normalize_output_shape(
        self,
        parsed: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
    ) -> Dict[str, Any]:
        expected_fields = set(output_schema.fields.keys())
        if not expected_fields:
            return parsed

        preferred_wrapper_paths = {
            "state",
            "output",
            "result",
            "data",
            "response",
            output_schema.name,
            output_schema.name.split(".")[-1],
        }

        def walk_dicts(value: Dict[str, Any], path: str = "", depth: int = 0) -> List[tuple[str, Dict[str, Any]]]:
            if depth > 3:
                return []
            candidates = [(path or "<root>", value)]
            for key, nested in value.items():
                if isinstance(nested, dict):
                    nested_path = f"{path}.{key}" if path else key
                    candidates.extend(walk_dicts(nested, nested_path, depth + 1))
            return candidates

        def score(candidate_path: str, candidate_value: Dict[str, Any]) -> tuple[int, int, int]:
            overlap = len(expected_fields.intersection(candidate_value.keys()))
            preferred = 1 if candidate_path in preferred_wrapper_paths else 0
            extra_keys = len(set(candidate_value.keys()) - expected_fields)
            return (overlap, preferred, -extra_keys)

        candidates = walk_dicts(parsed)
        best_path, best_value = max(candidates, key=lambda item: score(item[0], item[1]))
        root_score = score("<root>", parsed)
        best_score = score(best_path, best_value)

        if best_path != "<root>" and best_score > root_score and best_score[0] > 0:
            return best_value
        return parsed

    def _select_best_output_candidate(
        self,
        candidates: List[Dict[str, Any]],
        output_schema: OutputSchemaDefinition,
    ) -> OutputCandidateAssessment:
        return max(
            (self._assess_output_candidate(candidate, output_schema) for candidate in candidates),
            key=lambda assessment: (
                1 if assessment.valid else 0,
                len(assessment.projected),
                -len(assessment.missing_fields),
                -len(assessment.invalid_fields),
                -len(assessment.extra_fields),
            ),
        )

    def _generate_once(self, prompt: str, max_tokens: int) -> str:
        llm = self._ensure_llm()
        sampling_params = SamplingParams(
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=max_tokens,
        )
        outputs = llm.generate(prompts=[prompt], sampling_params=sampling_params)
        return outputs[0].outputs[0].text

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        llm = self._ensure_llm()
        tokenizer = llm.get_tokenizer()
        return len(tokenizer.encode(text, add_special_tokens=False))

    def _assess_output_candidate(
        self,
        candidate: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
    ) -> OutputCandidateAssessment:
        projected: Dict[str, Any] = {}
        missing_fields: List[str] = []
        invalid_fields: List[str] = []
        expected_field_names = set(output_schema.fields.keys())

        for field_name, field_def in output_schema.fields.items():
            if field_name not in candidate:
                missing_fields.append(field_name)
                continue
            value = candidate[field_name]
            projected[field_name] = value
            if not _validate_typed_value(value, field_def.type_spec):
                invalid_fields.append(field_name)

        extra_fields = sorted(key for key in candidate.keys() if key not in expected_field_names)
        return OutputCandidateAssessment(
            candidate=candidate,
            projected=projected,
            missing_fields=missing_fields,
            invalid_fields=invalid_fields,
            extra_fields=extra_fields,
        )

    def _format_output_mismatch(
        self,
        assessment: OutputCandidateAssessment,
        output_schema: OutputSchemaDefinition,
    ) -> str:
        parts: List[str] = []
        if assessment.missing_fields:
            parts.append(f"missing required keys {assessment.missing_fields}")
        if assessment.invalid_fields:
            invalid_descriptions = [
                f"{field} (expected {output_schema.fields[field].type_spec})"
                for field in assessment.invalid_fields
            ]
            parts.append(f"invalid field types {invalid_descriptions}")
        if assessment.extra_fields:
            parts.append(f"unexpected keys {assessment.extra_fields}")
        if not parts:
            parts.append("unknown schema mismatch")
        return "; ".join(parts)

    def _looks_truncated(self, raw_text: str) -> bool:
        stripped = raw_text.strip()
        if not stripped:
            return True
        if stripped.count("{") > stripped.count("}"):
            return True
        if stripped.startswith("{") and not stripped.endswith("}"):
            return True
        return False

    def _looks_like_prompt_echo(self, text: str) -> bool:
        normalized = text.strip().lower()
        if not normalized:
            return True
        strong_markers = [
            "output schema",
            "task view",
            "state view",
            "artifact view",
            "return exactly one json object",
            "return one corrected json object only",
            "previous answer:",
            "stage:",
            "instruction:",
            "do not mention any specific",
            "the response should be a message that:",
            "(this is getting too long",
        ]
        if any(marker in normalized for marker in strong_markers):
            return True
        if normalized.count("do not ") >= 4:
            return True
        return False

    def _coerce_plain_text_output(
        self,
        raw_text: str,
        output_schema: OutputSchemaDefinition,
    ) -> Dict[str, Any] | None:
        single_string_field = _single_string_output_field(output_schema)
        if single_string_field is None:
            return None

        stripped = raw_text.strip()
        if not stripped:
            return None

        fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
        if fenced_blocks:
            stripped = fenced_blocks[0].strip()

        if not stripped or stripped.startswith("{") or self._looks_like_prompt_echo(stripped):
            return None
        return {single_string_field: stripped}

    def _build_repair_prompt(
        self,
        *,
        stage: StageDefinition,
        instruction: str,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
        previous_raw_text: str,
        feedback: str,
    ) -> str:
        single_string_field = _single_string_output_field(output_schema)
        if single_string_field is not None:
            return "\n\n".join(
                [
                    "Your previous answer was invalid for this workflow stage.",
                    "Return exactly one JSON object and nothing else.",
                    f'Output format: {{"{single_string_field}": "<final text for this stage>"}}',
                    "Write only the final text value. Do not explain the prompt, policy, schema, or workflow.",
                    f"Stage instruction:\n{instruction or 'No extra instruction provided.'}",
                    f"Task view:\n{json.dumps(task_view, ensure_ascii=False, indent=2)}",
                    f"State view:\n{json.dumps(state_view, ensure_ascii=False, indent=2)}",
                    f"Artifact view:\n{json.dumps(artifact_view, ensure_ascii=False, indent=2)}",
                    f"Schema feedback: {feedback}",
                ]
            )

        base_prompt = render_json_stage_prompt(
            stage=stage,
            instruction=instruction,
            task_view=task_view,
            state_view=state_view,
            artifact_view=artifact_view,
            output_schema=output_schema,
            prompt_header="You are correcting one workflow stage on a local model.",
        )
        return "\n\n".join(
            [
                base_prompt,
                "The previous answer did not satisfy the output contract.",
                f"Schema feedback: {feedback}",
                "Return one corrected JSON object only.",
                "Do not explain, do not quote the prompt, and do not repeat previous-stage fields unless they are required by the schema.",
                f"Previous answer:\n{_truncate_text(previous_raw_text)}",
            ]
        )

    def generate_json(
        self,
        *,
        stage: StageDefinition,
        instruction: str,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
    ) -> Dict[str, Any]:
        # We ask the local model for a single JSON object and let runtime schema validation stay in charge.
        prompt = render_json_stage_prompt(
            stage=stage,
            instruction=instruction,
            task_view=task_view,
            state_view=state_view,
            artifact_view=artifact_view,
            output_schema=output_schema,
            prompt_header="You are executing one workflow stage on a local model.",
        )

        raw_text = ""
        last_error: Exception | None = None
        max_tokens = self.max_output_tokens
        repair_prompt: str | None = None
        for attempt_index in range(3):
            current_prompt = repair_prompt or prompt
            raw_text = self._generate_once(current_prompt, max_tokens)
            try:
                parsed_objects = self._extract_json_objects(raw_text)
                normalized_candidates = [
                    self._normalize_output_shape(parsed, output_schema) for parsed in parsed_objects
                ]
                best_assessment = self._select_best_output_candidate(normalized_candidates, output_schema)
                if best_assessment.valid:
                    return best_assessment.projected
                last_error = ValueError(self._format_output_mismatch(best_assessment, output_schema))
                repair_prompt = self._build_repair_prompt(
                    stage=stage,
                    instruction=instruction,
                    task_view=task_view,
                    state_view=state_view,
                    artifact_view=artifact_view,
                    output_schema=output_schema,
                    previous_raw_text=raw_text,
                    feedback=str(last_error),
                )
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                coerced_output = self._coerce_plain_text_output(raw_text, output_schema)
                if coerced_output is not None:
                    return coerced_output
                repair_prompt = self._build_repair_prompt(
                    stage=stage,
                    instruction=instruction,
                    task_view=task_view,
                    state_view=state_view,
                    artifact_view=artifact_view,
                    output_schema=output_schema,
                    previous_raw_text=raw_text,
                    feedback=str(exc),
                )
            if attempt_index == 0 and self._looks_truncated(raw_text):
                max_tokens = max(self.max_output_tokens + 256, int(self.max_output_tokens * 1.5))
                repair_prompt = None
            else:
                max_tokens = max(self.max_output_tokens + 256, int(self.max_output_tokens * 1.5))

        raise RuntimeError(
            f"Local model failed to produce valid JSON for stage {stage.name}.\n"
            f"Raw text:\n{raw_text}\n\nError: {last_error}"
        ) from last_error


def build_default_json_backend() -> JsonGenerationBackend:
    mode = os.environ.get("SKILL_RUNTIME_LLM_MODE", "deterministic").strip().lower()
    if mode == "local-vllm":
        model = os.environ.get("SKILL_RUNTIME_LLM_MODEL", "").strip()
        if not model:
            raise ValueError(
                "local-vllm mode requires SKILL_RUNTIME_LLM_MODEL to point to a local model."
            )
        return LocalVLLMJsonBackend(
            model=model,
            max_model_len=int(os.environ.get("SKILL_RUNTIME_MAX_MODEL_LEN", "8192")),
            tensor_parallel_size=int(os.environ.get("SKILL_RUNTIME_TENSOR_PARALLEL_SIZE", "1")),
            gpu_memory_utilization=float(os.environ.get("SKILL_RUNTIME_GPU_MEMORY_UTILIZATION", "0.6")),
            max_num_seqs=int(os.environ.get("SKILL_RUNTIME_MAX_NUM_SEQS", "16")),
            max_num_batched_tokens=_env_optional_int("SKILL_RUNTIME_MAX_NUM_BATCHED_TOKENS"),
            enforce_eager=_env_flag("SKILL_RUNTIME_ENFORCE_EAGER", True),
            enable_prefix_caching=_env_flag("SKILL_RUNTIME_ENABLE_PREFIX_CACHING", True),
            max_output_tokens=int(os.environ.get("SKILL_RUNTIME_LLM_MAX_OUTPUT_TOKENS", "768")),
        )
    return DeterministicJsonBackend()
