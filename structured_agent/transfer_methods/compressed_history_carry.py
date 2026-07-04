"""Compressed-history carry baseline."""

from __future__ import annotations

from typing import Optional, TypeVar

from ..schemas import SLOPolicyConfig, StateTransferMethodArtifacts
from ..state_transfer import StructuredStateStore
from .common import (
    TransferMethodRuntime,
    TransferWorkflowAdapter,
    finalize_method,
    resolve_history_compression_config,
)

TTask = TypeVar("TTask")


def execute_compressed_history_carry(
    task: TTask,
    policy_config: SLOPolicyConfig,
    artifacts: StateTransferMethodArtifacts,
    adapter: TransferWorkflowAdapter[TTask],
    runtime: TransferMethodRuntime,
    history_seed: Optional[str] = None,
) -> str:
    transferable_history = history_seed or adapter.build_history_seed(task)
    compression_config = resolve_history_compression_config(adapter)
    initial_stage = adapter.initial_stage()
    initial_store = StructuredStateStore(artifacts.method_name)
    parsed = runtime.run_initial_stage(task, policy_config, artifacts, initial_store, initial_stage)
    transferable_history = runtime.append_history(
        transferable_history,
        initial_stage.stage_name,
        parsed.model_dump(),
    )
    for stage in adapter.routed_stages():
        compressed_history = runtime.compress_history(
            task,
            policy_config,
            artifacts,
            stage.stage_name,
            transferable_history,
            adapter.build_history_compression_prompt,
            compression_config,
        )
        parsed = runtime.run_history_stage(
            task,
            policy_config,
            artifacts,
            stage,
            compressed_history,
        )
        transferable_history = runtime.append_history(
            compressed_history,
            stage.stage_name,
            parsed.model_dump(),
        )
    finalize_method(artifacts, policy_config)
    return transferable_history
