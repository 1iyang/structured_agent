from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

try:
    from skill_runtime.cli import add_local_llm_backend_args, apply_local_llm_backend_overrides
    from skill_runtime.loader import load_skill_manifest
    from skill_runtime.llm import build_default_json_backend
    from skill_runtime.runtime import SkillWorkflowRuntime
    from skill_runtime.strategy import build_runtime_strategy
except ModuleNotFoundError:
    from skill_workflow_runtime.skill_runtime.cli import (
        add_local_llm_backend_args,
        apply_local_llm_backend_overrides,
    )
    from skill_workflow_runtime.skill_runtime.loader import load_skill_manifest
    from skill_workflow_runtime.skill_runtime.llm import build_default_json_backend
    from skill_workflow_runtime.skill_runtime.runtime import SkillWorkflowRuntime
    from skill_workflow_runtime.skill_runtime.strategy import build_runtime_strategy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / "skill_workflow_runtime" / "skills"
TASKS_ROOT = PROJECT_ROOT / "skill_workflow_runtime" / "tasks"
OPTIONAL_STAGE_NAME = "response_refinement"
DEFAULT_SUPPORT_SKILLS = [
    "support_ticket",
    "support_ticket_full_evidence",
    "support_ticket_raw_context_baseline",
]
DEFAULT_SUPPORT_TASKS = [
    "support_ticket_realistic_profile_demo.json",
    "support_ticket_account_recovery_complex_demo.json",
    "support_ticket_low_risk_ready_demo.json",
]


def default_summary_path() -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / "support_budget_experiments"
        / "support_budget_summary.json"
    )


def default_markdown_path() -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / "support_budget_experiments"
        / "support_budget_summary.md"
    )


def _load_task(task_name: str) -> Dict[str, Any]:
    return json.loads((TASKS_ROOT / task_name).read_text())


def _optional_stage_record(result: Dict[str, Any]) -> Dict[str, Any]:
    return next(stage for stage in result["stages"] if stage["name"] == OPTIONAL_STAGE_NAME)


def _required_budget(record: Dict[str, Any]) -> int:
    strategy_context = record["strategy_context"]
    return (
        int(strategy_context["consumed_prompt_tokens_before_stage"])
        + int(strategy_context["estimated_optional_branch_prompt_tokens"])
    )


def _run_support_skill(
    *,
    skill_dir_name: str,
    task: Dict[str, Any],
    strategy_name: str,
    prompt_budget_threshold: int | None,
    llm_backend: Any | None = None,
) -> Dict[str, Any]:
    manifest = load_skill_manifest(SKILLS_ROOT / skill_dir_name)
    runtime = SkillWorkflowRuntime(
        llm_backend=llm_backend,
        strategy=build_runtime_strategy(
            strategy_name,
            prompt_budget_threshold=prompt_budget_threshold,
        )
    )
    return runtime.run(manifest=manifest, task=task)


def _summarize_run(result: Dict[str, Any]) -> Dict[str, Any]:
    record = _optional_stage_record(result)
    strategy_context = record["strategy_context"]
    decision = record["decision"]
    return {
        "skill_name": result["skill_name"],
        "task_id": result["task_id"],
        "optional_stage": OPTIONAL_STAGE_NAME,
        "decision_reason": decision["reason"],
        "decision_notes": list(decision["notes"]),
        "skipped": bool(record.get("skipped")),
        "required_prompt_budget": _required_budget(record),
        "consumed_prompt_tokens_before_stage": int(
            strategy_context["consumed_prompt_tokens_before_stage"]
        ),
        "estimated_branch_prompt_tokens_runtime": int(
            strategy_context["estimated_optional_branch_prompt_tokens_runtime"]
        ),
        "estimated_branch_prompt_tokens_metadata": int(
            strategy_context["estimated_optional_branch_prompt_tokens_metadata"]
        ),
        "estimated_branch_prompt_tokens": int(
            strategy_context["estimated_optional_branch_prompt_tokens"]
        ),
        "suffix_prompt_tokens": result["token_summary"]["suffix_prompt_tokens"],
        "prompt_tokens_total": result["token_summary"]["prompt_tokens_total"],
        "input_view_tokens_total": result["token_summary"]["input_view_tokens_total"],
        "final_state": {
            "issue_type": result["final_state"].get("issue_type"),
            "eligibility_decision": result["final_state"].get("eligibility_decision"),
            "resolution_category": result["final_state"].get("resolution_category"),
            "handoff_status": result["final_state"].get("handoff_status"),
            "refinement_focus": result["final_state"].get("refinement_focus", []),
        },
    }


def _choose_crossover_budget(state_only_runs: Dict[str, Dict[str, Any]]) -> int | None:
    minimal = state_only_runs.get("support_ticket")
    if not minimal or minimal["skipped"]:
        return None

    minimal_required = int(minimal["required_prompt_budget"])
    competitor_required: List[int] = []
    for skill_name, run in state_only_runs.items():
        if skill_name == "support_ticket":
            continue
        if run["skipped"]:
            continue
        competitor_required.append(int(run["required_prompt_budget"]))

    if not competitor_required:
        return None

    crossover_budget = min(competitor_required) - 1
    if crossover_budget < minimal_required:
        return None
    return crossover_budget


def _render_markdown(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Support State + Budget Experiments")
    lines.append("")
    lines.append(f"- strategy: `{summary['strategy']}`")
    lines.append(f"- optional stage: `{summary['optional_stage']}`")
    lines.append(f"- llm mode: `{summary['llm_mode']}`")
    lines.append(f"- status: `{summary.get('status', 'complete')}`")
    if summary.get("failed_task"):
        failed_task = summary["failed_task"]
        lines.append(
            f"- failed task: `{failed_task.get('task_id') or failed_task.get('task_file')}`"
        )
        if failed_task.get("error"):
            lines.append(f"- failure: `{failed_task['error']}`")
    lines.append("")

    for task_summary in summary["tasks"]:
        lines.append(f"## {task_summary['task_id']}")
        lines.append("")
        lines.append(f"- task file: `{task_summary['task_file']}`")
        if task_summary["budget_scenarios"]["crossover"] is None:
            lines.append("- crossover budget: none")
        else:
            lines.append(
                f"- crossover budget: `{task_summary['budget_scenarios']['crossover']}`"
            )
        lines.append(
            f"- tight budget: `{task_summary['budget_scenarios']['tight']}`"
        )
        lines.append(
            f"- open-all budget: `{task_summary['budget_scenarios']['open_all']}`"
        )
        lines.append("")
        lines.append(
            "| skill | required budget | runtime est | metadata hint | state-only | tight | crossover | open-all |"
        )
        lines.append(
            "|---|---:|---:|---:|---|---|---|---|"
        )
        for record in task_summary["skills"]:
            budgets = record["budget_runs"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        record["skill_name"],
                        str(record["state_only"]["required_prompt_budget"]),
                        str(record["state_only"]["estimated_branch_prompt_tokens_runtime"]),
                        str(record["state_only"]["estimated_branch_prompt_tokens_metadata"]),
                        record["state_only"]["decision_reason"],
                        budgets["tight"]["decision_reason"],
                        budgets["crossover"]["decision_reason"]
                        if budgets["crossover"] is not None
                        else "n/a",
                        budgets["open_all"]["decision_reason"],
                    ]
                )
                + " |"
            )
        lines.append("")
    return "\n".join(lines)


def _build_summary_payload(
    *,
    strategy_name: str,
    task_summaries: List[Dict[str, Any]],
    status: str = "complete",
    failed_task: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "strategy": strategy_name,
        "optional_stage": OPTIONAL_STAGE_NAME,
        "llm_mode": os.environ.get("SKILL_RUNTIME_LLM_MODE", "deterministic"),
        "status": status,
        "completed_tasks": len(task_summaries),
        "failed_task": failed_task,
        "tasks": task_summaries,
    }


def _write_summary_outputs(
    *,
    summary: Dict[str, Any],
    output_path: Path | None,
    markdown_output_path: Path | None,
) -> None:
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    if markdown_output_path is not None:
        markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_output_path.write_text(_render_markdown(summary))


def run_support_budget_experiments(
    *,
    task_names: List[str],
    skill_names: List[str],
    strategy_name: str,
    output_path: Path | None = None,
    markdown_output_path: Path | None = None,
) -> Dict[str, Any]:
    task_summaries: List[Dict[str, Any]] = []
    llm_backend = build_default_json_backend()
    try:
        for task_name in task_names:
            task_id: Any = None
            try:
                task = _load_task(task_name)
                task_id = task.get("task_id") or task.get("ticket_id") or task.get("id")
                state_only_runs: Dict[str, Dict[str, Any]] = {}
                for skill_name in skill_names:
                    result = _run_support_skill(
                        skill_dir_name=skill_name,
                        task=task,
                        strategy_name=strategy_name,
                        prompt_budget_threshold=None,
                        llm_backend=llm_backend,
                    )
                    state_only_runs[skill_name] = _summarize_run(result)

                open_required_thresholds = [
                    run["required_prompt_budget"]
                    for run in state_only_runs.values()
                    if not run["skipped"]
                ]
                tight_budget = min(open_required_thresholds) - 1 if open_required_thresholds else 0
                open_all_budget = max(open_required_thresholds) + 50 if open_required_thresholds else 0
                crossover_budget = _choose_crossover_budget(state_only_runs)

                skill_summaries: List[Dict[str, Any]] = []
                for skill_name in skill_names:
                    budget_runs: Dict[str, Any] = {}
                    for label, budget in [
                        ("tight", tight_budget),
                        ("crossover", crossover_budget),
                        ("open_all", open_all_budget),
                    ]:
                        if budget is None:
                            budget_runs[label] = None
                            continue
                        result = _run_support_skill(
                            skill_dir_name=skill_name,
                            task=task,
                            strategy_name=strategy_name,
                            prompt_budget_threshold=budget,
                            llm_backend=llm_backend,
                        )
                        budget_runs[label] = _summarize_run(result)

                    skill_summaries.append(
                        {
                            "skill_name": skill_name,
                            "state_only": state_only_runs[skill_name],
                            "budget_runs": budget_runs,
                        }
                    )

                task_summaries.append(
                    {
                        "task_file": task_name,
                        "task_id": task_id,
                        "budget_scenarios": {
                            "tight": tight_budget,
                            "crossover": crossover_budget,
                            "open_all": open_all_budget,
                        },
                        "skills": skill_summaries,
                    }
                )
                _write_summary_outputs(
                    summary=_build_summary_payload(
                        strategy_name=strategy_name,
                        task_summaries=task_summaries,
                        status="running",
                    ),
                    output_path=output_path,
                    markdown_output_path=markdown_output_path,
                )
            except Exception as exc:
                _write_summary_outputs(
                    summary=_build_summary_payload(
                        strategy_name=strategy_name,
                        task_summaries=task_summaries,
                        status="failed",
                        failed_task={
                            "task_file": task_name,
                            "task_id": task_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    ),
                    output_path=output_path,
                    markdown_output_path=markdown_output_path,
                )
                raise
    finally:
        close_backend = getattr(llm_backend, "close", None)
        if callable(close_backend):
            close_backend()

    summary = _build_summary_payload(
        strategy_name=strategy_name,
        task_summaries=task_summaries,
        status="complete",
    )
    _write_summary_outputs(
        summary=summary,
        output_path=output_path,
        markdown_output_path=markdown_output_path,
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run support state + budget experiments.")
    parser.add_argument(
        "--tasks",
        nargs="*",
        default=DEFAULT_SUPPORT_TASKS,
        help="Task JSON filenames under skill_workflow_runtime/tasks/.",
    )
    parser.add_argument(
        "--skills",
        nargs="*",
        default=DEFAULT_SUPPORT_SKILLS,
        help="Skill directory names under skill_workflow_runtime/skills/.",
    )
    parser.add_argument(
        "--strategy",
        default="support-response-budget-policy",
        help="Runtime strategy name to use for support budget experiments.",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON summary output path.",
    )
    parser.add_argument(
        "--markdown-output",
        help="Optional Markdown summary output path.",
    )
    add_local_llm_backend_args(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    apply_local_llm_backend_overrides(args)

    output_path = Path(args.output) if args.output else default_summary_path()
    markdown_path = Path(args.markdown_output) if args.markdown_output else default_markdown_path()
    summary = run_support_budget_experiments(
        task_names=list(args.tasks),
        skill_names=list(args.skills),
        strategy_name=args.strategy,
        output_path=output_path,
        markdown_output_path=markdown_path,
    )

    print(f"Strategy: {summary['strategy']}")
    print(f"Optional stage: {summary['optional_stage']}")
    print(f"LLM mode: {summary['llm_mode']}")
    print(f"Status: {summary.get('status')}")
    print("Tasks:")
    for task_summary in summary["tasks"]:
        print(f"  - {task_summary['task_id']} ({task_summary['task_file']})")
        budgets = task_summary["budget_scenarios"]
        print(
            "    budgets: "
            f"tight={budgets['tight']}, "
            f"crossover={budgets['crossover']}, "
            f"open_all={budgets['open_all']}"
        )
    print(f"Wrote JSON summary to {output_path}")
    print(f"Wrote Markdown summary to {markdown_path}")


if __name__ == "__main__":
    main()
