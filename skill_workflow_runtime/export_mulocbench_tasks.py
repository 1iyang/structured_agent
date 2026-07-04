from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from benchmark_adapters.mulocbench import adapt_mulocbench_records, load_mulocbench_records
except ModuleNotFoundError:
    from skill_workflow_runtime.benchmark_adapters.mulocbench import (
        adapt_mulocbench_records,
        load_mulocbench_records,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a MULocBench export into bug-skill task records."
    )
    parser.add_argument("--input", required=True, help="Path to a MULocBench JSON or JSONL export.")
    parser.add_argument("--output", required=True, help="Path to write the adapted task list JSON.")
    parser.add_argument(
        "--repos-root",
        help=(
            "Optional root directory that contains local benchmark repositories. "
            "The adapter writes repo_subpaths that point into this root."
        ),
    )
    parser.add_argument(
        "--repo-layout",
        choices=["nested", "flat", "repo_name"],
        default="nested",
        help=(
            "How repositories are laid out under --repos-root. "
            "`nested` -> <root>/<organization>/<repo>, `flat` -> <root>/<organization>__<repo>, "
            "`repo_name` -> <root>/<repo>."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of records to convert.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_mulocbench_records(Path(args.input))
    if args.limit is not None:
        records = records[: args.limit]
    tasks = adapt_mulocbench_records(
        records,
        repos_root=args.repos_root,
        repo_layout=args.repo_layout,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(tasks, indent=2, ensure_ascii=False))

    print("Exported MULocBench tasks")
    print(f"  - records: {len(records)}")
    print(f"  - output: {output_path}")
    if tasks:
        sample = tasks[0]
        print(f"  - sample task_id: {sample.get('task_id')}")
        print(f"  - sample repo_subpaths: {sample.get('repo_subpaths')}")
        print(f"  - sample gold_primary_file: {sample.get('gold_primary_file')}")


if __name__ == "__main__":
    main()
