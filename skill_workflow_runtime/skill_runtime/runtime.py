from __future__ import annotations

import json
from typing import Any, Dict, List

from .builtins import BUILTIN_EXECUTORS, BUILTIN_TOOLS
from .llm import JsonGenerationBackend, build_default_json_backend, render_json_stage_prompt
from .models import InputProfile, OutputSchemaDefinition, SkillManifest, StageDefinition, TaskPayload
from .strategy import (
    DefaultRuntimeStrategy,
    RuntimeStrategy,
    StageExecutionDecision,
    StageRuntimeSnapshot,
    WorkflowStageGroups,
)


class SkillWorkflowRuntime:
    def __init__(
        self,
        llm_backend: JsonGenerationBackend | None = None,
        strategy: RuntimeStrategy | None = None,
    ) -> None:
        self._executors = dict(BUILTIN_EXECUTORS)
        self._tools = dict(BUILTIN_TOOLS)
        self._llm_backend = llm_backend or build_default_json_backend()
        self._strategy = strategy or DefaultRuntimeStrategy()

    def _serialize_payload(self, payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)

    def close(self) -> None:
        close_backend = getattr(self._llm_backend, "close", None)
        if callable(close_backend):
            close_backend()

    def _count_tokens(self, text: str) -> int:
        return self._llm_backend.count_tokens(text)

    def _estimate_stage_token_usage(
        self,
        stage: StageDefinition,
        executor_name: str,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
        state_output: Dict[str, Any],
        artifact_output: Dict[str, Any],
        instruction: str,
        output_schema: OutputSchemaDefinition | None,
    ) -> Dict[str, Any]:
        task_text = self._serialize_payload(task_view)
        state_text = self._serialize_payload(state_view)
        artifact_text = self._serialize_payload(artifact_view)
        state_output_text = self._serialize_payload(state_output)
        artifact_output_text = self._serialize_payload(artifact_output)
        usage = {
            "task_view_tokens": self._count_tokens(task_text),
            "state_view_tokens": self._count_tokens(state_text),
            "artifact_view_tokens": self._count_tokens(artifact_text),
            "state_output_tokens": self._count_tokens(state_output_text),
            "artifact_output_tokens": self._count_tokens(artifact_output_text),
        }
        usage["input_view_tokens"] = (
            usage["task_view_tokens"] + usage["state_view_tokens"] + usage["artifact_view_tokens"]
        )
        usage["output_tokens"] = usage["state_output_tokens"] + usage["artifact_output_tokens"]

        if executor_name == "llm.json_stage" and output_schema is not None:
            prompt_text = render_json_stage_prompt(
                stage=stage,
                instruction=instruction,
                task_view=task_view,
                state_view=state_view,
                artifact_view=artifact_view,
                output_schema=output_schema,
                prompt_header="You are executing one workflow stage on a local model.",
            )
            usage["prompt_tokens"] = self._count_tokens(prompt_text)
        else:
            usage["prompt_tokens"] = None
        return usage

    def _summarize_token_usage(
        self,
        stage_groups: WorkflowStageGroups,
        stage_records: List[Dict[str, Any]],
        canonical_task: Dict[str, Any],
    ) -> Dict[str, Any]:
        def sum_stage_tokens(stage_names: List[str], field_name: str) -> int:
            total = 0
            for record in stage_records:
                if record.get("name") not in stage_names or record.get("skipped"):
                    continue
                value = record.get("token_usage", {}).get(field_name)
                if isinstance(value, int):
                    total += value
            return total

        executed_stage_names = [
            record["name"] for record in stage_records if not record.get("skipped")
        ]
        return {
            "token_count_mode": getattr(self._llm_backend, "token_count_mode", "unknown"),
            "canonical_task_tokens": self._count_tokens(self._serialize_payload(canonical_task)),
            "executed_stages": executed_stage_names,
            "input_view_tokens_total": sum_stage_tokens(executed_stage_names, "input_view_tokens"),
            "output_tokens_total": sum_stage_tokens(executed_stage_names, "output_tokens"),
            "prompt_tokens_total": sum_stage_tokens(executed_stage_names, "prompt_tokens"),
            "prefix_input_view_tokens": sum_stage_tokens(stage_groups.prefix_stages, "input_view_tokens"),
            "suffix_input_view_tokens": sum_stage_tokens(stage_groups.suffix_stages, "input_view_tokens"),
            "anchor_input_view_tokens": sum_stage_tokens(stage_groups.anchor_stages, "input_view_tokens"),
            "prefix_prompt_tokens": sum_stage_tokens(stage_groups.prefix_stages, "prompt_tokens"),
            "suffix_prompt_tokens": sum_stage_tokens(stage_groups.suffix_stages, "prompt_tokens"),
            "anchor_prompt_tokens": sum_stage_tokens(stage_groups.anchor_stages, "prompt_tokens"),
        }

    def _is_missing(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

    def _resolve_path(self, payload: Dict[str, Any], path: str) -> Any:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def _has_path(self, payload: Dict[str, Any], path: str) -> bool:
        current: Any = payload
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    def _split_args(self, expression: str) -> List[str]:
        args: List[str] = []
        current: List[str] = []
        depth = 0
        for char in expression:
            if char == "," and depth == 0:
                args.append("".join(current).strip())
                current = []
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            current.append(char)
        if current:
            args.append("".join(current).strip())
        return args

    def _evaluate_expression(self, raw_task: TaskPayload, expression: str) -> Any:
        expr = expression.strip()
        if expr == "[]":
            return []
        if expr.startswith("listify(") and expr.endswith(")"):
            inner = expr[len("listify(") : -1]
            value = self._evaluate_expression(raw_task, inner)
            if value is None:
                return []
            if isinstance(value, list):
                return value
            if isinstance(value, tuple):
                return list(value)
            if isinstance(value, set):
                return sorted(value)
            if isinstance(value, dict):
                return [value]
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return []
                if "\n" in stripped:
                    return [item.strip() for item in stripped.splitlines() if item.strip()]
                if "," in stripped:
                    return [item.strip() for item in stripped.split(",") if item.strip()]
                return [stripped]
            return [value]
        if expr.startswith("concat(") and expr.endswith(")"):
            inner = expr[len("concat(") : -1]
            values = [self._evaluate_expression(raw_task, arg) for arg in self._split_args(inner)]
            parts = [str(value) for value in values if value not in (None, "", [])]
            return "\n\n".join(parts)
        if expr.startswith("coalesce(") and expr.endswith(")"):
            inner = expr[len("coalesce(") : -1]
            for arg in self._split_args(inner):
                value = self._evaluate_expression(raw_task, arg)
                if value not in (None, "", []):
                    return value
            return None
        if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in {"'", '"'}:
            return expr[1:-1]
        return self._resolve_path(raw_task, expr)

    def _normalize_with_profile(
        self,
        manifest: SkillManifest,
        profile: InputProfile,
        raw_task: TaskPayload,
    ) -> tuple[TaskPayload, List[str]]:
        normalized = {
            field: self._evaluate_expression(raw_task, expression)
            for field, expression in profile.field_map.items()
        }
        missing = [
            field
            for field in manifest.canonical_task_fields
            if self._is_missing(normalized.get(field))
        ]
        return normalized, missing

    def _match_profile(self, manifest: SkillManifest, raw_task: TaskPayload) -> tuple[InputProfile, TaskPayload]:
        candidates: List[tuple[int, int, int, int, InputProfile, TaskPayload]] = []
        for index, profile in enumerate(manifest.input_profiles):
            present_matches = sum(1 for field in profile.match_fields if self._has_path(raw_task, field))
            missing_matches = len(profile.match_fields) - present_matches
            normalized, missing = self._normalize_with_profile(manifest, profile, raw_task)
            if missing:
                continue
            populated_count = sum(
                1 for field in manifest.canonical_task_fields if not self._is_missing(normalized.get(field))
            )
            candidates.append(
                (present_matches, -missing_matches, populated_count, -index, profile, normalized)
            )

        if not candidates:
            available = sorted(raw_task.keys())
            raise ValueError(
                f"No input profile could normalize task keys {available}. "
                f"Available profiles: {[profile.name for profile in manifest.input_profiles]}"
            )

        _, _, _, _, chosen_profile, chosen_task = max(candidates, key=lambda item: item[:4])
        return chosen_profile, chosen_task

    def _normalize_task(
        self,
        manifest: SkillManifest,
        raw_task: TaskPayload,
    ) -> tuple[InputProfile, TaskPayload]:
        return self._match_profile(manifest, raw_task)

    def _build_stage_task_view(self, stage: StageDefinition, task: TaskPayload) -> Dict[str, Any]:
        return {field: task.get(field) for field in stage.reads.task}

    def _build_stage_state_view(self, stage: StageDefinition, state: Dict[str, Any]) -> Dict[str, Any]:
        return {field: state.get(field) for field in stage.reads.state}

    def _build_stage_artifact_view(
        self,
        stage: StageDefinition,
        artifacts: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {field: artifacts.get(field) for field in stage.reads.artifacts}

    def _build_stage_artifact_read_provenance(
        self,
        stage: StageDefinition,
        artifact_provenance: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            artifact_name: dict(artifact_provenance.get(artifact_name, {}))
            if artifact_name in artifact_provenance
            else None
            for artifact_name in stage.reads.artifacts
        }

    def _build_stage_toolbox(self, stage: StageDefinition) -> Dict[str, Any]:
        toolbox = {tool_name: self._tools[tool_name] for tool_name in stage.uses_tools}
        toolbox["__llm_backend"] = self._llm_backend
        return toolbox

    def _build_stage_groups(self, manifest: SkillManifest) -> WorkflowStageGroups:
        stage_order = [stage.name for stage in manifest.stages]
        anchor_stages = [stage.name for stage in manifest.stages if stage.metadata.anchor_stage]
        optional_stages = [stage.name for stage in manifest.stages if stage.metadata.optional_stage]
        stage_type_groups: Dict[str, List[str]] = {}
        workflow_phase_groups: Dict[str, List[str]] = {}
        for stage in manifest.stages:
            stage_type_groups.setdefault(stage.metadata.stage_type, []).append(stage.name)
            workflow_phase_groups.setdefault(stage.metadata.workflow_phase, []).append(stage.name)

        if anchor_stages:
            last_anchor_index = max(
                index
                for index, stage in enumerate(manifest.stages)
                if stage.metadata.anchor_stage
            )
            prefix_stages = stage_order[: last_anchor_index + 1]
            suffix_stages = stage_order[last_anchor_index + 1 :]
            anchor_boundary_stage = stage_order[last_anchor_index]
        else:
            prefix_stages = []
            suffix_stages = list(stage_order)
            anchor_boundary_stage = None

        return WorkflowStageGroups(
            stage_order=stage_order,
            prefix_stages=prefix_stages,
            anchor_stages=anchor_stages,
            suffix_stages=suffix_stages,
            optional_stages=optional_stages,
            stage_type_groups=stage_type_groups,
            workflow_phase_groups=workflow_phase_groups,
            anchor_boundary_stage=anchor_boundary_stage,
        )

    def _stage_groups_to_dict(self, stage_groups: WorkflowStageGroups) -> Dict[str, Any]:
        return {
            "stage_order": list(stage_groups.stage_order),
            "prefix_stages": list(stage_groups.prefix_stages),
            "anchor_stages": list(stage_groups.anchor_stages),
            "suffix_stages": list(stage_groups.suffix_stages),
            "optional_stages": list(stage_groups.optional_stages),
            "stage_type_groups": {
                key: list(value) for key, value in stage_groups.stage_type_groups.items()
            },
            "workflow_phase_groups": {
                key: list(value) for key, value in stage_groups.workflow_phase_groups.items()
            },
            "anchor_boundary_stage": stage_groups.anchor_boundary_stage,
        }

    def _estimated_placeholder_value(self, type_spec: str) -> Any:
        normalized = type_spec.strip()
        if normalized == "string":
            return "<estimated>"
        if normalized == "integer":
            return 0
        if normalized == "number":
            return 0
        if normalized == "boolean":
            return False
        if normalized.startswith("enum[") and normalized.endswith("]"):
            options = [item.strip() for item in normalized[len("enum[") : -1].split(",") if item.strip()]
            return options[0] if options else ""
        if normalized.startswith("list["):
            return []
        return None

    def _estimated_output_from_schema(
        self,
        manifest: SkillManifest,
        stage: StageDefinition,
    ) -> Dict[str, Any]:
        if not stage.output_schema:
            return {field_name: "<estimated>" for field_name in stage.writes}
        output_schema = manifest.output_schemas[stage.output_schema]
        return {
            field_name: self._estimated_placeholder_value(field_def.type_spec)
            for field_name, field_def in output_schema.fields.items()
        }

    def _estimated_tool_artifacts(
        self,
        stage: StageDefinition,
        task_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not stage.writes_artifacts:
            return {}
        source_lists = [value for value in artifact_view.values() if isinstance(value, list)]
        if not source_lists:
            source_lists = [value for value in task_view.values() if isinstance(value, list)]
        if source_lists:
            seed_items = list(source_lists[0])[:4]
        else:
            seed_items = []
        return {
            artifact_name: list(seed_items)
            for artifact_name in stage.writes_artifacts
        }

    def _estimate_optional_branch_prompt_cost(
        self,
        manifest: SkillManifest,
        stage_index: int,
        canonical_task: Dict[str, Any],
        state: Dict[str, Any],
        artifacts: Dict[str, Any],
    ) -> tuple[int, int, int, List[str]]:
        if not manifest.stages[stage_index].metadata.optional_stage:
            return 0, 0, 0, []

        runtime_estimated_prompt_tokens = 0
        metadata_prompt_cost_hint = 0
        branch_stage_names: List[str] = []
        virtual_state = dict(state)
        virtual_artifacts = dict(artifacts)

        for candidate_stage in manifest.stages[stage_index:]:
            if not candidate_stage.metadata.optional_stage:
                break
            branch_stage_names.append(candidate_stage.name)
            task_view = self._build_stage_task_view(candidate_stage, canonical_task)
            state_view = self._build_stage_state_view(candidate_stage, virtual_state)
            artifact_view = self._build_stage_artifact_view(candidate_stage, virtual_artifacts)
            if candidate_stage.executor == "llm.json_stage" and candidate_stage.output_schema:
                prompt_text = render_json_stage_prompt(
                    stage=candidate_stage,
                    instruction=candidate_stage.instruction,
                    task_view=task_view,
                    state_view=state_view,
                    artifact_view=artifact_view,
                    output_schema=manifest.output_schemas[candidate_stage.output_schema],
                    prompt_header="You are executing one workflow stage on a local model.",
                )
                runtime_estimated_prompt_tokens += self._count_tokens(prompt_text)

            metadata_prompt_cost_hint += candidate_stage.metadata.prompt_cost_hint

            estimated_state_output = self._estimated_output_from_schema(
                manifest,
                candidate_stage,
            )
            virtual_state.update(estimated_state_output)

            if candidate_stage.executor == "tool.retrieve_artifacts":
                virtual_artifacts.update(
                    self._estimated_tool_artifacts(
                        candidate_stage,
                        task_view,
                        artifact_view,
                    )
                )
            elif candidate_stage.writes_artifacts:
                for artifact_name in candidate_stage.writes_artifacts:
                    virtual_artifacts[artifact_name] = []

        combined_prompt_estimate = max(
            runtime_estimated_prompt_tokens,
            metadata_prompt_cost_hint,
        )
        return (
            runtime_estimated_prompt_tokens,
            metadata_prompt_cost_hint,
            combined_prompt_estimate,
            branch_stage_names,
        )

    def _build_stage_snapshot(
        self,
        stage: StageDefinition,
        stage_index: int,
        manifest: SkillManifest,
        state: Dict[str, Any],
        artifacts: Dict[str, Any],
        canonical_task: Dict[str, Any],
        consumed_prompt_tokens: int,
        consumed_input_view_tokens: int,
        consumed_output_tokens: int,
    ) -> StageRuntimeSnapshot:
        (
            estimated_optional_branch_prompt_tokens_runtime,
            estimated_optional_branch_prompt_tokens_metadata,
            estimated_optional_branch_prompt_tokens,
            optional_branch_stage_names,
        ) = (
            self._estimate_optional_branch_prompt_cost(
                manifest,
                stage_index,
                canonical_task,
                state,
                artifacts,
            )
        )
        return StageRuntimeSnapshot(
            stage_name=stage.name,
            stage_index=stage_index,
            total_stages=len(manifest.stages),
            metadata=stage.metadata,
            completed_stages=[prior_stage.name for prior_stage in manifest.stages[:stage_index]],
            remaining_stages=[later_stage.name for later_stage in manifest.stages[stage_index + 1 :]],
            available_state_keys=sorted(state.keys()),
            available_artifact_keys=sorted(artifacts.keys()),
            reads_state_fields=list(stage.reads.state),
            reads_artifact_fields=list(stage.reads.artifacts),
            state_values=dict(state),
            consumed_prompt_tokens=consumed_prompt_tokens,
            consumed_input_view_tokens=consumed_input_view_tokens,
            consumed_output_tokens=consumed_output_tokens,
            estimated_optional_branch_prompt_tokens_runtime=estimated_optional_branch_prompt_tokens_runtime,
            estimated_optional_branch_prompt_tokens_metadata=estimated_optional_branch_prompt_tokens_metadata,
            estimated_optional_branch_prompt_tokens=estimated_optional_branch_prompt_tokens,
            optional_branch_stage_names=optional_branch_stage_names,
        )

    def _validate_typed_value(self, value: Any, type_spec: str) -> bool:
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

    def _validate_state_output_against_schema(
        self,
        stage: StageDefinition,
        state_output: Dict[str, Any],
        output_schema: OutputSchemaDefinition,
    ) -> None:
        for field_name, field_def in output_schema.fields.items():
            if field_name not in state_output:
                raise ValueError(
                    f"Stage {stage.name} did not produce schema field {field_name}."
                )
            if not self._validate_typed_value(state_output[field_name], field_def.type_spec):
                value = state_output[field_name]
                raise ValueError(
                    f"Stage {stage.name} produced invalid value for {field_name}: {value!r} "
                    f"does not match {field_def.type_spec}."
                )

    def _execute_stage(
        self,
        manifest: SkillManifest,
        stage: StageDefinition,
        task_view: Dict[str, Any],
        state_view: Dict[str, Any],
        artifact_view: Dict[str, Any],
        toolbox: Dict[str, Any],
        executor_name: str | None = None,
    ) -> Dict[str, Any]:
        executor_key = executor_name or stage.executor
        executor = self._executors.get(executor_key)
        if executor is None:
            raise ValueError(f"Unknown executor: {executor_key}")
        output = executor(stage, task_view, state_view, artifact_view, toolbox)
        state_output = output.get("state", {})
        artifact_output = output.get("artifacts", {})
        missing = [field for field in stage.writes if field not in state_output]
        if missing:
            available = sorted(state_output.keys()) if isinstance(state_output, dict) else [type(state_output).__name__]
            raise ValueError(
                f"Executor {executor_key} did not produce required fields: {missing}. "
                f"Available top-level fields: {available}"
            )
        if stage.output_schema:
            output_schema = manifest.output_schemas[stage.output_schema]
            self._validate_state_output_against_schema(stage, state_output, output_schema)
        missing_artifacts = [
            field for field in stage.writes_artifacts if field not in artifact_output
        ]
        if missing_artifacts:
            raise ValueError(
                f"Executor {executor_key} did not produce required artifacts: {missing_artifacts}"
            )
        return output

    def run(self, manifest: SkillManifest, task: TaskPayload) -> Dict[str, Any]:
        profile, canonical_task = self._normalize_task(manifest, task)
        stage_groups = self._strategy.plan_workflow(
            manifest.name,
            canonical_task,
            self._build_stage_groups(manifest),
            manifest.constraints,
        )

        state: Dict[str, Any] = {}
        artifacts: Dict[str, Any] = {}
        state_provenance: Dict[str, Dict[str, Any]] = {}
        artifact_provenance: Dict[str, Dict[str, Any]] = {}
        stages: List[Dict[str, Any]] = []
        consumed_prompt_tokens = 0
        consumed_input_view_tokens = 0
        consumed_output_tokens = 0

        for stage_index, stage in enumerate(manifest.stages):
            snapshot = self._build_stage_snapshot(
                stage,
                stage_index,
                manifest,
                state,
                artifacts,
                canonical_task,
                consumed_prompt_tokens,
                consumed_input_view_tokens,
                consumed_output_tokens,
            )
            decision = self._strategy.decide_stage(
                manifest.name,
                stage.name,
                snapshot,
                stage_groups,
                manifest.constraints,
            )
            if not decision.execute_stage:
                stages.append(
                    {
                        "name": stage.name,
                        "executor": stage.executor,
                        "metadata": {
                            "stage_type": stage.metadata.stage_type,
                            "workflow_phase": stage.metadata.workflow_phase,
                            "anchor_stage": stage.metadata.anchor_stage,
                            "criticality": stage.metadata.criticality,
                            "optional_stage": stage.metadata.optional_stage,
                            "tool_mode": stage.metadata.tool_mode,
                            "tool_intent": stage.metadata.tool_intent,
                            "post_tool_policy": stage.metadata.post_tool_policy,
                            "prompt_cost_hint": stage.metadata.prompt_cost_hint,
                        },
                        "decision": {
                            "execute_stage": decision.execute_stage,
                            "executor_override": decision.executor_override,
                            "reason": decision.reason,
                            "notes": list(decision.notes),
                        },
                        "strategy_context": {
                            "consumed_prompt_tokens_before_stage": snapshot.consumed_prompt_tokens,
                            "estimated_optional_branch_prompt_tokens_runtime": (
                                snapshot.estimated_optional_branch_prompt_tokens_runtime
                            ),
                            "estimated_optional_branch_prompt_tokens_metadata": (
                                snapshot.estimated_optional_branch_prompt_tokens_metadata
                            ),
                            "estimated_optional_branch_prompt_tokens": snapshot.estimated_optional_branch_prompt_tokens,
                            "optional_branch_stage_names": list(snapshot.optional_branch_stage_names),
                        },
                        "token_usage": {
                            "task_view_tokens": 0,
                            "state_view_tokens": 0,
                            "artifact_view_tokens": 0,
                            "state_output_tokens": 0,
                            "artifact_output_tokens": 0,
                            "input_view_tokens": 0,
                            "output_tokens": 0,
                            "prompt_tokens": None,
                        },
                        "skipped": True,
                    }
                )
                continue
            task_view = self._build_stage_task_view(stage, canonical_task)
            state_view = self._build_stage_state_view(stage, state)
            artifact_view = self._build_stage_artifact_view(stage, artifacts)
            artifact_read_provenance = self._build_stage_artifact_read_provenance(stage, artifact_provenance)
            toolbox = self._build_stage_toolbox(stage)
            if stage.output_schema:
                toolbox["__output_schema"] = manifest.output_schemas[stage.output_schema]
            output = self._execute_stage(
                manifest,
                stage,
                task_view,
                state_view,
                artifact_view,
                toolbox,
                executor_name=decision.executor_override,
            )
            state_output = output.get("state", {})
            artifact_output = output.get("artifacts", {})
            effective_executor = decision.executor_override or stage.executor
            token_usage = self._estimate_stage_token_usage(
                stage=stage,
                executor_name=effective_executor,
                task_view=task_view,
                state_view=state_view,
                artifact_view=artifact_view,
                state_output=state_output,
                artifact_output=artifact_output,
                instruction=stage.instruction,
                output_schema=manifest.output_schemas.get(stage.output_schema) if stage.output_schema else None,
            )
            for field_name in state_output:
                state_provenance[field_name] = {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                    "stage_type": stage.metadata.stage_type,
                    "output_schema": stage.output_schema or None,
                }
            for artifact_name in artifact_output:
                artifact_provenance[artifact_name] = {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                    "stage_type": stage.metadata.stage_type,
                }
            state.update(state_output)
            artifacts.update(artifact_output)
            prompt_tokens = token_usage.get("prompt_tokens")
            if isinstance(prompt_tokens, int):
                consumed_prompt_tokens += prompt_tokens
            input_view_tokens = token_usage.get("input_view_tokens")
            if isinstance(input_view_tokens, int):
                consumed_input_view_tokens += input_view_tokens
            output_tokens = token_usage.get("output_tokens")
            if isinstance(output_tokens, int):
                consumed_output_tokens += output_tokens
            stages.append(
                {
                    "name": stage.name,
                    "executor": effective_executor,
                    "metadata": {
                        "stage_type": stage.metadata.stage_type,
                        "workflow_phase": stage.metadata.workflow_phase,
                        "anchor_stage": stage.metadata.anchor_stage,
                        "criticality": stage.metadata.criticality,
                        "optional_stage": stage.metadata.optional_stage,
                        "tool_mode": stage.metadata.tool_mode,
                        "tool_intent": stage.metadata.tool_intent,
                        "post_tool_policy": stage.metadata.post_tool_policy,
                        "prompt_cost_hint": stage.metadata.prompt_cost_hint,
                    },
                    "decision": {
                        "execute_stage": decision.execute_stage,
                        "executor_override": decision.executor_override,
                        "reason": decision.reason,
                        "notes": list(decision.notes),
                    },
                    "strategy_context": {
                        "consumed_prompt_tokens_before_stage": snapshot.consumed_prompt_tokens,
                        "estimated_optional_branch_prompt_tokens_runtime": (
                            snapshot.estimated_optional_branch_prompt_tokens_runtime
                        ),
                        "estimated_optional_branch_prompt_tokens_metadata": (
                            snapshot.estimated_optional_branch_prompt_tokens_metadata
                        ),
                        "estimated_optional_branch_prompt_tokens": snapshot.estimated_optional_branch_prompt_tokens,
                        "optional_branch_stage_names": list(snapshot.optional_branch_stage_names),
                    },
                    "instruction": stage.instruction,
                    "task_view": task_view,
                    "state_view": state_view,
                    "artifact_view": artifact_view,
                    "artifact_read_provenance": artifact_read_provenance,
                    "tools_used": list(toolbox.keys()),
                    "token_usage": token_usage,
                    "output": output,
                }
            )

        backend_info: Dict[str, Any] | None = None
        describe_backend = getattr(self._llm_backend, "describe_runtime", None)
        if callable(describe_backend):
            backend_info = describe_backend()

        return {
            "skill": manifest.name,
            "skill_name": manifest.name,
            "description": manifest.description,
            "strategy": self._strategy.name,
            "strategy_config": self._strategy.describe(),
            "input_profile": profile.name,
            "constraints": [
                {"name": constraint.name, "description": constraint.description}
                for constraint in manifest.constraints
            ],
            "static_lint": manifest.static_lint,
            "raw_task": task,
            "canonical_task": canonical_task,
            "task_id": canonical_task.get("task_id"),
            "stage_groups": self._stage_groups_to_dict(stage_groups),
            "stages": stages,
            "token_summary": self._summarize_token_usage(stage_groups, stages, canonical_task),
            "llm_backend": backend_info,
            "state_provenance": state_provenance,
            "artifact_provenance": artifact_provenance,
            "final_artifacts": artifacts,
            "final_state": state,
        }
