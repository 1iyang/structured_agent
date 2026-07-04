#!/usr/bin/env python3
"""Run the mixed-corpus support ticket state-transfer workflow."""

import argparse
from pathlib import Path

from structured_agent.eval import print_support_ticket_summary
from structured_agent.io import save_support_ticket_json, save_support_ticket_report
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
from structured_agent.support_ticket_workflow import SupportTicketWorkflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the mixed-corpus support ticket state-transfer workflow."
    )
    add_common_runtime_args(parser)
    add_slo_args(parser)
    parser.add_argument(
        "--tickets-file",
        type=str,
        required=True,
        help="JSON or JSONL file containing support tickets.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    artifact_output_dir = args.artifact_output_dir or default_artifact_output_dir(args.output)
    print_progress(f"Preparing support ticket workflow for tickets file `{args.tickets_file}`")
    reset_run_outputs(output_path, artifact_output_dir)

    runner = run_with_heartbeat(
        f"Initializing vLLM runner for model `{args.model}`",
        lambda: build_runner(args),
    )
    workflow = SupportTicketWorkflow(runner=runner)
    policy_config = SLOPolicyConfig(
        total_slo_seconds=args.slo_seconds,
        retrieval_elapsed_seconds=args.retrieval_elapsed_seconds,
        seconds_per_token=args.seconds_per_token,
        view_read_overhead_seconds=args.view_read_overhead_seconds,
        stage_safety_margin_seconds=args.stage_safety_margin_seconds,
        optional_selection_score_threshold=args.optional_selection_score_threshold,
    )

    results = run_with_heartbeat(
        "Running support ticket workflow",
        lambda: workflow.run(
            tickets_file=args.tickets_file,
            artifact_output_dir=artifact_output_dir,
            policy_config=policy_config,
        ),
        interval_seconds=30.0,
    )

    print_progress(f"Saving workflow report to `{args.output}`")
    if output_path.suffix.lower() == ".json":
        save_support_ticket_json(args.output, args.tickets_file, results)
    else:
        save_support_ticket_report(args.output, args.tickets_file, results)
    print_support_ticket_summary(results)


if __name__ == "__main__":
    main()
