from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_records(path: Path) -> List[Dict[str, Any]]:
    parsed = json.loads(path.read_text())
    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return parsed


def _load_run_records(runs_dir: Path) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for path in sorted(runs_dir.rglob("*.json")):
        try:
            parsed = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        task_id = str(parsed.get("task_id") or "").strip()
        if task_id:
            records[task_id] = parsed
    return records


def _safe_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _task_row(task: Dict[str, Any], run: Dict[str, Any] | None) -> Dict[str, Any]:
    gold_primary_file = str(task.get("gold_primary_file") or "").strip()
    gold_localized_files = _safe_list(task.get("gold_localized_files"))
    final_state = run.get("final_state", {}) if isinstance(run, dict) else {}
    candidate_files = _safe_list(final_state.get("candidate_files"))
    predicted_primary_file = str(final_state.get("primary_file") or "").strip()
    any_gold_in_candidates = any(path in candidate_files for path in gold_localized_files)
    primary_matches_gold = bool(predicted_primary_file and predicted_primary_file == gold_primary_file)
    primary_matches_any_gold = bool(predicted_primary_file and predicted_primary_file in gold_localized_files)
    return {
        "task_id": task.get("task_id"),
        "gold_primary_file": gold_primary_file,
        "gold_localized_files": gold_localized_files,
        "candidate_files": candidate_files,
        "predicted_primary_file": predicted_primary_file,
        "any_gold_in_candidates": any_gold_in_candidates,
        "primary_matches_gold": primary_matches_gold,
        "primary_matches_any_gold": primary_matches_any_gold,
        "run_found": run is not None,
    }


def summarize_mulocbench_runs(tasks: List[Dict[str, Any]], run_records: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    rows = [_task_row(task, run_records.get(str(task.get("task_id") or "").strip())) for task in tasks]
    matched_rows = [row for row in rows if row["run_found"]]
    rows_with_gold = [row for row in matched_rows if row["gold_primary_file"]]
    rows_with_candidates = [row for row in matched_rows if row["any_gold_in_candidates"]]

    def _rate(items: List[Dict[str, Any]], field: str) -> float:
        if not items:
            return 0.0
        return sum(1.0 for item in items if item[field]) / float(len(items))

    return {
        "total_tasks": len(tasks),
        "tasks_with_runs": len(matched_rows),
        "tasks_with_gold_primary_file": len(rows_with_gold),
        "candidate_recall_rate_any_gold": _rate(matched_rows, "any_gold_in_candidates"),
        "primary_file_match_rate": _rate(rows_with_gold, "primary_matches_gold"),
        "primary_file_match_rate_when_any_gold_in_candidates": _rate(rows_with_candidates, "primary_matches_gold"),
        "primary_file_any_gold_match_rate": _rate(matched_rows, "primary_matches_any_gold"),
        "rows": rows,
    }


def render_mulocbench_summary_markdown(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# MULocBench Localization Summary")
    lines.append("")
    lines.append(f"- status: `{summary.get('status', 'complete')}`")
    if summary.get("failed_task"):
        failed_task = summary["failed_task"]
        lines.append(f"- failed task: `{failed_task.get('task_id', '')}`")
        if failed_task.get("error"):
            lines.append(f"- failure: `{failed_task['error']}`")
    lines.append(f"- total tasks: `{summary['total_tasks']}`")
    lines.append(f"- tasks with runs: `{summary['tasks_with_runs']}`")
    lines.append(f"- tasks with gold primary file: `{summary['tasks_with_gold_primary_file']}`")
    lines.append(f"- candidate recall (any gold): `{summary['candidate_recall_rate_any_gold']:.3f}`")
    lines.append(f"- primary-file match rate: `{summary['primary_file_match_rate']:.3f}`")
    lines.append(
        "- primary-file match rate | any gold in candidates: "
        f"`{summary['primary_file_match_rate_when_any_gold_in_candidates']:.3f}`"
    )
    lines.append(
        f"- primary-file any-gold match rate: `{summary['primary_file_any_gold_match_rate']:.3f}`"
    )
    lines.append("")
    lines.append(
        "| task_id | gold_primary_file | predicted_primary_file | any_gold_in_candidates | primary_matches_gold | primary_matches_any_gold |"
    )
    lines.append("|---|---|---|---:|---:|---:|")
    for row in summary["rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["task_id"] or ""),
                    str(row["gold_primary_file"] or ""),
                    str(row["predicted_primary_file"] or ""),
                    "yes" if row["any_gold_in_candidates"] else "no",
                    "yes" if row["primary_matches_gold"] else "no",
                    "yes" if row["primary_matches_any_gold"] else "no",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize MULocBench bug-localization run records.")
    parser.add_argument("--task-file", required=True, help="Adapted MULocBench task list JSON.")
    parser.add_argument("--runs-dir", required=True, help="Directory containing run_skill_workflow JSON outputs.")
    parser.add_argument("--output", help="Optional JSON summary output path.")
    parser.add_argument("--markdown-output", help="Optional Markdown summary output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    tasks = _load_records(Path(args.task_file))
    run_records = _load_run_records(Path(args.runs_dir))
    summary = summarize_mulocbench_runs(tasks, run_records)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.markdown_output:
        markdown_path = Path(args.markdown_output)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_mulocbench_summary_markdown(summary))

    print("MULocBench localization summary")
    print(f"  - total tasks: {summary['total_tasks']}")
    print(f"  - tasks with runs: {summary['tasks_with_runs']}")
    print(f"  - candidate recall (any gold): {summary['candidate_recall_rate_any_gold']:.3f}")
    print(f"  - primary-file match rate: {summary['primary_file_match_rate']:.3f}")
    print(
        "  - primary-file match rate | any gold in candidates: "
        f"{summary['primary_file_match_rate_when_any_gold_in_candidates']:.3f}"
    )
    print(f"  - primary-file any-gold match rate: {summary['primary_file_any_gold_match_rate']:.3f}")


if __name__ == "__main__":
    main()
