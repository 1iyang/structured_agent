"""Traditional full-history carry baseline."""

from __future__ import annotations

from typing import Optional, TypeVar

from ..schemas import SLOPolicyConfig, StateTransferMethodArtifacts
from ..state_transfer import StructuredStateStore
from .common import TransferMethodRuntime, TransferWorkflowAdapter, finalize_method

TTask = TypeVar("TTask")


def execute_full_history_carry(
    task: TTask,
    policy_config: SLOPolicyConfig,
    artifacts: StateTransferMethodArtifacts,
    adapter: TransferWorkflowAdapter[TTask],
    runtime: TransferMethodRuntime,
    history_seed: Optional[str] = None,
) -> str:
    history_text = history_seed or adapter.build_history_seed(task)
    initial_stage = adapter.initial_stage()
    initial_store = StructuredStateStore(artifacts.method_name)
    parsed = runtime.run_initial_stage(task, policy_config, artifacts, initial_store, initial_stage)
    history_text = runtime.append_history(history_text, initial_stage.stage_name, parsed.model_dump())
    for stage in adapter.routed_stages():
        parsed = runtime.run_history_stage(
            task,
            policy_config,
            artifacts,
            stage,
            history_text,
        )
        history_text = runtime.append_history(history_text, stage.stage_name, parsed.model_dump())
    finalize_method(artifacts, policy_config)
    return history_text
