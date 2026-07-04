from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List

try:
    from benchmark_adapters.mulocbench import adapt_mulocbench_records, load_mulocbench_records
    from skill_runtime.cli import add_local_llm_backend_args, apply_local_llm_backend_overrides
    from skill_runtime.loader import load_skill_manifest
    from skill_runtime.runtime import SkillWorkflowRuntime
    from skill_runtime.strategy import build_runtime_strategy
    from summarize_mulocbench_runs import (
        render_mulocbench_summary_markdown,
        summarize_mulocbench_runs,
    )
except ModuleNotFoundError:
    from skill_workflow_runtime.benchmark_adapters.mulocbench import (
        adapt_mulocbench_records,
        load_mulocbench_records,
    )
    from skill_workflow_runtime.skill_runtime.cli import (
        add_local_llm_backend_args,
        apply_local_llm_backend_overrides,
    )
    from skill_workflow_runtime.skill_runtime.loader import load_skill_manifest
    from skill_workflow_runtime.skill_runtime.runtime import SkillWorkflowRuntime
    from skill_workflow_runtime.skill_runtime.strategy import build_runtime_strategy
    from skill_workflow_runtime.summarize_mulocbench_runs import (
        render_mulocbench_summary_markdown,
        summarize_mulocbench_runs,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / "skill_workflow_runtime" / "skills"


def default_tasks_output_path() -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / "mulocbench"
        / "tasks.json"
    )


def default_runs_dir() -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / "mulocbench"
        / "runs"
    )


def default_summary_json_path() -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / "mulocbench"
        / "summary.json"
    )


def default_summary_markdown_path() -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / "mulocbench"
        / "summary.md"
    )


def _slugify(value: object) -> str:
    text = str(value).strip().lower()
    if not text:
        return "task"
    chars = []
    for char in text:
        chars.append(char if char.isalnum() else "-")
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "task"


def _build_summary_payload(
    *,
    tasks: List[Dict[str, Any]],
    run_records: Dict[str, Dict[str, Any]],
    manifest_name: str,
    strategy_name: str,
    tasks_output_path: Path,
    runs_dir: Path,
    status: str = "complete",
    failed_task: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    summary = summarize_mulocbench_runs(tasks, run_records)
    summary["source_dataset"] = "MULocBench"
    summary["skill_name"] = manifest_name
    summary["strategy"] = strategy_name
    summary["llm_mode"] = os.environ.get("SKILL_RUNTIME_LLM_MODE", "deterministic")
    summary["task_file"] = str(tasks_output_path)
    summary["runs_dir"] = str(runs_dir)
    summary["status"] = status
    summary["completed_tasks"] = len(run_records)
    summary["failed_task"] = failed_task
    return summary


def _write_summary_outputs(
    *,
    summary: Dict[str, Any],
    summary_output_path: Path,
    summary_markdown_output_path: Path,
) -> None:
    summary_output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    summary_markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_markdown_output_path.write_text(render_mulocbench_summary_markdown(summary))


def run_mulocbench_localization_experiments(
    *,
    raw_records: List[Dict[str, Any]],
    skill_dir_name: str,
    repos_root: str | None,
    repo_layout: str,
    strategy_name: str,
    tasks_output_path: Path,
    runs_dir: Path,
    summary_output_path: Path,
    summary_markdown_output_path: Path,
) -> Dict[str, Any]:
    tasks = adapt_mulocbench_records(
        raw_records,
        repos_root=repos_root,
        repo_layout=repo_layout,
    )
    tasks_output_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_output_path.write_text(json.dumps(tasks, indent=2, ensure_ascii=False))

    manifest = load_skill_manifest(SKILLS_ROOT / skill_dir_name)
    runtime = SkillWorkflowRuntime(strategy=build_runtime_strategy(strategy_name))
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_records: Dict[str, Dict[str, Any]] = {}
    try:
        for task in tasks:
            task_id = str(task.get("task_id") or "").strip() or "task"
            try:
                result = runtime.run(manifest=manifest, task=task)
                task_id = str(result.get("task_id") or task_id).strip() or "task"
                run_records[task_id] = result
                output_path = runs_dir / f"{_slugify(task_id)}.json"
                output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
                _write_summary_outputs(
                    summary=_build_summary_payload(
                        tasks=tasks,
                        run_records=run_records,
                        manifest_name=manifest.name,
                        strategy_name=strategy_name,
                        tasks_output_path=tasks_output_path,
                        runs_dir=runs_dir,
                        status="running",
                    ),
                    summary_output_path=summary_output_path,
                    summary_markdown_output_path=summary_markdown_output_path,
                )
            except Exception as exc:
                _write_summary_outputs(
                    summary=_build_summary_payload(
                        tasks=tasks,
                        run_records=run_records,
                        manifest_name=manifest.name,
                        strategy_name=strategy_name,
                        tasks_output_path=tasks_output_path,
                        runs_dir=runs_dir,
                        status="failed",
                        failed_task={
                            "task_id": task_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    ),
                    summary_output_path=summary_output_path,
                    summary_markdown_output_path=summary_markdown_output_path,
                )
                raise
    finally:
        close_runtime = getattr(runtime, "close", None)
        if callable(close_runtime):
            close_runtime()

    summary = _build_summary_payload(
        tasks=tasks,
        run_records=run_records,
        manifest_name=manifest.name,
        strategy_name=strategy_name,
        tasks_output_path=tasks_output_path,
        runs_dir=runs_dir,
        status="complete",
    )
    _write_summary_outputs(
        summary=summary,
        summary_output_path=summary_output_path,
        summary_markdown_output_path=summary_markdown_output_path,
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert and run the first batch of MULocBench localization samples through the bug skill runtime."
    )
    parser.add_argument("--input", required=True, help="Path to a MULocBench JSON or JSONL export.")
    parser.add_argument(
        "--skill",
        default="bug_triage",
        help="Skill directory name under skill_workflow_runtime/skills/. Defaults to bug_triage.",
    )
    parser.add_argument("--strategy", default="default", help="Runtime strategy name.")
    parser.add_argument("--repos-root", help="Optional root containing local benchmark repositories.")
    parser.add_argument(
        "--repo-layout",
        choices=["nested", "flat", "repo_name"],
        default="nested",
        help="Repository layout under --repos-root.",
    )
    parser.add_argument("--tasks-output", help="Optional adapted-task JSON output path.")
    parser.add_argument("--runs-dir", help="Optional directory for per-task run records.")
    parser.add_argument("--summary-output", help="Optional JSON summary output path.")
    parser.add_argument("--summary-markdown-output", help="Optional Markdown summary output path.")
    parser.add_argument("--offset", type=int, default=0, help="Optional start offset into the raw benchmark export.")
    parser.add_argument("--limit", type=int, help="Optional maximum number of records to run.")
    add_local_llm_backend_args(parser)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    apply_local_llm_backend_overrides(args)

    raw_records = load_mulocbench_records(Path(args.input))
    if args.offset:
        raw_records = raw_records[args.offset :]
    if args.limit is not None:
        raw_records = raw_records[: args.limit]

    tasks_output_path = Path(args.tasks_output) if args.tasks_output else default_tasks_output_path()
    runs_dir = Path(args.runs_dir) if args.runs_dir else default_runs_dir()
    summary_output_path = Path(args.summary_output) if args.summary_output else default_summary_json_path()
    summary_markdown_output_path = (
        Path(args.summary_markdown_output)
        if args.summary_markdown_output
        else default_summary_markdown_path()
    )

    summary = run_mulocbench_localization_experiments(
        raw_records=raw_records,
        skill_dir_name=args.skill,
        repos_root=args.repos_root,
        repo_layout=args.repo_layout,
        strategy_name=args.strategy,
        tasks_output_path=tasks_output_path,
        runs_dir=runs_dir,
        summary_output_path=summary_output_path,
        summary_markdown_output_path=summary_markdown_output_path,
    )

    print("Ran MULocBench localization experiments")
    print(f"  - records: {len(raw_records)}")
    print(f"  - tasks output: {tasks_output_path}")
    print(f"  - runs dir: {runs_dir}")
    print(f"  - summary json: {summary_output_path}")
    print(f"  - summary markdown: {summary_markdown_output_path}")
    print(f"  - candidate recall (any gold): {summary['candidate_recall_rate_any_gold']:.3f}")
    print(f"  - primary-file match rate: {summary['primary_file_match_rate']:.3f}")
    print(
        "  - primary-file match rate | any gold in candidates: "
        f"{summary['primary_file_match_rate_when_any_gold_in_candidates']:.3f}"
    )


if __name__ == "__main__":
    main()
