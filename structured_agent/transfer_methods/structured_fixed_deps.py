"""Structured fixed-dependency transfer strategy."""

from __future__ import annotations

from typing import Optional, TypeVar

from ..schemas import SLOPolicyConfig, StateTransferMethodArtifacts
from ..state_transfer import StructuredStateStore
from .common import TransferMethodRuntime, TransferWorkflowAdapter, finalize_method

TTask = TypeVar("TTask")


def execute_structured_fixed_deps(
    task: TTask,
    policy_config: SLOPolicyConfig,
    artifacts: StateTransferMethodArtifacts,
    adapter: TransferWorkflowAdapter[TTask],
    runtime: TransferMethodRuntime,
    store: Optional[StructuredStateStore] = None,
) -> StructuredStateStore:
    structured_store = store or adapter.build_structured_store(task, artifacts.method_name)
    initial_stage = adapter.initial_stage()
    runtime.run_initial_stage(task, policy_config, artifacts, structured_store, initial_stage)
    contracts = adapter.fixed_dependency_contracts()
    for stage in adapter.routed_stages():
        runtime.run_fixeddeps_stage(
            task,
            policy_config,
            artifacts,
            structured_store,
            contracts[stage.stage_name],
            stage,
        )
    finalize_method(artifacts, policy_config, structured_store)
    return structured_store
