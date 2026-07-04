from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from skill_runtime.benchmark_registry import (
        build_benchmark_validation_plan,
        render_benchmark_validation_markdown,
    )
except ModuleNotFoundError:
    from skill_workflow_runtime.skill_runtime.benchmark_registry import (
        build_benchmark_validation_plan,
        render_benchmark_validation_markdown,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def default_json_path() -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / "benchmark_plan"
        / "benchmark_validation_plan.json"
    )


def default_markdown_path() -> Path:
    return (
        PROJECT_ROOT
        / "runs"
        / "skill_runtime"
        / "benchmark_plan"
        / "benchmark_validation_plan.md"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the benchmark validation plan for the skill workflow runtime."
    )
    parser.add_argument(
        "--output",
        help="Optional JSON output path. Defaults to runs/skill_runtime/benchmark_plan/benchmark_validation_plan.json",
    )
    parser.add_argument(
        "--markdown-output",
        help="Optional Markdown output path. Defaults to runs/skill_runtime/benchmark_plan/benchmark_validation_plan.md",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = build_benchmark_validation_plan()
    json_path = Path(args.output) if args.output else default_json_path()
    markdown_path = Path(args.markdown_output) if args.markdown_output else default_markdown_path()

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_benchmark_validation_markdown(plan))

    print("Exported benchmark validation plan")
    print(f"  - JSON: {json_path}")
    print(f"  - Markdown: {markdown_path}")


if __name__ == "__main__":
    main()
