from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import ConstraintDefinition, StageMetadata


@dataclass
class WorkflowStageGroups:
    stage_order: List[str]
    prefix_stages: List[str]
    anchor_stages: List[str]
    suffix_stages: List[str]
    optional_stages: List[str]
    stage_type_groups: Dict[str, List[str]]
    workflow_phase_groups: Dict[str, List[str]]
    anchor_boundary_stage: Optional[str] = None


@dataclass
class StageRuntimeSnapshot:
    stage_name: str
    stage_index: int
    total_stages: int
    metadata: StageMetadata
    completed_stages: List[str]
    remaining_stages: List[str]
    available_state_keys: List[str]
    available_artifact_keys: List[str]
    reads_state_fields: List[str] = field(default_factory=list)
    reads_artifact_fields: List[str] = field(default_factory=list)
    state_values: Dict[str, object] = field(default_factory=dict)
    consumed_prompt_tokens: int = 0
    consumed_input_view_tokens: int = 0
    consumed_output_tokens: int = 0
    estimated_optional_branch_prompt_tokens_runtime: int = 0
    estimated_optional_branch_prompt_tokens_metadata: int = 0
    estimated_optional_branch_prompt_tokens: int = 0
    optional_branch_stage_names: List[str] = field(default_factory=list)


@dataclass
class StageExecutionDecision:
    execute_stage: bool = True
    executor_override: Optional[str] = None
    reason: str = "default"
    notes: List[str] = field(default_factory=list)


class RuntimeStrategy:
    name = "default"

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name}

    def plan_workflow(
        self,
        skill_name: str,
        canonical_task: Dict[str, object],
        stage_groups: WorkflowStageGroups,
        constraints: List[ConstraintDefinition],
    ) -> WorkflowStageGroups:
        return stage_groups

    def decide_stage(
        self,
        skill_name: str,
        stage_name: str,
        snapshot: StageRuntimeSnapshot,
        stage_groups: WorkflowStageGroups,
        constraints: List[ConstraintDefinition],
    ) -> StageExecutionDecision:
        return StageExecutionDecision()


class DefaultRuntimeStrategy(RuntimeStrategy):
    name = "default"


class SuffixReretrievalPolicyStrategy(RuntimeStrategy):
    name = "suffix-reretrieval-policy"

    def __init__(self, prompt_budget_threshold: int | None = None) -> None:
        self.prompt_budget_threshold = prompt_budget_threshold

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "prompt_budget_threshold": self.prompt_budget_threshold,
            "branch_cost_model": "max(runtime_estimator, skill_metadata_hint)",
        }

    def _remaining_prompt_budget(self, snapshot: StageRuntimeSnapshot) -> int | None:
        if self.prompt_budget_threshold is None:
            return None
        return self.prompt_budget_threshold - snapshot.consumed_prompt_tokens

    def _has_query_signal(self, snapshot: StageRuntimeSnapshot) -> bool:
        for field_name in snapshot.reads_state_fields:
            normalized = field_name.lower()
            if "query" not in normalized and "queries" not in normalized:
                continue
            value = snapshot.state_values.get(field_name)
            if isinstance(value, list) and any(str(item).strip() for item in value):
                return True
            if isinstance(value, str) and value.strip():
                return True
        return False

    def decide_stage(
        self,
        skill_name: str,
        stage_name: str,
        snapshot: StageRuntimeSnapshot,
        stage_groups: WorkflowStageGroups,
        constraints: List[ConstraintDefinition],
    ) -> StageExecutionDecision:
        if not snapshot.metadata.optional_stage:
            return StageExecutionDecision()

        if snapshot.metadata.tool_intent == "re_retrieval":
            retrieval_decision = snapshot.state_values.get("retrieval_decision")
            remaining_budget = self._remaining_prompt_budget(snapshot)
            estimated_branch_cost = snapshot.estimated_optional_branch_prompt_tokens
            if retrieval_decision == "re_retrieve" and self._has_query_signal(snapshot):
                if remaining_budget is not None and remaining_budget < estimated_branch_cost:
                    return StageExecutionDecision(
                        execute_stage=False,
                        reason="policy_skip_suffix_reretrieval_budget",
                        notes=[
                            "Structured follow-up queries are present, but the optional re-retrieval branch "
                            "is skipped because the remaining prompt budget is smaller than the "
                            f"estimated branch cost. remaining_prompt_budget={remaining_budget}, "
                            f"estimated_branch_prompt_cost={estimated_branch_cost}, "
                            f"runtime_estimate={snapshot.estimated_optional_branch_prompt_tokens_runtime}, "
                            f"metadata_hint={snapshot.estimated_optional_branch_prompt_tokens_metadata}, "
                            f"consumed_prompt_tokens={snapshot.consumed_prompt_tokens}, "
                            f"threshold={self.prompt_budget_threshold}."
                        ],
                    )
                return StageExecutionDecision(
                    execute_stage=True,
                    reason="policy_open_suffix_reretrieval",
                    notes=[
                        "Structured follow-up queries are present, so the optional re-retrieval subloop is enabled."
                        + (
                            f" Remaining prompt budget before the branch: {remaining_budget}."
                            if remaining_budget is not None
                            else ""
                        )
                        + (
                            f" Estimated branch prompt cost: {estimated_branch_cost}. "
                            f"Runtime estimate: {snapshot.estimated_optional_branch_prompt_tokens_runtime}. "
                            f"Metadata hint: {snapshot.estimated_optional_branch_prompt_tokens_metadata}."
                        )
                    ],
                )
            return StageExecutionDecision(
                execute_stage=False,
                reason="policy_skip_suffix_reretrieval",
                notes=["No structured follow-up query signal requires a suffix re-retrieval pass."],
            )

        if snapshot.metadata.stage_type == "compression" and snapshot.reads_artifact_fields:
            missing_artifacts = [
                artifact_name
                for artifact_name in snapshot.reads_artifact_fields
                if artifact_name not in snapshot.available_artifact_keys
            ]
            if missing_artifacts:
                return StageExecutionDecision(
                    execute_stage=False,
                    reason="policy_skip_optional_compression",
                    notes=[
                        "Optional compression stage skipped because its upstream suffix artifact pool was not produced: "
                        f"{missing_artifacts}."
                    ],
                )
            return StageExecutionDecision(
                execute_stage=True,
                reason="policy_run_optional_compression",
                notes=["Optional compression stage is enabled because the suffix artifact pool is available."],
            )

        return StageExecutionDecision()


class SupportResponseBudgetPolicyStrategy(RuntimeStrategy):
    name = "support-response-budget-policy"

    def __init__(self, prompt_budget_threshold: int | None = None) -> None:
        self.prompt_budget_threshold = prompt_budget_threshold

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "prompt_budget_threshold": self.prompt_budget_threshold,
            "branch_cost_model": "max(runtime_estimator, skill_metadata_hint)",
            "state_gate": "urgency_or_handoff_complexity",
        }

    def _remaining_prompt_budget(self, snapshot: StageRuntimeSnapshot) -> int | None:
        if self.prompt_budget_threshold is None:
            return None
        return self.prompt_budget_threshold - snapshot.consumed_prompt_tokens

    def _refinement_reasons(self, snapshot: StageRuntimeSnapshot) -> List[str]:
        reasons: List[str] = []
        state = snapshot.state_values
        urgency = str(state.get("urgency", "")).strip()
        handoff_status = str(state.get("handoff_status", "")).strip()
        decision = str(state.get("eligibility_decision", "")).strip()
        guardrails = list(state.get("response_guardrails", []) or [])
        missing_information = list(state.get("missing_information", []) or [])
        blockers = list(state.get("blocking_requirements", []) or [])
        risk_flags = list(state.get("risk_flags", []) or [])

        if urgency == "high":
            reasons.append("high_urgency")
        if handoff_status in {"blocked", "escalate"}:
            reasons.append(f"handoff_status={handoff_status}")
        if decision in {"needs_info", "escalate", "partial"}:
            reasons.append(f"eligibility_decision={decision}")
        if missing_information:
            reasons.append("missing_information_present")
        if blockers:
            reasons.append("blocking_requirements_present")
        if guardrails:
            reasons.append("response_guardrails_present")
        if risk_flags:
            reasons.append("risk_flags_present")
        return reasons

    def decide_stage(
        self,
        skill_name: str,
        stage_name: str,
        snapshot: StageRuntimeSnapshot,
        stage_groups: WorkflowStageGroups,
        constraints: List[ConstraintDefinition],
    ) -> StageExecutionDecision:
        if stage_name != "response_refinement" or not snapshot.metadata.optional_stage:
            return StageExecutionDecision()

        refinement_reasons = self._refinement_reasons(snapshot)
        if not refinement_reasons:
            return StageExecutionDecision(
                execute_stage=False,
                reason="policy_skip_support_refinement_state",
                notes=[
                    "Optional response refinement skipped because the structured state does not indicate "
                    "a high-urgency, blocker-heavy, escalation, or risk-sensitive handoff."
                ],
            )

        remaining_budget = self._remaining_prompt_budget(snapshot)
        estimated_branch_cost = snapshot.estimated_optional_branch_prompt_tokens
        if remaining_budget is not None and remaining_budget < estimated_branch_cost:
            return StageExecutionDecision(
                execute_stage=False,
                reason="policy_skip_support_refinement_budget",
                notes=[
                    "Optional response refinement is relevant for this case, but the remaining prompt "
                    "budget is smaller than the estimated branch cost. "
                    f"state_triggers={refinement_reasons}, "
                    f"remaining_prompt_budget={remaining_budget}, "
                    f"estimated_branch_prompt_cost={estimated_branch_cost}, "
                    f"runtime_estimate={snapshot.estimated_optional_branch_prompt_tokens_runtime}, "
                    f"metadata_hint={snapshot.estimated_optional_branch_prompt_tokens_metadata}, "
                    f"consumed_prompt_tokens={snapshot.consumed_prompt_tokens}, "
                    f"threshold={self.prompt_budget_threshold}."
                ],
            )

        note = (
            "Optional response refinement enabled because the structured handoff state suggests that "
            f"extra messaging care is useful. state_triggers={refinement_reasons}. "
            f"Estimated branch prompt cost: {estimated_branch_cost}. "
            f"Runtime estimate: {snapshot.estimated_optional_branch_prompt_tokens_runtime}. "
            f"Metadata hint: {snapshot.estimated_optional_branch_prompt_tokens_metadata}."
        )
        if remaining_budget is not None:
            note += f" Remaining prompt budget before the branch: {remaining_budget}."
        return StageExecutionDecision(
            execute_stage=True,
            reason="policy_open_support_refinement",
            notes=[note],
        )


def build_runtime_strategy(
    name: str | None = None,
    *,
    prompt_budget_threshold: int | None = None,
) -> RuntimeStrategy:
    if not name or name == "default":
        return DefaultRuntimeStrategy()
    if name == SuffixReretrievalPolicyStrategy.name:
        return SuffixReretrievalPolicyStrategy(
            prompt_budget_threshold=prompt_budget_threshold
        )
    if name == SupportResponseBudgetPolicyStrategy.name:
        return SupportResponseBudgetPolicyStrategy(
            prompt_budget_threshold=prompt_budget_threshold
        )
    raise ValueError(f"Unknown runtime strategy: {name}")


def available_runtime_strategies() -> List[str]:
    return [
        "default",
        SuffixReretrievalPolicyStrategy.name,
        SupportResponseBudgetPolicyStrategy.name,
    ]
