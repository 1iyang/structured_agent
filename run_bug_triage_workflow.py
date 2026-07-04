#!/usr/bin/env python3
"""Run the extended local code-repository bug-triage state-transfer workflow."""

import argparse
from pathlib import Path

from structured_agent.bug_triage_workflow import BugTriageWorkflow
from structured_agent.eval import print_bug_triage_summary
from structured_agent.io import save_bug_triage_json, save_bug_triage_report
from structured_agent.schemas import SLOPolicyConfig
from structured_agent.script_utils import (
    add_common_runtime_args,
    add_slo_args,
    build_runner,
    default_artifact_output_dir,
    print_progress,
    reset_run_outputs,
    run_with_heartbeat,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the extended local code-repository bug-triage state-transfer workflow."
    )
    add_common_runtime_args(parser)
    add_slo_args(parser)
    parser.add_argument("--repo-root", type=str, required=True, help="Local repository root to inspect.")
    parser.add_argument("--issues-file", type=str, required=True, help="JSON or JSONL file containing triage issues.")
    parser.add_argument("--max-search-hits", type=int, default=20, help="Maximum raw local-search hits per issue.")
    parser.add_argument("--max-files", type=int, default=8, help="Maximum files to keep as grouped evidence.")
    parser.add_argument("--snippet-context-lines", type=int, default=6, help="Context lines around each matching line.")
    parser.add_argument(
        "--max-reretrieval-rounds",
        type=int,
        default=2,
        help="Maximum evidence-driven re-retrieval rounds before final triage.",
    )
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="Run the optional review ablation after fix_plan.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    artifact_output_dir = args.artifact_output_dir or default_artifact_output_dir(args.output)
    print_progress(
        f"Preparing bug-triage workflow for repo `{args.repo_root}` and issues `{args.issues_file}`"
    )
    reset_run_outputs(output_path, artifact_output_dir)

    runner = run_with_heartbeat(
        f"Initializing vLLM runner for model `{args.model}`",
        lambda: build_runner(args),
    )
    workflow = BugTriageWorkflow(
        runner=runner,
        max_search_hits=args.max_search_hits,
        max_files=args.max_files,
        snippet_context_lines=args.snippet_context_lines,
        include_review=args.include_review,
        max_reretrieval_rounds=args.max_reretrieval_rounds,
    )
    policy_config = SLOPolicyConfig(
        total_slo_seconds=args.slo_seconds,
        retrieval_elapsed_seconds=args.retrieval_elapsed_seconds,
        seconds_per_token=args.seconds_per_token,
        view_read_overhead_seconds=args.view_read_overhead_seconds,
        stage_safety_margin_seconds=args.stage_safety_margin_seconds,
        optional_selection_score_threshold=args.optional_selection_score_threshold,
    )

    results = run_with_heartbeat(
        "Running bug-triage workflow",
        lambda: workflow.run(
            repo_root=args.repo_root,
            issues_file=args.issues_file,
            artifact_output_dir=artifact_output_dir,
            policy_config=policy_config,
        ),
        interval_seconds=30.0,
    )

    print_progress(f"Saving workflow report to `{args.output}`")
    if output_path.suffix.lower() == ".json":
        save_bug_triage_json(args.output, args.repo_root, args.issues_file, results)
    else:
        save_bug_triage_report(args.output, args.repo_root, args.issues_file, results)
    print_bug_triage_summary(results)


if __name__ == "__main__":
    main()
