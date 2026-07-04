"""Shared state-transfer helpers for local agent experiments."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .schemas import (
    MethodStats,
    SLOPolicyConfig,
    StageDependencyContract,
    StageRoutingTrace,
    StateDependencySpec,
    StateReadRecord,
    StateTransferMethodArtifacts,
    StoredStateArtifact,
)
from .utils import count_tokens_simple


class StructuredStateStore:
    """Minimal typed artifact store for workflow state externalization."""

    def __init__(self, method_name: str) -> None:
        self.method_name = method_name
        self.artifacts: List[StoredStateArtifact] = []

    def add(self, artifact: StoredStateArtifact) -> None:
        self.artifacts.append(artifact)

    def list(self, artifact_type: Optional[str] = None) -> List[StoredStateArtifact]:
        if artifact_type is None:
            return list(self.artifacts)
        return [artifact for artifact in self.artifacts if artifact.artifact_type == artifact_type]


class DependencyAwareContextAssembler:
    """Assemble stage context from typed artifacts under an SLO budget."""

    def __init__(
        self,
        policy_config: SLOPolicyConfig,
        token_counter: Optional[Callable[[str], int]] = None,
    ) -> None:
        self.policy_config = policy_config
        self.token_counter = token_counter or count_tokens_simple

    def _estimate_tokens(self, text: str) -> int:
        return max(1, self.token_counter(text)) if text else 0

    def _estimate_latency_s(self, text: str) -> float:
        tokens = self._estimate_tokens(text)
        return self.policy_config.view_read_overhead_seconds + (
            self.policy_config.seconds_per_token * tokens
        )

    @staticmethod
    def _serialize_payload(payload: object) -> str:
        if isinstance(payload, str):
            return payload.strip()
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _resolve_view(self, artifact: StoredStateArtifact, view_name: str) -> str:
        if view_name in artifact.views:
            return artifact.views[view_name].strip()
        return self._serialize_payload(artifact.payload)

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        raw_terms = re.findall(r"[a-z0-9_./-]+|[\u4e00-\u9fff]{2,}", query.lower())
        return [term for term in raw_terms if len(term.strip()) >= 2]

    def _relevance_score(
        self,
        query: str,
        content: str,
        spec: StateDependencySpec,
    ) -> float:
        query_terms = self._query_terms(query)
        if not query_terms:
            return spec.relevance_weight
        lowered = content.lower()
        matches = sum(1 for term in query_terms if term in lowered)
        normalized = matches / max(1, len(query_terms))
        return spec.relevance_weight * (1.0 + normalized)

    def _candidate_records(
        self,
        store: StructuredStateStore,
        spec: StateDependencySpec,
        user_query: str,
    ) -> List[Tuple[StateReadRecord, str]]:
        candidates: List[Tuple[StateReadRecord, str]] = []
        for artifact in store.list(spec.artifact_type):
            view_text = self._resolve_view(artifact, spec.view_name)
            if not view_text:
                continue
            content = view_text.strip()
            record = StateReadRecord(
                artifact_id=artifact.artifact_id,
                artifact_type=artifact.artifact_type,
                artifact_label=artifact.label,
                view_name=spec.view_name,
                required=spec.required,
                selected=False,
                estimated_tokens=self._estimate_tokens(content),
                estimated_latency_s=self._estimate_latency_s(content),
                relevance_score=self._relevance_score(
                    user_query,
                    "\n".join((artifact.label, content)),
                    spec,
                ),
                content_preview=content[:160],
            )
            candidates.append((record, content))
        candidates.sort(key=lambda item: (-item[0].relevance_score, item[0].artifact_label, item[0].artifact_id))
        return candidates

    @staticmethod
    def _format_section(record: StateReadRecord, content: str) -> str:
        return (
            f"[artifact={record.artifact_label} type={record.artifact_type} "
            f"view={record.view_name}]\n{content.strip()}"
        )

    def assemble_context(
        self,
        store: StructuredStateStore,
        contract: StageDependencyContract,
        user_query: str,
        remaining_slo_s: float,
    ) -> Tuple[str, StageRoutingTrace]:
        trace = StageRoutingTrace(
            stage_name=contract.stage_name,
            remaining_slo_before_s=remaining_slo_s,
        )
        sections: List[str] = []
        total_selected_latency = 0.0

        for spec in contract.required_dependencies:
            candidates = self._candidate_records(store, spec, user_query)
            limit = spec.max_items if spec.max_items is not None else len(candidates)
            for idx, (record, content) in enumerate(candidates):
                record.required = True
                if idx < limit:
                    record.selected = True
                    sections.append(self._format_section(record, content))
                    trace.required_context_tokens += record.estimated_tokens
                    total_selected_latency += record.estimated_latency_s
                trace.read_records.append(record)

        optional_budget_s = max(
            0.0,
            remaining_slo_s
            - contract.estimated_base_seconds
            - contract.future_reserve_seconds
            - self.policy_config.stage_safety_margin_seconds
            - total_selected_latency,
        )

        optional_pool: List[Tuple[float, StateReadRecord, str]] = []
        for spec in contract.optional_dependencies:
            candidates = self._candidate_records(store, spec, user_query)
            limit = spec.max_items if spec.max_items is not None else len(candidates)
            for idx, (record, content) in enumerate(candidates):
                if idx >= limit:
                    trace.read_records.append(record)
                    continue
                ratio = record.relevance_score / max(record.estimated_latency_s, 1e-6)
                optional_pool.append((ratio, record, content))

        optional_pool.sort(key=lambda item: (-item[0], item[1].artifact_label, item[1].artifact_id))
        for _, record, content in optional_pool:
            if record.relevance_score < self.policy_config.optional_selection_score_threshold:
                trace.read_records.append(record)
                continue
            if record.estimated_latency_s <= optional_budget_s:
                record.selected = True
                sections.append(self._format_section(record, content))
                trace.optional_context_tokens += record.estimated_tokens
                total_selected_latency += record.estimated_latency_s
                optional_budget_s -= record.estimated_latency_s
            trace.read_records.append(record)

        trace.selected_context_tokens = trace.required_context_tokens + trace.optional_context_tokens
        trace.estimated_context_read_time_s = total_selected_latency
        if not sections:
            trace.notes.append("No prior state views were selected for this stage.")
        if contract.optional_dependencies:
            trace.notes.append(
                "Optional read budget after reserve: "
                f"{max(optional_budget_s, 0.0):.3f}s."
            )
        return "\n\n".join(sections).strip(), trace


def append_method_log(
    artifacts: StateTransferMethodArtifacts,
    log,
    stage_wall_time_s: float,
) -> None:
    """Accumulate one stage log into the method totals."""
    artifacts.stats.add_log(log)
    artifacts.logs.append(asdict(log))
    artifacts.total_wall_time_s += stage_wall_time_s


def remaining_slo_seconds(
    policy_config: SLOPolicyConfig,
    artifacts: StateTransferMethodArtifacts,
) -> float:
    """Compute the remaining end-to-end SLO budget."""
    return policy_config.total_slo_seconds - policy_config.retrieval_elapsed_seconds - artifacts.total_wall_time_s


def completion_proxy(artifacts: StateTransferMethodArtifacts) -> float:
    """Generic fallback completion proxy across the shared state slots."""
    state_fields = (
        "intake_state",
        "signal_state",
        "query_state",
        "evidence_state",
        "candidate_state",
        "eligibility_state",
        "hypothesis_state",
        "decision_state",
        "plan_state",
        "response_state",
        "review_state",
    )
    weight = 1.0 / len(state_fields)
    score = 0.0
    for field_name in state_fields:
        if getattr(artifacts, field_name, None):
            score += weight
    return min(score, 1.0)


def safe_reduction(candidate: float, reference: float) -> Optional[float]:
    """Return 1 - candidate/reference when the reference is non-zero."""
    if reference > 0:
        return 1.0 - (candidate / reference)
    return None


def application_metrics_delta(
    candidate_stats: MethodStats,
    reference_stats: MethodStats,
) -> Dict[str, Optional[float]]:
    """Compute resource deltas between two strategies."""
    return {
        "prompt_tokens_delta": candidate_stats.prompt_tokens - reference_stats.prompt_tokens,
        "output_tokens_delta": candidate_stats.output_tokens - reference_stats.output_tokens,
        "latency_seconds_delta": candidate_stats.latency_s - reference_stats.latency_s,
        "prompt_reduction_ratio": safe_reduction(
            candidate_stats.prompt_tokens,
            reference_stats.prompt_tokens,
        ),
        "output_reduction_ratio": safe_reduction(
            candidate_stats.output_tokens,
            reference_stats.output_tokens,
        ),
        "latency_reduction_ratio": safe_reduction(
            candidate_stats.latency_s,
            reference_stats.latency_s,
        ),
    }


def routing_metrics_delta(
    candidate: StateTransferMethodArtifacts,
    reference: StateTransferMethodArtifacts,
) -> Dict[str, Optional[float]]:
    """Compute run-level deltas between two transfer strategies."""
    delta = application_metrics_delta(candidate.stats, reference.stats)
    delta.update(
        {
            "wall_time_seconds_delta": candidate.total_wall_time_s - reference.total_wall_time_s,
            "wall_time_reduction_ratio": safe_reduction(
                candidate.total_wall_time_s,
                reference.total_wall_time_s,
            ),
            "completion_proxy_delta": candidate.completion_proxy - reference.completion_proxy,
            "utility_at_slo_proxy_delta": (
                (candidate.completion_proxy if candidate.met_slo else 0.0)
                - (reference.completion_proxy if reference.met_slo else 0.0)
            ),
            "met_slo_delta": float(candidate.met_slo) - float(reference.met_slo),
        }
    )
    return delta
