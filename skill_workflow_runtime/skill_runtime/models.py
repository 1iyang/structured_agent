from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ConstraintDefinition:
    name: str
    description: str = ""


@dataclass
class StageReads:
    task: List[str] = field(default_factory=list)
    state: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)


@dataclass
class StageMetadata:
    stage_type: str
    workflow_phase: str
    anchor_stage: bool = False
    criticality: str = "medium"
    optional_stage: bool = False
    tool_mode: str = "none"
    tool_intent: str = "none"
    post_tool_policy: str = "none"
    prompt_cost_hint: int = 0


@dataclass
class StageDefinition:
    name: str
    executor: str
    reads: StageReads
    writes: List[str]
    metadata: StageMetadata
    output_schema: str = ""
    uses_tools: List[str] = field(default_factory=list)
    writes_artifacts: List[str] = field(default_factory=list)
    instruction: str = ""


@dataclass
class InputProfile:
    name: str
    match_fields: List[str]
    field_map: Dict[str, str]
    description: str = ""


@dataclass
class ToolDefinition:
    name: str
    kind: str
    description: str = ""


@dataclass
class ArtifactDefinition:
    name: str
    description: str = ""


@dataclass
class OutputFieldDefinition:
    name: str
    type_spec: str
    description: str = ""


@dataclass
class OutputSchemaDefinition:
    name: str
    fields: Dict[str, OutputFieldDefinition]
    description: str = ""


@dataclass
class SkillManifest:
    name: str
    description: str
    canonical_task_fields: List[str]
    input_profiles: List[InputProfile]
    constraints: List[ConstraintDefinition]
    tools: List[ToolDefinition]
    artifacts: List[ArtifactDefinition]
    output_schemas: Dict[str, OutputSchemaDefinition]
    stages: List[StageDefinition]
    static_lint: Dict[str, Any] = field(default_factory=dict)


StageOutput = Dict[str, Any]
TaskPayload = Dict[str, Any]
