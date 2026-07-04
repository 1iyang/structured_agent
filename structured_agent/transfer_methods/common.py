"""Shared types and runtime helpers for reusable transfer methods."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, Generic, List, Optional, Protocol, Sequence, Tuple, Type, TypeVar

from pydantic import BaseModel, Field

from ..schemas import (
    SLOPolicyConfig,
    StageDependencyContract,
    StageRoutingTrace,
    StateDependencySpec,
    StateReadRecord,
    StateTransferMethodArtifacts,
    StoredStateArtifact,
)
from ..state_transfer import StructuredStateStore, append_method_log, completion_proxy, remaining_slo_seconds
from ..utils import count_tokens_simple

TTask = TypeVar("TTask")
TModel = TypeVar("TModel", bound=BaseModel)


class PlannerReadChoice(BaseModel):
    """One planner-selected structured read."""

    read_id: str
    reason: str = Field(default="")


class StructuredDependencyPlan(BaseModel):
    """Planner output over available structured state reads."""

    selected_reads: List[PlannerReadChoice] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


@dataclass(frozen=True)
class InitialStructuredStage(Generic[TTask]):
    """Definition for the first structured stage in a workflow."""

    stage_name: str
    result_field: str
    schema_model: Type[BaseModel]
    prompt_builder: Callable[[TTask], str]
    artifact_builder: Callable[[BaseModel, str], StoredStateArtifact]
    max_tokens: int = 260
    notes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutedStructuredStage(Generic[TTask]):
    """Definition for downstream stages that consume transferred context."""

    stage_name: str
    result_field: str
    schema_model: Type[BaseModel]
    prompt_builder: Callable[[TTask, str], str]
    artifact_builder: Callable[[BaseModel, str], StoredStateArtifact]
    max_tokens: int = 360


@dataclass(frozen=True)
class HistoryCompressionConfig:
    """Optional configuration for structured history compression."""

    schema_model: Optional[Type[BaseModel]] = None
    render_model: Optional[Callable[[BaseModel], str]] = None
    min_history_tokens: int = 4000
    skip_stage_names: Tuple[str, ...] = ()
    max_tokens: int = 320


DEFAULT_HISTORY_COMPRESSION_CONFIG = HistoryCompressionConfig()


def default_history_compression_config(**overrides: object) -> HistoryCompressionConfig:
    """Return the shared baseline compression policy with optional overrides."""

    return replace(DEFAULT_HISTORY_COMPRESSION_CONFIG, **overrides)


def resolve_history_compression_config(adapter: object) -> HistoryCompressionConfig:
    """Resolve workflow-specific compression config, falling back to shared defaults."""

    config_builder = getattr(adapter, "history_compression_config", None)
    if config_builder is None:
        return default_history_compression_config()
    config = config_builder()
    if config is None:
        return default_history_compression_config()
    return config


class TransferWorkflowAdapter(Protocol[TTask]):
    """Workflow-provided task semantics for the shared transfer methods."""

    def build_structured_store(self, task: TTask, method_name: str) -> StructuredStateStore:
        """Return the precomputed structured state available before stage execution."""

    def build_history_seed(self, task: TTask) -> str:
        """Return the raw running-history seed for history-carry baselines."""

    def initial_stage(self) -> InitialStructuredStage[TTask]:
        """Return the first structured stage shared by all methods."""

    def routed_stages(self) -> Sequence[RoutedStructuredStage[TTask]]:
        """Return the common downstream stage skeleton for the experiment."""

    def fixed_dependency_contracts(self) -> Dict[str, StageDependencyContract]:
        """Return the fixed dependency contracts for structured fixed-deps execution."""

    def planner_optional_specs(self, stage_name: str) -> Sequence[StateDependencySpec]:
        """Return candidate optional reads that the planner may select from."""

    def build_planner_prompt(
        self,
        task: TTask,
        stage_name: str,
        available_reads_text: str,
    ) -> str:
        """Build the planner prompt for one routed stage."""

    def build_history_compression_prompt(
        self,
        task: TTask,
        next_stage_name: str,
        history_text: str,
    ) -> str:
        """Build the compression prompt for one history-carry stage transition."""


class TransferMethodRuntime:
    """Shared runtime for executing the four transfer strategies."""

    def __init__(
        self,
        runner,
        token_counter: Optional[Callable[[str], int]] = None,
    ) -> None:
        self.runner = runner
        self._token_counter = token_counter or getattr(runner, "count_tokens", count_tokens_simple)

    def _estimate_tokens(self, text: str) -> int:
        return max(1, self._token_counter(text)) if text else 0

    def _estimate_latency_s(self, policy_config: SLOPolicyConfig, text: str) -> float:
        tokens = self._estimate_tokens(text)
        return policy_config.view_read_overhead_seconds + (policy_config.seconds_per_token * tokens)

    @staticmethod
    def _format_section(record: StateReadRecord, content: str) -> str:
        return (
            f"[artifact={record.artifact_label} type={record.artifact_type} "
            f"view={record.view_name}]\n{content.strip()}"
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

    def _make_read_record(
        self,
        artifact: StoredStateArtifact,
        view_name: str,
        content: str,
        policy_config: SLOPolicyConfig,
        required: bool,
    ) -> StateReadRecord:
        return StateReadRecord(
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type,
            artifact_label=artifact.label,
            view_name=view_name,
            required=required,
            selected=True,
            estimated_tokens=self._estimate_tokens(content),
            estimated_latency_s=self._estimate_latency_s(policy_config, content),
            relevance_score=1.0,
            content_preview=content[:160],
        )

    def run_structured_stage(
        self,
        artifacts: StateTransferMethodArtifacts,
        stage_name: str,
        prompt: str,
        schema_model: Type[TModel],
        max_tokens: int,
    ) -> Tuple[TModel, float]:
        stage_start = time.perf_counter()
        parsed, log = self.runner.generate_json(
            prompt,
            schema_model,
            max_tokens=max_tokens,
            temperature=0.0,
            stage_name=stage_name,
        )
        stage_wall_time = time.perf_counter() - stage_start
        append_method_log(artifacts, log, stage_wall_time)
        return parsed, stage_wall_time

    def run_text_stage(
        self,
        artifacts: StateTransferMethodArtifacts,
        stage_name: str,
        prompt: str,
        max_tokens: int,
    ) -> Tuple[str, float]:
        stage_start = time.perf_counter()
        text, log = self.runner.generate_text(
            prompt,
            max_tokens=max_tokens,
            temperature=0.0,
            stage_name=stage_name,
        )
        stage_wall_time = time.perf_counter() - stage_start
        append_method_log(artifacts, log, stage_wall_time)
        return text, stage_wall_time

    @staticmethod
    def append_history(history_text: str, stage_name: str, payload: object) -> str:
        rendered = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
        if not history_text.strip():
            return f"[{stage_name}]\n{rendered}"
        return f"{history_text.strip()}\n\n[{stage_name}]\n{rendered}"

    def history_trace(
        self,
        policy_config: SLOPolicyConfig,
        stage_name: str,
        history_text: str,
    ) -> StageRoutingTrace:
        record = StateReadRecord(
            artifact_id=f"history:{stage_name}",
            artifact_type="running_history",
            artifact_label="running_history",
            view_name="full",
            required=True,
            selected=True,
            estimated_tokens=self._estimate_tokens(history_text),
            estimated_latency_s=self._estimate_latency_s(policy_config, history_text),
            relevance_score=1.0,
            content_preview=history_text[:160],
        )
        return StageRoutingTrace(
            stage_name=stage_name,
            selected_context_tokens=record.estimated_tokens,
            estimated_context_read_time_s=record.estimated_latency_s,
            read_records=[record],
        )

    def run_initial_stage(
        self,
        task: TTask,
        policy_config: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        store: StructuredStateStore,
        spec: InitialStructuredStage[TTask],
    ) -> BaseModel:
        remaining_before = remaining_slo_seconds(policy_config, artifacts)
        trace = StageRoutingTrace(
            stage_name=spec.stage_name,
            remaining_slo_before_s=remaining_before,
            notes=list(spec.notes),
        )
        prompt = spec.prompt_builder(task)
        parsed, stage_wall_time = self.run_structured_stage(
            artifacts,
            stage_name=f"{artifacts.method_name}_{spec.stage_name}",
            prompt=prompt,
            schema_model=spec.schema_model,
            max_tokens=spec.max_tokens,
        )
        setattr(artifacts, spec.result_field, parsed.model_dump())
        store.add(spec.artifact_builder(parsed, artifacts.method_name))
        trace.actual_stage_wall_time_s = stage_wall_time
        trace.remaining_slo_after_s = remaining_slo_seconds(policy_config, artifacts)
        artifacts.stage_traces.append(trace)
        return parsed

    def build_fixed_context(
        self,
        policy_config: SLOPolicyConfig,
        store: StructuredStateStore,
        contract: StageDependencyContract,
    ) -> Tuple[str, StageRoutingTrace]:
        trace = StageRoutingTrace(stage_name=contract.stage_name)
        sections: List[str] = []
        for spec in contract.required_dependencies:
            selected = 0
            limit = spec.max_items if spec.max_items is not None else None
            for artifact in store.list(spec.artifact_type):
                if limit is not None and selected >= limit:
                    break
                content = self._resolve_view(artifact, spec.view_name)
                if not content:
                    continue
                record = self._make_read_record(
                    artifact,
                    spec.view_name,
                    content,
                    policy_config,
                    required=True,
                )
                trace.read_records.append(record)
                trace.selected_context_tokens += record.estimated_tokens
                trace.required_context_tokens += record.estimated_tokens
                trace.estimated_context_read_time_s += record.estimated_latency_s
                sections.append(self._format_section(record, content))
                selected += 1
        return "\n\n".join(sections).strip(), trace

    def run_fixeddeps_stage(
        self,
        task: TTask,
        policy_config: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        store: StructuredStateStore,
        contract: StageDependencyContract,
        stage: RoutedStructuredStage[TTask],
    ) -> BaseModel:
        remaining_before = remaining_slo_seconds(policy_config, artifacts)
        context_text, trace = self.build_fixed_context(policy_config, store, contract)
        trace.stage_name = contract.stage_name
        trace.remaining_slo_before_s = remaining_before
        prompt = stage.prompt_builder(task, context_text)
        parsed, stage_wall_time = self.run_structured_stage(
            artifacts,
            stage_name=f"{artifacts.method_name}_{contract.stage_name}",
            prompt=prompt,
            schema_model=stage.schema_model,
            max_tokens=stage.max_tokens,
        )
        setattr(artifacts, stage.result_field, parsed.model_dump())
        store.add(stage.artifact_builder(parsed, artifacts.method_name))
        trace.actual_stage_wall_time_s = stage_wall_time
        trace.remaining_slo_after_s = remaining_slo_seconds(policy_config, artifacts)
        artifacts.stage_traces.append(trace)
        return parsed

    def planner_candidates(
        self,
        policy_config: SLOPolicyConfig,
        store: StructuredStateStore,
        specs: Sequence[StateDependencySpec],
    ) -> Dict[str, Tuple[StateReadRecord, str]]:
        candidates: Dict[str, Tuple[StateReadRecord, str]] = {}
        next_id = 1
        for spec in specs:
            selected = 0
            limit = spec.max_items if spec.max_items is not None else None
            for artifact in store.list(spec.artifact_type):
                if limit is not None and selected >= limit:
                    break
                content = self._resolve_view(artifact, spec.view_name)
                if not content:
                    continue
                record = self._make_read_record(
                    artifact,
                    spec.view_name,
                    content,
                    policy_config,
                    required=False,
                )
                read_id = f"read_{next_id}"
                candidates[read_id] = (record, content)
                next_id += 1
                selected += 1
        return candidates

    def run_planner_stage(
        self,
        task: TTask,
        policy_config: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        stage_name: str,
        candidates: Dict[str, Tuple[StateReadRecord, str]],
        build_prompt: Callable[[TTask, str, str], str],
    ) -> List[Tuple[StateReadRecord, str]]:
        trace = StageRoutingTrace(
            stage_name=f"plan_{stage_name}",
            remaining_slo_before_s=remaining_slo_seconds(policy_config, artifacts),
        )
        if not candidates:
            trace.notes.append("No additional structured reads were available for planning.")
            trace.remaining_slo_after_s = trace.remaining_slo_before_s
            artifacts.stage_traces.append(trace)
            return []

        available_lines = []
        for read_id, (record, content) in candidates.items():
            available_lines.append(
                f"{read_id} | artifact={record.artifact_label} | "
                f"type={record.artifact_type} | view={record.view_name} | "
                f"tok_est={record.estimated_tokens} | preview={content[:120].replace(chr(10), ' ')}"
            )
        available_text = "\n".join(available_lines)
        planner_prompt = build_prompt(task, stage_name, available_text)
        plan, stage_wall_time = self.run_structured_stage(
            artifacts,
            stage_name=f"{artifacts.method_name}_plan_{stage_name}",
            prompt=planner_prompt,
            schema_model=StructuredDependencyPlan,
            max_tokens=220,
        )
        selected: List[Tuple[StateReadRecord, str]] = []
        seen_ids = set()
        for choice in plan.selected_reads:
            if choice.read_id in seen_ids or choice.read_id not in candidates:
                continue
            seen_ids.add(choice.read_id)
            selected.append(candidates[choice.read_id])
        trace.notes.extend(plan.notes)
        trace.notes.append(
            f"Planner selected {len(selected)} additional structured reads out of {len(candidates)} candidates."
        )
        trace.actual_stage_wall_time_s = stage_wall_time
        trace.remaining_slo_after_s = remaining_slo_seconds(policy_config, artifacts)
        artifacts.stage_traces.append(trace)
        return selected

    def run_planner_stage_execution(
        self,
        task: TTask,
        policy_config: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        store: StructuredStateStore,
        contract: StageDependencyContract,
        stage: RoutedStructuredStage[TTask],
        planner_optional_specs: Sequence[StateDependencySpec],
        build_planner_prompt: Callable[[TTask, str, str], str],
    ) -> BaseModel:
        remaining_before = remaining_slo_seconds(policy_config, artifacts)
        base_context, trace = self.build_fixed_context(policy_config, store, contract)
        planner_candidates = self.planner_candidates(
            policy_config,
            store,
            planner_optional_specs,
        )
        planner_selected = self.run_planner_stage(
            task,
            policy_config,
            artifacts,
            contract.stage_name,
            planner_candidates,
            build_planner_prompt,
        )
        sections = [base_context] if base_context else []
        for record, content in planner_selected:
            trace.read_records.append(record)
            trace.selected_context_tokens += record.estimated_tokens
            trace.optional_context_tokens += record.estimated_tokens
            trace.estimated_context_read_time_s += record.estimated_latency_s
            sections.append(self._format_section(record, content))
        trace.stage_name = contract.stage_name
        trace.remaining_slo_before_s = remaining_before
        prompt = stage.prompt_builder(task, "\n\n".join(section for section in sections if section))
        parsed, stage_wall_time = self.run_structured_stage(
            artifacts,
            stage_name=f"{artifacts.method_name}_{contract.stage_name}",
            prompt=prompt,
            schema_model=stage.schema_model,
            max_tokens=stage.max_tokens,
        )
        setattr(artifacts, stage.result_field, parsed.model_dump())
        store.add(stage.artifact_builder(parsed, artifacts.method_name))
        trace.actual_stage_wall_time_s = stage_wall_time
        trace.remaining_slo_after_s = remaining_slo_seconds(policy_config, artifacts)
        artifacts.stage_traces.append(trace)
        return parsed

    def run_history_stage(
        self,
        task: TTask,
        policy_config: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        stage: RoutedStructuredStage[TTask],
        history_text: str,
    ) -> BaseModel:
        remaining_before = remaining_slo_seconds(policy_config, artifacts)
        trace = self.history_trace(policy_config, stage.stage_name, history_text)
        trace.remaining_slo_before_s = remaining_before
        prompt = stage.prompt_builder(task, history_text)
        parsed, stage_wall_time = self.run_structured_stage(
            artifacts,
            stage_name=f"{artifacts.method_name}_{stage.stage_name}",
            prompt=prompt,
            schema_model=stage.schema_model,
            max_tokens=stage.max_tokens,
        )
        setattr(artifacts, stage.result_field, parsed.model_dump())
        trace.actual_stage_wall_time_s = stage_wall_time
        trace.remaining_slo_after_s = remaining_slo_seconds(policy_config, artifacts)
        artifacts.stage_traces.append(trace)
        return parsed

    def compress_history(
        self,
        task: TTask,
        policy_config: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        next_stage_name: str,
        history_text: str,
        build_prompt: Callable[[TTask, str, str], str],
        compression_config: Optional[HistoryCompressionConfig] = None,
    ) -> str:
        config = compression_config or default_history_compression_config()
        remaining_before = remaining_slo_seconds(policy_config, artifacts)
        history_tokens = self._estimate_tokens(history_text)
        if next_stage_name in config.skip_stage_names or history_tokens < config.min_history_tokens:
            trace = StageRoutingTrace(
                stage_name=f"compress_before_{next_stage_name}",
                skipped=True,
                remaining_slo_before_s=remaining_before,
                remaining_slo_after_s=remaining_before,
            )
            if next_stage_name in config.skip_stage_names:
                trace.notes.append("Skipped compression before this stage by workflow policy.")
            else:
                trace.notes.append(
                    f"Skipped compression because history was below the {config.min_history_tokens}-token threshold."
                )
            artifacts.stage_traces.append(trace)
            return history_text

        trace = self.history_trace(policy_config, f"compress_before_{next_stage_name}", history_text)
        trace.remaining_slo_before_s = remaining_before
        prompt = build_prompt(task, next_stage_name, history_text)
        if config.schema_model is not None and config.render_model is not None:
            compressed_state, stage_wall_time = self.run_structured_stage(
                artifacts,
                stage_name=f"{artifacts.method_name}_compress_before_{next_stage_name}",
                prompt=prompt,
                schema_model=config.schema_model,
                max_tokens=config.max_tokens,
            )
            compressed_text = config.render_model(compressed_state)
            trace.notes.append("Compressed the running history into structured memory slots.")
        else:
            compressed_text, stage_wall_time = self.run_text_stage(
                artifacts,
                stage_name=f"{artifacts.method_name}_compress_before_{next_stage_name}",
                prompt=prompt,
                max_tokens=config.max_tokens,
            )
            compressed_text = compressed_text.strip()
            trace.notes.append("Compressed the running history before the next stage.")
        trace.actual_stage_wall_time_s = stage_wall_time
        trace.remaining_slo_after_s = remaining_slo_seconds(policy_config, artifacts)
        artifacts.stage_traces.append(trace)
        return compressed_text


def finalize_method(
    artifacts: StateTransferMethodArtifacts,
    policy_config: SLOPolicyConfig,
    store: Optional[StructuredStateStore] = None,
) -> None:
    """Finalize one method run after all stages complete."""
    if store is not None:
        artifacts.state_artifacts = list(store.artifacts)
    artifacts.completion_proxy = completion_proxy(artifacts)
    artifacts.met_slo = (
        policy_config.retrieval_elapsed_seconds + artifacts.total_wall_time_s
        <= policy_config.total_slo_seconds
    )
