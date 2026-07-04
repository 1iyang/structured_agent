from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

try:
    from skill_runtime.cli import add_local_llm_backend_args, apply_local_llm_backend_overrides
    from skill_runtime.loader import load_skill_manifest
    from skill_runtime.runtime import SkillWorkflowRuntime
    from skill_runtime.strategy import available_runtime_strategies, build_runtime_strategy
except ModuleNotFoundError:
    from skill_workflow_runtime.skill_runtime.cli import (
        add_local_llm_backend_args,
        apply_local_llm_backend_overrides,
    )
    from skill_workflow_runtime.skill_runtime.loader import load_skill_manifest
    from skill_workflow_runtime.skill_runtime.runtime import SkillWorkflowRuntime
    from skill_workflow_runtime.skill_runtime.strategy import (
        available_runtime_strategies,
        build_runtime_strategy,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _slugify(value: object) -> str:
    text = str(value).strip().lower()
    if not text:
        return "run"
    slug = re.sub(r"[^a-z0-9]+", "-", text)
    slug = slug.strip("-")
    return slug or "run"


def default_run_record_path(
    skill_name: str,
    task_id: object,
    *,
    strategy_name: str = "default",
    strategy_budget_threshold: int | None = None,
) -> Path:
    filename_parts = [_slugify(task_id or "task")]
    if strategy_name != "default":
        filename_parts.append(f"strategy-{_slugify(strategy_name)}")
    if strategy_budget_threshold is not None:
        filename_parts.append(f"budget-{strategy_budget_threshold}")
    filename = "__".join(filename_parts) + ".json"
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / _slugify(skill_name)
        / filename
    )


def resolve_output_path(
    requested_output: str | None,
    skill_name: str,
    task_id: object,
    *,
    strategy_name: str = "default",
    strategy_budget_threshold: int | None = None,
) -> Path:
    if requested_output:
        return Path(requested_output)
    return default_run_record_path(
        skill_name,
        task_id,
        strategy_name=strategy_name,
        strategy_budget_threshold=strategy_budget_threshold,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a skill-defined workflow.")
    parser.add_argument(
        "--skill",
        required=True,
        help="Path to a skill directory or a SKILL.md file.",
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Path to a task JSON file.",
    )
    parser.add_argument(
        "--task-index",
        type=int,
        default=0,
        help="If the task file is a JSON list, run the item at this index. Defaults to 0.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the full run record as JSON. If omitted, a project-local path under runs/skill_runtime/ is used.",
    )
    parser.add_argument(
        "--strategy",
        choices=available_runtime_strategies(),
        default="default",
        help="Runtime strategy plugin to apply when deciding optional or policy-controlled stages.",
    )
    parser.add_argument(
        "--strategy-budget-threshold",
        type=int,
        help="Optional prompt-token budget for budget-aware strategy plugins. For suffix-reretrieval-policy, the optional branch opens only when the remaining prompt budget can cover the estimated branch prompt cost.",
    )
    add_local_llm_backend_args(
        parser,
        max_num_seqs_help=(
            "Optional max_num_seqs override for local-vllm mode. Lower values are safer "
            "for offline single-task runs."
        ),
        enforce_eager_help=(
            "Force eager execution for local-vllm mode. This is the safer default for "
            "offline workflow runs."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # Keep CLI-to-runtime wiring centralized through env vars so backend construction stays in one place.
    apply_local_llm_backend_overrides(args)

    manifest = load_skill_manifest(Path(args.skill))
    task_path = Path(args.task)
    raw_task = json.loads(task_path.read_text())
    if isinstance(raw_task, list):
        if not raw_task:
            raise ValueError("Task list is empty.")
        if args.task_index < 0 or args.task_index >= len(raw_task):
            raise ValueError(
                f"Task index {args.task_index} is out of range for list of size {len(raw_task)}."
            )
        task = raw_task[args.task_index]
    else:
        task = raw_task

    runtime = SkillWorkflowRuntime(
        strategy=build_runtime_strategy(
            args.strategy,
            prompt_budget_threshold=args.strategy_budget_threshold,
        )
    )
    result = runtime.run(manifest=manifest, task=task)
    result.setdefault("skill_name", manifest.name)

    output_path = resolve_output_path(
        args.output,
        manifest.name,
        result.get("task_id"),
        strategy_name=args.strategy,
        strategy_budget_threshold=args.strategy_budget_threshold,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"Skill: {manifest.name}")
    print(f"Task id: {result.get('task_id', '<no-id>')}")
    print(f"Input profile: {result.get('input_profile')}")
    print(f"LLM mode: {os.environ.get('SKILL_RUNTIME_LLM_MODE', 'deterministic')}")
    if os.environ.get("CUDA_VISIBLE_DEVICES"):
        print(f"CUDA_VISIBLE_DEVICES: {os.environ.get('CUDA_VISIBLE_DEVICES')}")
    backend_info = result.get("llm_backend") or {}
    active_config = backend_info.get("active_config", {}) if isinstance(backend_info, dict) else {}
    active_profile = backend_info.get("active_profile") if isinstance(backend_info, dict) else None
    if backend_info:
        print("LLM backend:")
        print(f"  - model: {backend_info.get('model')}")
        print(f"  - active profile: {active_profile or '(unknown)'}")
        if active_config:
            print(f"  - max model len: {active_config.get('max_model_len')}")
            print(f"  - gpu memory utilization: {active_config.get('gpu_memory_utilization')}")
            print(f"  - max num seqs: {active_config.get('max_num_seqs')}")
            print(f"  - enforce eager: {active_config.get('enforce_eager')}")
    print(f"Strategy: {result.get('strategy')}")
    strategy_config = result.get("strategy_config") or {}
    if strategy_config:
        print("Strategy config:")
        for key, value in strategy_config.items():
            if key == "name":
                continue
            print(f"  - {key}: {value}")
    stage_groups = result.get("stage_groups", {})
    if stage_groups:
        print("Stage groups:")
        print(f"  - anchors: {', '.join(stage_groups.get('anchor_stages', [])) or '(none)'}")
        print(f"  - suffix: {', '.join(stage_groups.get('suffix_stages', [])) or '(none)'}")
        print(f"  - optional: {', '.join(stage_groups.get('optional_stages', [])) or '(none)'}")
        phase_groups = stage_groups.get("workflow_phase_groups", {})
        if phase_groups:
            print("Workflow phases:")
            for phase_name in ["observe", "abstract", "route", "execute", "verify", "handoff"]:
                phase_stages = phase_groups.get(phase_name, [])
                if phase_stages:
                    print(f"  - {phase_name}: {', '.join(phase_stages)}")
    constraints = result.get("constraints", [])
    if constraints:
        print("Constraints:")
        for constraint in constraints:
            print(f"  - {constraint.get('name')}")
    static_lint = result.get("static_lint", {})
    if static_lint:
        artifact_tool = static_lint.get("artifact_tool_boundary", {})
        artifact_lineage = static_lint.get("artifact_lineage", {})
        task_boundary = static_lint.get("task_boundary", {})
        print("Static lint:")
        print(
            "  - suffix raw task reads: "
            f"{len(task_boundary.get('suffix_raw_task_reads', []))}"
        )
        print(
            "  - suffix heavy artifact reads: "
            f"{len(artifact_tool.get('suffix_heavy_artifact_reads', []))}"
        )
        print(
            "  - suffix read-only tool stages: "
            f"{len(artifact_tool.get('suffix_read_only_tool_stages', []))}"
        )
        print(
            "  - tool contracts: "
            f"{len(artifact_tool.get('tool_contracts', []))}"
        )
        print(
            "  - suffix re-retrieval subloops: "
            f"{len(artifact_tool.get('suffix_retrieval_subloops', []))}"
        )
        print(
            "  - declared artifacts: "
            f"{len(artifact_lineage.get('declared_artifacts', []))}"
        )
        print(
            "  - suffix artifact lineage reads: "
            f"{len(artifact_lineage.get('suffix_artifact_lineage_reads', []))}"
        )
    token_summary = result.get("token_summary", {})
    if token_summary:
        print("Token summary:")
        print(f"  - counting mode: {token_summary.get('token_count_mode')}")
        print(f"  - canonical task: {token_summary.get('canonical_task_tokens')}")
        print(f"  - prompt total: {token_summary.get('prompt_tokens_total')}")
        print(f"  - input views total: {token_summary.get('input_view_tokens_total')}")
        print(f"  - prefix prompt: {token_summary.get('prefix_prompt_tokens')}")
        print(f"  - suffix prompt: {token_summary.get('suffix_prompt_tokens')}")
    print("Stages executed:")
    for stage in result["stages"]:
        status = " (skipped)" if stage.get("skipped") else ""
        print(f"  - {stage['name']}{status}")
    print("Final state keys:")
    for key in sorted(result["final_state"].keys()):
        print(f"  - {key}")
    print(f"Wrote run record to {output_path}")


if __name__ == "__main__":
    main()
