from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .models import (
    ArtifactDefinition,
    ConstraintDefinition,
    InputProfile,
    OutputFieldDefinition,
    OutputSchemaDefinition,
    SkillManifest,
    StageDefinition,
    StageMetadata,
    StageReads,
    ToolDefinition,
)

VALID_STAGE_TYPES = {
    "intake",
    "evidence",
    "retrieval",
    "compression",
    "decision",
    "plan",
    "review",
    "response",
}
VALID_WORKFLOW_PHASES = {
    "observe",
    "abstract",
    "route",
    "execute",
    "verify",
    "handoff",
}
WORKFLOW_PHASE_ORDER = {
    "observe": 0,
    "abstract": 1,
    "route": 2,
    "execute": 3,
    "verify": 4,
    "handoff": 5,
}
VALID_CRITICALITY = {"high", "medium", "low"}
VALID_TOOL_MODES = {"none", "read_only", "side_effecting"}
VALID_TOOL_INTENTS = {"none", "retrieval", "re_retrieval", "lookup", "side_effect"}
VALID_POST_TOOL_POLICIES = {"none", "requires_compression", "direct_state"}
CONTROL_FAMILIES = {"blocker", "path", "decision", "action"}
PHASE_ALLOWED_CONTROL_FAMILIES = {
    "observe": {"blocker", "path"},
    "abstract": {"blocker", "path"},
    "route": {"path", "decision"},
    "execute": {"path", "decision", "action"},
    "verify": {"blocker", "decision", "action"},
    "handoff": {"action"},
}
HEAVY_RAW_TASK_FIELD_KEYWORDS = {
    "message",
    "context",
    "description",
    "body",
    "excerpt",
    "artifact",
    "history",
    "source",
    "stacktrace",
    "trace",
    "transcript",
    "conversation",
    "payload",
    "pack",
    "log",
}
ALLOW_SUFFIX_RAW_TASK_READ_CONSTRAINT = "allow_suffix_raw_task_reads"
HEAVY_ARTIFACT_KEYWORDS = {
    "retrieved",
    "raw",
    "full",
    "search",
    "repo",
    "context",
    "history",
    "transcript",
    "source",
    "evidence",
    "artifact",
    "payload",
    "pack",
    "log",
    "trace",
    "snippet",
}
ALLOW_SUFFIX_HEAVY_ARTIFACT_READS_CONSTRAINT = "allow_suffix_heavy_artifact_reads"
ALLOW_SUFFIX_READ_ONLY_TOOL_CALLS_CONSTRAINT = "allow_suffix_read_only_tool_calls"


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    if not text.startswith("---\n"):
        raise ValueError("Skill file must begin with YAML frontmatter.")
    _, remainder = text.split("---\n", 1)
    frontmatter_block, body = remainder.split("\n---\n", 1)
    metadata: Dict[str, str] = {}
    for line in frontmatter_block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise ValueError(f"Invalid frontmatter line: {line}")
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = _strip_wrapping_quotes(value.strip())
    return metadata, body


def _parse_list_value(value: str) -> List[str]:
    normalized = value.strip()
    if not normalized or normalized.lower() == "none":
        return []
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _parse_bool_value(value: str, *, field_name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0", ""}:
        return False
    raise ValueError(f"Invalid boolean value for {field_name}: {value}")


def _parse_non_negative_int(value: str, *, field_name: str) -> int:
    normalized = value.strip()
    if not normalized:
        return 0
    parsed = int(normalized)
    if parsed < 0:
        raise ValueError(f"Invalid non-negative integer value for {field_name}: {value}")
    return parsed


def _normalize_identifier(value: str) -> str:
    return value.strip().lower()


def _looks_like_heavy_raw_task_field(field_name: str) -> bool:
    normalized = _normalize_identifier(field_name)
    if normalized.endswith("_id") or normalized in {"task_id", "title", "repo_subpaths"}:
        return False
    return any(keyword in normalized for keyword in HEAVY_RAW_TASK_FIELD_KEYWORDS)


def _infer_control_family(field_name: str) -> str:
    normalized = _normalize_identifier(field_name)

    if normalized.startswith(("candidate_", "possible_", "suggested_")) and (
        "action" in normalized or "step" in normalized
    ):
        return "path"

    if any(
        token in normalized
        for token in {"blocker", "blocking", "missing", "requirement", "risk", "guardrail", "gap"}
    ):
        return "blocker"

    if normalized in {
        "primary_file",
        "root_cause_category",
        "resolution_category",
        "decision_status",
        "handoff_status",
    }:
        return "decision"
    if any(
        token in normalized
        for token in {"decision", "verdict", "eligibility", "approval", "denial", "category", "status"}
    ):
        return "decision"

    if any(
        token in normalized
        for token in {
            "path",
            "route",
            "branch",
            "candidate",
            "hypoth",
            "query",
            "hint",
            "signal",
            "factor",
            "label",
            "module",
        }
    ):
        return "path"

    if normalized in {"fix_plan", "response_message"}:
        return "action"
    if any(token in normalized for token in {"plan", "steps", "response", "draft", "action"}):
        return "action"

    return "neutral"


def _looks_like_heavy_artifact(artifact_name: str, description: str = "") -> bool:
    normalized = _normalize_identifier(f"{artifact_name} {description}")
    return any(keyword in normalized for keyword in HEAVY_ARTIFACT_KEYWORDS)


def _is_artifact_compression_boundary(
    consumer_stage: StageDefinition,
    artifact_name: str,
) -> bool:
    if artifact_name not in consumer_stage.reads.artifacts or not consumer_stage.writes:
        return False
    if consumer_stage.metadata.stage_type == "compression":
        return True
    if consumer_stage.metadata.workflow_phase in {"observe", "abstract"}:
        return True
    return False


def _parse_simple_list_section(lines: List[str], heading: str) -> List[str]:
    items: List[str] = []
    capture = False
    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            capture = True
            continue
        if capture and stripped.startswith("## "):
            break
        if capture and stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def _parse_input_profiles(lines: List[str]) -> List[InputProfile]:
    profiles: List[InputProfile] = []
    in_profiles = False
    current_name: str | None = None
    current_meta: Dict[str, str] = {}
    current_description: List[str] = []

    def flush() -> None:
        nonlocal current_name, current_meta, current_description
        if current_name is None:
            return
        profiles.append(
            InputProfile(
                name=current_name,
                match_fields=_parse_list_value(current_meta.get("match", "")),
                field_map={
                    key[len("map."):]: value
                    for key, value in current_meta.items()
                    if key.startswith("map.")
                },
                description="\n".join(line for line in current_description if line).strip(),
            )
        )
        current_name = None
        current_meta = {}
        current_description = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "## Input Profiles":
            in_profiles = True
            continue
        if in_profiles and stripped.startswith("## "):
            flush()
            break
        if not in_profiles:
            continue
        if stripped.startswith("### "):
            flush()
            current_name = stripped[4:].strip()
            continue
        if current_name is None:
            continue
        if stripped.startswith("- ") and ":" in stripped:
            key, value = stripped[2:].split(":", 1)
            current_meta[key.strip()] = value.strip()
            continue
        current_description.append(stripped)

    flush()
    return profiles


def _parse_named_section(
    lines: List[str],
    section_heading: str,
    item_cls: type[ToolDefinition] | type[ArtifactDefinition],
) -> List[ToolDefinition] | List[ArtifactDefinition]:
    items: List[ToolDefinition] | List[ArtifactDefinition] = []
    in_section = False
    current_name: str | None = None
    current_meta: Dict[str, str] = {}
    current_description: List[str] = []

    def flush() -> None:
        nonlocal current_name, current_meta, current_description
        if current_name is None:
            return
        description = "\n".join(line for line in current_description if line).strip()
        if item_cls is ToolDefinition:
            kind = current_meta.get("kind")
            if not kind:
                raise ValueError(f"Tool {current_name} is missing kind.")
            items.append(ToolDefinition(name=current_name, kind=kind, description=description))
        else:
            items.append(ArtifactDefinition(name=current_name, description=description))
        current_name = None
        current_meta = {}
        current_description = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == section_heading:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            flush()
            break
        if not in_section:
            continue
        if stripped.startswith("### "):
            flush()
            current_name = stripped[4:].strip()
            continue
        if current_name is None:
            continue
        if stripped.startswith("- ") and ":" in stripped:
            key, value = stripped[2:].split(":", 1)
            current_meta[key.strip()] = value.strip()
            continue
        current_description.append(stripped)

    flush()
    return items


def _parse_constraints(lines: List[str]) -> List[ConstraintDefinition]:
    constraints: List[ConstraintDefinition] = []
    in_section = False
    current_name: str | None = None
    current_description: List[str] = []

    def flush() -> None:
        nonlocal current_name, current_description
        if current_name is None:
            return
        constraints.append(
            ConstraintDefinition(
                name=current_name,
                description="\n".join(line for line in current_description if line).strip(),
            )
        )
        current_name = None
        current_description = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "## Constraints":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            flush()
            break
        if not in_section:
            continue
        if stripped.startswith("### "):
            flush()
            current_name = stripped[4:].strip()
            continue
        if current_name is None:
            continue
        current_description.append(stripped)

    flush()
    return constraints


def _parse_output_schemas(lines: List[str]) -> Dict[str, OutputSchemaDefinition]:
    schemas: Dict[str, OutputSchemaDefinition] = {}
    in_section = False
    current_name: str | None = None
    current_fields: Dict[str, OutputFieldDefinition] = {}
    current_description: List[str] = []

    def flush() -> None:
        nonlocal current_name, current_fields, current_description
        if current_name is None:
            return
        schemas[current_name] = OutputSchemaDefinition(
            name=current_name,
            fields=dict(current_fields),
            description="\n".join(line for line in current_description if line).strip(),
        )
        current_name = None
        current_fields = {}
        current_description = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "## Output Schemas":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            flush()
            break
        if not in_section:
            continue
        if stripped.startswith("### "):
            flush()
            current_name = stripped[4:].strip()
            continue
        if current_name is None:
            continue
        if stripped.startswith("- ") and ":" in stripped:
            field_name, value = stripped[2:].split(":", 1)
            type_spec = value.strip()
            current_fields[field_name.strip()] = OutputFieldDefinition(
                name=field_name.strip(),
                type_spec=type_spec,
            )
            continue
        current_description.append(stripped)

    flush()
    return schemas


def _parse_workflow_stages(lines: List[str]) -> List[StageDefinition]:
    stages: List[StageDefinition] = []
    in_workflow = False
    current_name: str | None = None
    current_meta: Dict[str, str] = {}
    current_instruction: List[str] = []

    def flush() -> None:
        nonlocal current_name, current_meta, current_instruction
        if current_name is None:
            return
        executor = current_meta.get("executor")
        if not executor:
            raise ValueError(f"Stage {current_name} is missing executor.")
        writes = _parse_list_value(current_meta.get("writes", ""))
        if not writes:
            raise ValueError(f"Stage {current_name} is missing writes.")
        stages.append(
            StageDefinition(
                name=current_name,
                executor=executor,
                reads=StageReads(
                    task=_parse_list_value(current_meta.get("reads.task", "")),
                    state=_parse_list_value(current_meta.get("reads.state", "")),
                    artifacts=_parse_list_value(current_meta.get("reads.artifacts", "")),
                ),
                writes=writes,
                metadata=StageMetadata(
                    stage_type=current_meta.get("stage_type", "").strip(),
                    workflow_phase=current_meta.get("workflow_phase", "").strip(),
                    anchor_stage=_parse_bool_value(
                        current_meta.get("anchor_stage", "false"),
                        field_name=f"{current_name}.anchor_stage",
                    ),
                    criticality=current_meta.get("criticality", "medium").strip(),
                    optional_stage=_parse_bool_value(
                        current_meta.get("optional_stage", "false"),
                        field_name=f"{current_name}.optional_stage",
                    ),
                    tool_mode=current_meta.get("tool_mode", "").strip()
                    or ("read_only" if current_meta.get("uses.tools", "").strip() else "none"),
                    tool_intent=current_meta.get("tool_intent", "none").strip() or "none",
                    post_tool_policy=current_meta.get("post_tool_policy", "none").strip() or "none",
                    prompt_cost_hint=_parse_non_negative_int(
                        current_meta.get("prompt_cost_hint", "0"),
                        field_name=f"{current_name}.prompt_cost_hint",
                    ),
                ),
                output_schema=current_meta.get("output_schema", "").strip(),
                uses_tools=_parse_list_value(current_meta.get("uses.tools", "")),
                writes_artifacts=_parse_list_value(current_meta.get("writes.artifacts", "")),
                instruction="\n".join(line for line in current_instruction if line).strip(),
            )
        )
        current_name = None
        current_meta = {}
        current_instruction = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "## Workflow":
            in_workflow = True
            continue
        if in_workflow and stripped.startswith("## "):
            flush()
            break
        if not in_workflow:
            continue
        if stripped.startswith("### "):
            flush()
            current_name = stripped[4:].strip()
            continue
        if current_name is None:
            continue
        if stripped.startswith("- ") and ":" in stripped:
            key, value = stripped[2:].split(":", 1)
            current_meta[key.strip()] = value.strip()
            continue
        current_instruction.append(stripped)

    flush()
    return stages


def _build_static_lint_report(manifest: SkillManifest) -> Dict[str, object]:
    constraint_names = {constraint.name for constraint in manifest.constraints}
    heavy_artifacts = {
        artifact.name: artifact
        for artifact in manifest.artifacts
        if _looks_like_heavy_artifact(artifact.name, artifact.description)
    }
    suffix_phases = {"route", "execute", "verify", "handoff"}
    stage_indices = {stage.name: index for index, stage in enumerate(manifest.stages)}

    control_family_summary = []
    suffix_raw_task_reads = []
    suffix_heavy_artifact_reads = []
    suffix_read_only_tool_stages = []
    anchor_answer_like_stages = []
    mixed_control_family_stages = []
    heavy_tool_artifact_boundaries = []
    tool_contracts = []
    suffix_retrieval_subloops = []
    artifact_producers: Dict[str, List[Dict[str, str]]] = {
        artifact.name: [] for artifact in manifest.artifacts
    }
    artifact_consumers: Dict[str, List[Dict[str, str]]] = {
        artifact.name: [] for artifact in manifest.artifacts
    }
    suffix_artifact_lineage_reads = []

    for stage in manifest.stages:
        control_families = sorted(
            {
                family
                for family in (_infer_control_family(field_name) for field_name in stage.writes)
                if family in CONTROL_FAMILIES
            }
        )
        control_family_summary.append(
            {
                "stage": stage.name,
                "workflow_phase": stage.metadata.workflow_phase,
                "control_families": control_families,
            }
        )
        if stage.metadata.anchor_stage and set(control_families).intersection({"decision", "action"}):
            anchor_answer_like_stages.append(stage.name)
        if len(control_families) > 2:
            mixed_control_family_stages.append(
                {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                    "control_families": control_families,
                }
            )
        if stage.uses_tools:
            tool_contracts.append(
                {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                    "tool_mode": stage.metadata.tool_mode,
                    "tool_intent": stage.metadata.tool_intent,
                    "post_tool_policy": stage.metadata.post_tool_policy,
                    "tools": list(stage.uses_tools),
                    "writes_artifacts": list(stage.writes_artifacts),
                }
            )

        heavy_raw_task_reads = [
            field for field in stage.reads.task if _looks_like_heavy_raw_task_field(field)
        ]
        if stage.metadata.workflow_phase in suffix_phases and heavy_raw_task_reads:
            suffix_raw_task_reads.append(
                {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                    "fields": heavy_raw_task_reads,
                    "allowed_by_constraint": ALLOW_SUFFIX_RAW_TASK_READ_CONSTRAINT in constraint_names,
                }
            )

        heavy_artifact_reads = [
            artifact_name for artifact_name in stage.reads.artifacts if artifact_name in heavy_artifacts
        ]
        if stage.metadata.workflow_phase in suffix_phases and heavy_artifact_reads:
            suffix_heavy_artifact_reads.append(
                {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                    "artifacts": heavy_artifact_reads,
                    "allowed_by_constraint": ALLOW_SUFFIX_HEAVY_ARTIFACT_READS_CONSTRAINT in constraint_names,
                }
            )

        if stage.metadata.workflow_phase in suffix_phases and stage.metadata.tool_mode == "read_only" and stage.uses_tools:
            suffix_read_only_tool_stages.append(
                {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                    "tools": list(stage.uses_tools),
                    "tool_intent": stage.metadata.tool_intent,
                    "post_tool_policy": stage.metadata.post_tool_policy,
                    "allowed_by_constraint": ALLOW_SUFFIX_READ_ONLY_TOOL_CALLS_CONSTRAINT in constraint_names,
                }
            )
        for artifact_name in stage.reads.artifacts:
            if artifact_name not in artifact_consumers:
                continue
            artifact_consumers[artifact_name].append(
                {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                }
            )
        for artifact_name in stage.writes_artifacts:
            if artifact_name not in artifact_producers:
                continue
            artifact_producers[artifact_name].append(
                {
                    "stage": stage.name,
                    "workflow_phase": stage.metadata.workflow_phase,
                }
            )

    for producer_stage in manifest.stages:
        if not producer_stage.uses_tools:
            continue
        for artifact_name in producer_stage.writes_artifacts:
            if artifact_name not in heavy_artifacts:
                continue
            producer_index = stage_indices[producer_stage.name]
            compressed_by = [
                consumer_stage.name
                for consumer_stage in manifest.stages[producer_index + 1 :]
                if _is_artifact_compression_boundary(
                    consumer_stage,
                    artifact_name,
                )
            ]
            heavy_tool_artifact_boundaries.append(
                {
                    "artifact": artifact_name,
                    "producer_stage": producer_stage.name,
                    "producer_phase": producer_stage.metadata.workflow_phase,
                    "tools": list(producer_stage.uses_tools),
                    "compressed_by": compressed_by,
                }
            )

    for artifact_name, consumers in artifact_consumers.items():
        producer = artifact_producers.get(artifact_name, [])
        producer_stage = producer[0]["stage"] if len(producer) == 1 else None
        producer_phase = producer[0]["workflow_phase"] if len(producer) == 1 else None
        for consumer in consumers:
            if consumer["workflow_phase"] not in suffix_phases:
                continue
            suffix_artifact_lineage_reads.append(
                {
                    "artifact": artifact_name,
                    "consumer_stage": consumer["stage"],
                    "consumer_phase": consumer["workflow_phase"],
                    "producer_stage": producer_stage,
                    "producer_phase": producer_phase,
                    "ambiguous_producer": len(producer) != 1,
                }
            )

    for stage_index, stage in enumerate(manifest.stages):
        if stage.metadata.tool_intent != "re_retrieval":
            continue
        next_stage = manifest.stages[stage_index + 1] if stage_index + 1 < len(manifest.stages) else None
        compression_stage = None
        if (
            next_stage is not None
            and next_stage.metadata.stage_type == "compression"
            and set(stage.writes_artifacts).issubset(set(next_stage.reads.artifacts))
        ):
            compression_stage = next_stage
        suffix_retrieval_subloops.append(
            {
                "tool_stage": stage.name,
                "tool_phase": stage.metadata.workflow_phase,
                "post_tool_policy": stage.metadata.post_tool_policy,
                "artifacts": list(stage.writes_artifacts),
                "compression_stage": compression_stage.name if compression_stage else None,
                "compression_phase": compression_stage.metadata.workflow_phase if compression_stage else None,
            }
        )

    return {
        "version": "artifact-tool-lint-v3",
        "declared_exception_constraints": sorted(
            constraint_name
            for constraint_name in constraint_names
            if constraint_name
            in {
                ALLOW_SUFFIX_RAW_TASK_READ_CONSTRAINT,
                ALLOW_SUFFIX_HEAVY_ARTIFACT_READS_CONSTRAINT,
                ALLOW_SUFFIX_READ_ONLY_TOOL_CALLS_CONSTRAINT,
            }
        ),
        "task_boundary": {
            "suffix_raw_task_reads": suffix_raw_task_reads,
        },
        "artifact_tool_boundary": {
            "heavy_artifacts": sorted(heavy_artifacts.keys()),
            "suffix_heavy_artifact_reads": suffix_heavy_artifact_reads,
            "suffix_read_only_tool_stages": suffix_read_only_tool_stages,
            "heavy_tool_artifact_boundaries": heavy_tool_artifact_boundaries,
            "tool_contracts": tool_contracts,
            "suffix_retrieval_subloops": suffix_retrieval_subloops,
        },
        "artifact_lineage": {
            "declared_artifacts": sorted(artifact_producers.keys()),
            "artifact_producers": artifact_producers,
            "artifact_consumers": artifact_consumers,
            "unproduced_artifacts": sorted(
                artifact_name for artifact_name, producers in artifact_producers.items() if not producers
            ),
            "multi_producer_artifacts": {
                artifact_name: producers
                for artifact_name, producers in artifact_producers.items()
                if len(producers) > 1
            },
            "suffix_artifact_lineage_reads": suffix_artifact_lineage_reads,
        },
        "state_semantics": {
            "anchor_answer_like_stages": anchor_answer_like_stages,
            "mixed_control_family_stages": mixed_control_family_stages,
            "control_family_summary": control_family_summary,
        },
    }


def _validate_manifest(manifest: SkillManifest) -> Dict[str, object]:
    stage_names = set()
    available_state: List[str] = []
    written_state_fields: Dict[str, str] = {}
    artifact_names = {artifact.name for artifact in manifest.artifacts}
    heavy_artifact_names = {
        artifact.name
        for artifact in manifest.artifacts
        if _looks_like_heavy_artifact(artifact.name, artifact.description)
    }
    tool_names = {tool.name for tool in manifest.tools}
    constraint_names = {constraint.name for constraint in manifest.constraints}
    suffix_phases = {"route", "execute", "verify", "handoff"}
    profile_names = set()
    prior_phase_rank = -1
    available_artifacts = set()
    written_artifacts: Dict[str, str] = {}
    if not manifest.canonical_task_fields:
        raise ValueError("Skill must declare canonical task fields.")
    if not manifest.input_profiles:
        raise ValueError("Skill must declare at least one input profile.")
    for profile in manifest.input_profiles:
        if profile.name in profile_names:
            raise ValueError(f"Duplicate input profile name: {profile.name}")
        profile_names.add(profile.name)
        if not profile.match_fields:
            raise ValueError(f"Input profile {profile.name} must declare match fields.")
        missing_maps = [
            field for field in manifest.canonical_task_fields if field not in profile.field_map
        ]
        if missing_maps:
            raise ValueError(
                f"Input profile {profile.name} is missing canonical field mappings for: {missing_maps}"
            )
    for schema_name, schema in manifest.output_schemas.items():
        if not schema.fields:
            raise ValueError(f"Output schema {schema_name} must define at least one field.")
    for stage in manifest.stages:
        control_families = {
            _infer_control_family(field_name)
            for field_name in stage.writes
            if _infer_control_family(field_name) in CONTROL_FAMILIES
        }
        if stage.name in stage_names:
            raise ValueError(f"Duplicate stage name: {stage.name}")
        stage_names.add(stage.name)
        if not stage.metadata.stage_type:
            raise ValueError(f"Stage {stage.name} must declare stage_type.")
        if stage.metadata.stage_type not in VALID_STAGE_TYPES:
            raise ValueError(
                f"Stage {stage.name} declares invalid stage_type {stage.metadata.stage_type!r}. "
                f"Expected one of {sorted(VALID_STAGE_TYPES)}."
            )
        if not stage.metadata.workflow_phase:
            raise ValueError(f"Stage {stage.name} must declare workflow_phase.")
        if stage.metadata.workflow_phase not in VALID_WORKFLOW_PHASES:
            raise ValueError(
                f"Stage {stage.name} declares invalid workflow_phase {stage.metadata.workflow_phase!r}. "
                f"Expected one of {sorted(VALID_WORKFLOW_PHASES)}."
            )
        current_phase_rank = WORKFLOW_PHASE_ORDER[stage.metadata.workflow_phase]
        if current_phase_rank < prior_phase_rank:
            raise ValueError(
                f"Stage {stage.name} regresses workflow_phase ordering with "
                f"{stage.metadata.workflow_phase!r}. Skill phases must follow "
                "observe -> abstract -> route -> execute -> verify -> handoff."
            )
        prior_phase_rank = current_phase_rank
        if stage.metadata.criticality not in VALID_CRITICALITY:
            raise ValueError(
                f"Stage {stage.name} declares invalid criticality {stage.metadata.criticality!r}. "
                f"Expected one of {sorted(VALID_CRITICALITY)}."
            )
        if stage.metadata.tool_mode not in VALID_TOOL_MODES:
            raise ValueError(
                f"Stage {stage.name} declares invalid tool_mode {stage.metadata.tool_mode!r}. "
                f"Expected one of {sorted(VALID_TOOL_MODES)}."
            )
        if stage.metadata.tool_intent not in VALID_TOOL_INTENTS:
            raise ValueError(
                f"Stage {stage.name} declares invalid tool_intent {stage.metadata.tool_intent!r}. "
                f"Expected one of {sorted(VALID_TOOL_INTENTS)}."
            )
        if stage.metadata.post_tool_policy not in VALID_POST_TOOL_POLICIES:
            raise ValueError(
                f"Stage {stage.name} declares invalid post_tool_policy {stage.metadata.post_tool_policy!r}. "
                f"Expected one of {sorted(VALID_POST_TOOL_POLICIES)}."
            )
        if not stage.writes:
            raise ValueError(f"Stage {stage.name} must declare at least one write field.")
        if stage.metadata.anchor_stage and stage.metadata.workflow_phase not in {"observe", "abstract"}:
            raise ValueError(
                f"Stage {stage.name} declares anchor_stage but uses workflow_phase "
                f"{stage.metadata.workflow_phase!r}. Anchors must live in observe/abstract phases."
            )
        if stage.metadata.anchor_stage and control_families.intersection({"decision", "action"}):
            raise ValueError(
                f"Anchor stage {stage.name} writes answer-like fields with control families "
                f"{sorted(control_families)}. Anchors must stop at reusable facts/blockers/paths."
            )
        if len(control_families) > 2:
            raise ValueError(
                f"Stage {stage.name} mixes too many control families in one schema: "
                f"{sorted(control_families)}. Split blocker/path/decision/action surfaces across "
                "adjacent stages instead of collapsing them into one stage output."
            )
        disallowed_control_families = control_families - PHASE_ALLOWED_CONTROL_FAMILIES[
            stage.metadata.workflow_phase
        ]
        if disallowed_control_families:
            raise ValueError(
                f"Stage {stage.name} writes control-family fields {sorted(control_families)} "
                f"but workflow_phase {stage.metadata.workflow_phase!r} only allows "
                f"{sorted(PHASE_ALLOWED_CONTROL_FAMILIES[stage.metadata.workflow_phase])}. "
                f"Disallowed families: {sorted(disallowed_control_families)}."
            )
        heavy_raw_task_reads = [
            field for field in stage.reads.task if _looks_like_heavy_raw_task_field(field)
        ]
        if (
            stage.metadata.workflow_phase in {"route", "execute", "verify", "handoff"}
            and heavy_raw_task_reads
            and ALLOW_SUFFIX_RAW_TASK_READ_CONSTRAINT not in constraint_names
        ):
            raise ValueError(
                f"Stage {stage.name} rereads heavy raw task fields {heavy_raw_task_reads} "
                f"in workflow_phase {stage.metadata.workflow_phase!r}. Downstream stages must "
                "normally rely on reusable state; declare the "
                f"{ALLOW_SUFFIX_RAW_TASK_READ_CONSTRAINT!r} constraint only for explicit full/raw baselines."
            )
        heavy_artifact_reads = [
            artifact_name for artifact_name in stage.reads.artifacts if artifact_name in heavy_artifact_names
        ]
        if (
            stage.metadata.workflow_phase in suffix_phases
            and heavy_artifact_reads
            and ALLOW_SUFFIX_HEAVY_ARTIFACT_READS_CONSTRAINT not in constraint_names
        ):
            raise ValueError(
                f"Stage {stage.name} rereads heavy artifacts {heavy_artifact_reads} in "
                f"workflow_phase {stage.metadata.workflow_phase!r}. Downstream stages should "
                "normally rely on compressed state; declare the "
                f"{ALLOW_SUFFIX_HEAVY_ARTIFACT_READS_CONSTRAINT!r} constraint only for explicit "
                "full/raw artifact baselines."
            )
        if (
            stage.metadata.workflow_phase in suffix_phases
            and stage.metadata.tool_mode == "read_only"
            and stage.uses_tools
            and ALLOW_SUFFIX_READ_ONLY_TOOL_CALLS_CONSTRAINT not in constraint_names
        ):
            raise ValueError(
                f"Stage {stage.name} uses read-only tools {stage.uses_tools} in "
                f"workflow_phase {stage.metadata.workflow_phase!r}. Downstream stages should "
                "not reopen evidence by default; declare the "
                f"{ALLOW_SUFFIX_READ_ONLY_TOOL_CALLS_CONSTRAINT!r} constraint only for explicit "
                "re-retrieval or full-evidence baselines."
            )
        if stage.output_schema:
            if stage.output_schema not in manifest.output_schemas:
                raise ValueError(
                    f"Stage {stage.name} references unknown output schema {stage.output_schema}."
                )
            schema_fields = set(manifest.output_schemas[stage.output_schema].fields.keys())
            write_fields = set(stage.writes)
            if schema_fields != write_fields:
                raise ValueError(
                    f"Stage {stage.name} output schema fields {sorted(schema_fields)} do not match writes {sorted(write_fields)}."
                )
        for field in stage.writes:
            previous_stage = written_state_fields.get(field)
            if previous_stage is not None:
                raise ValueError(
                    f"Stage {stage.name} rewrites state field {field!r}, which was already "
                    f"written by stage {previous_stage}. Rewrites are not allowed in the current contract."
                )
            written_state_fields[field] = stage.name
        missing_task_reads = [
            field for field in stage.reads.task if field not in manifest.canonical_task_fields
        ]
        if missing_task_reads:
            raise ValueError(
                f"Stage {stage.name} reads canonical task fields that do not exist: {missing_task_reads}"
            )
        missing_artifact_reads = [
            field for field in stage.reads.artifacts if field not in artifact_names
        ]
        if missing_artifact_reads:
            raise ValueError(
                f"Stage {stage.name} reads artifacts that are not declared: {missing_artifact_reads}"
            )
        unavailable_artifact_reads = [
            field for field in stage.reads.artifacts if field not in available_artifacts
        ]
        if unavailable_artifact_reads:
            raise ValueError(
                f"Stage {stage.name} reads artifacts that have not been produced yet: {unavailable_artifact_reads}"
            )
        missing_artifact_writes = [
            field for field in stage.writes_artifacts if field not in artifact_names
        ]
        if missing_artifact_writes:
            raise ValueError(
                f"Stage {stage.name} writes artifacts that are not declared: {missing_artifact_writes}"
            )
        missing_tools = [tool for tool in stage.uses_tools if tool not in tool_names]
        if missing_tools:
            raise ValueError(
                f"Stage {stage.name} uses tools that are not declared: {missing_tools}"
            )
        if stage.uses_tools and stage.metadata.tool_mode == "none":
            raise ValueError(
                f"Stage {stage.name} uses tools but declares tool_mode none."
            )
        if not stage.uses_tools and stage.metadata.tool_mode != "none":
            raise ValueError(
                f"Stage {stage.name} declares tool_mode {stage.metadata.tool_mode!r} "
                "but does not use any tools."
            )
        if stage.uses_tools and stage.metadata.tool_intent == "none":
            raise ValueError(
                f"Stage {stage.name} uses tools but does not declare tool_intent."
            )
        if not stage.uses_tools and stage.metadata.tool_intent != "none":
            raise ValueError(
                f"Stage {stage.name} declares tool_intent {stage.metadata.tool_intent!r} "
                "but does not use any tools."
            )
        if not stage.uses_tools and stage.metadata.post_tool_policy != "none":
            raise ValueError(
                f"Stage {stage.name} declares post_tool_policy {stage.metadata.post_tool_policy!r} "
                "but does not use any tools."
            )
        if stage.uses_tools and stage.metadata.tool_mode == "read_only":
            if stage.metadata.tool_intent not in {"retrieval", "re_retrieval", "lookup"}:
                raise ValueError(
                    f"Stage {stage.name} uses read-only tools but declares incompatible "
                    f"tool_intent {stage.metadata.tool_intent!r}."
                )
        if stage.uses_tools and stage.metadata.tool_mode == "side_effecting":
            if stage.metadata.tool_intent != "side_effect":
                raise ValueError(
                    f"Stage {stage.name} uses side-effecting tools and must declare "
                    "tool_intent 'side_effect'."
                )
        if stage.uses_tools and stage.writes_artifacts and stage.metadata.post_tool_policy == "none":
            raise ValueError(
                f"Stage {stage.name} writes artifacts via tools but does not declare post_tool_policy."
            )
        if stage.metadata.tool_intent == "re_retrieval":
            if stage.metadata.workflow_phase not in suffix_phases:
                raise ValueError(
                    f"Stage {stage.name} declares tool_intent 're_retrieval' but is not in a suffix phase."
                )
            if stage.metadata.post_tool_policy != "requires_compression":
                raise ValueError(
                    f"Stage {stage.name} declares tool_intent 're_retrieval' but post_tool_policy "
                    f"is {stage.metadata.post_tool_policy!r}. Re-retrieval must return through a compression stage."
                )
            if not stage.writes_artifacts:
                raise ValueError(
                    f"Stage {stage.name} declares tool_intent 're_retrieval' but does not write artifacts."
                )
        missing = [field for field in stage.reads.state if field not in available_state]
        if missing:
            raise ValueError(
                f"Stage {stage.name} reads state fields that do not exist yet: {missing}"
            )
        available_state.extend(stage.writes)
        for artifact_name in stage.writes_artifacts:
            previous_stage = written_artifacts.get(artifact_name)
            if previous_stage is not None:
                raise ValueError(
                    f"Stage {stage.name} rewrites artifact {artifact_name!r}, which was already "
                    f"written by stage {previous_stage}. Artifact rewrites are not allowed in the current contract."
                )
            written_artifacts[artifact_name] = stage.name
            available_artifacts.add(artifact_name)

    unproduced_artifacts = sorted(artifact_names - set(written_artifacts))
    if unproduced_artifacts:
        raise ValueError(
            f"Artifacts declared but never produced: {unproduced_artifacts}"
        )

    for stage_index, stage in enumerate(manifest.stages):
        if stage.metadata.tool_intent != "re_retrieval":
            continue
        if stage_index + 1 >= len(manifest.stages):
            raise ValueError(
                f"Stage {stage.name} declares tool_intent 're_retrieval' but has no following compression stage."
            )
        next_stage = manifest.stages[stage_index + 1]
        if next_stage.metadata.stage_type != "compression":
            raise ValueError(
                f"Stage {stage.name} declares tool_intent 're_retrieval' but the next stage "
                f"{next_stage.name} is not a compression stage."
            )
        missing_artifacts = [
            artifact_name
            for artifact_name in stage.writes_artifacts
            if artifact_name not in next_stage.reads.artifacts
        ]
        if missing_artifacts:
            raise ValueError(
                f"Stage {stage.name} declares tool_intent 're_retrieval' but the next compression stage "
                f"{next_stage.name} does not read artifacts {missing_artifacts}."
            )
        if next_stage.uses_tools:
            raise ValueError(
                f"Stage {stage.name} declares tool_intent 're_retrieval' but the next compression stage "
                f"{next_stage.name} also uses tools. Re-retrieval must return to pure compression first."
            )

    for stage_index, producer_stage in enumerate(manifest.stages):
        if not producer_stage.uses_tools:
            continue
        for artifact_name in producer_stage.writes_artifacts:
            if artifact_name not in heavy_artifact_names:
                continue
            compressed_by = [
                consumer_stage.name
                for consumer_stage in manifest.stages[stage_index + 1 :]
                if _is_artifact_compression_boundary(
                    consumer_stage,
                    artifact_name,
                )
            ]
            if not compressed_by:
                raise ValueError(
                    f"Tool stage {producer_stage.name} writes heavy artifact {artifact_name!r} "
                    "but no later compression boundary converts it into reusable state before "
                    "downstream reasoning. Add an explicit artifact-to-state compression stage."
                )

    return _build_static_lint_report(manifest)


def load_skill_manifest(skill_path: Path) -> SkillManifest:
    manifest_path = skill_path / "SKILL.md" if skill_path.is_dir() else skill_path
    text = manifest_path.read_text()
    metadata, body = _parse_frontmatter(text)
    lines = body.splitlines()
    manifest = SkillManifest(
        name=str(metadata["name"]),
        description=str(metadata.get("description", "")),
        canonical_task_fields=_parse_simple_list_section(lines, "## Canonical Task"),
        input_profiles=_parse_input_profiles(lines),
        constraints=_parse_constraints(lines),
        tools=list(_parse_named_section(lines, "## Tools", ToolDefinition)),
        artifacts=list(_parse_named_section(lines, "## Artifacts", ArtifactDefinition)),
        output_schemas=_parse_output_schemas(lines),
        stages=_parse_workflow_stages(lines),
    )
    manifest.static_lint = _validate_manifest(manifest)
    return manifest
