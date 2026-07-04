from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import skill_workflow_runtime.run_support_budget_experiments as support_budget_module
from skill_workflow_runtime.run_support_budget_experiments import (
    run_support_budget_experiments,
)


class SupportBudgetExperimentsTest(unittest.TestCase):
    def test_realistic_task_produces_crossover_budget(self) -> None:
        summary = run_support_budget_experiments(
            task_names=["support_ticket_realistic_profile_demo.json"],
            skill_names=[
                "support_ticket",
                "support_ticket_full_evidence",
                "support_ticket_raw_context_baseline",
            ],
            strategy_name="support-response-budget-policy",
        )

        task_summary = summary["tasks"][0]
        self.assertIsNotNone(task_summary["budget_scenarios"]["crossover"])

        by_skill = {record["skill_name"]: record for record in task_summary["skills"]}
        self.assertEqual(
            by_skill["support_ticket"]["state_only"]["decision_reason"],
            "policy_open_support_refinement",
        )
        self.assertEqual(
            by_skill["support_ticket"]["budget_runs"]["crossover"]["decision_reason"],
            "policy_open_support_refinement",
        )
        self.assertEqual(
            by_skill["support_ticket_full_evidence"]["budget_runs"]["crossover"]["decision_reason"],
            "policy_skip_support_refinement_budget",
        )
        self.assertEqual(
            by_skill["support_ticket_raw_context_baseline"]["budget_runs"]["crossover"]["decision_reason"],
            "policy_skip_support_refinement_budget",
        )

    def test_low_risk_task_skips_optional_refinement_by_state(self) -> None:
        summary = run_support_budget_experiments(
            task_names=["support_ticket_low_risk_ready_demo.json"],
            skill_names=[
                "support_ticket",
                "support_ticket_full_evidence",
                "support_ticket_raw_context_baseline",
            ],
            strategy_name="support-response-budget-policy",
        )

        task_summary = summary["tasks"][0]
        self.assertIsNone(task_summary["budget_scenarios"]["crossover"])

        for record in task_summary["skills"]:
            self.assertEqual(
                record["state_only"]["decision_reason"],
                "policy_skip_support_refinement_state",
            )

    def test_experiments_reuse_one_backend_and_close_it_once(self) -> None:
        class DummyBackend:
            def __init__(self) -> None:
                self.close_calls = 0

            def close(self) -> None:
                self.close_calls += 1

        backend = DummyBackend()
        seen_backends = []

        def fake_run_support_skill(
            *,
            skill_dir_name,
            task,
            strategy_name,
            prompt_budget_threshold,
            llm_backend=None,
        ):
            seen_backends.append(llm_backend)
            return {
                "skill_name": skill_dir_name,
                "task_id": task["task_id"],
                "optional_stage": "response_refinement",
                "decision_reason": "policy_open_support_refinement",
                "decision_notes": [],
                "skipped": False,
                "required_prompt_budget": 100,
                "consumed_prompt_tokens_before_stage": 80,
                "estimated_branch_prompt_tokens_runtime": 20,
                "estimated_branch_prompt_tokens_metadata": 20,
                "estimated_branch_prompt_tokens": 20,
                "suffix_prompt_tokens": 50,
                "prompt_tokens_total": 100,
                "input_view_tokens_total": 90,
                "final_state": {
                    "issue_type": "damaged_item",
                    "eligibility_decision": "needs_info",
                    "resolution_category": "replacement",
                    "handoff_status": "blocked",
                    "refinement_focus": ["blocker_clarity"],
                },
            }

        with (
            patch.object(support_budget_module, "build_default_json_backend", return_value=backend),
            patch.object(support_budget_module, "_load_task", return_value={"task_id": "demo-task"}),
            patch.object(support_budget_module, "_run_support_skill", side_effect=fake_run_support_skill),
            patch.object(support_budget_module, "_summarize_run", side_effect=lambda result: result),
        ):
            summary = run_support_budget_experiments(
                task_names=["demo.json"],
                skill_names=["support_ticket"],
                strategy_name="support-response-budget-policy",
            )

        self.assertEqual(summary["tasks"][0]["task_id"], "demo-task")
        self.assertTrue(seen_backends)
        self.assertTrue(all(item is backend for item in seen_backends))
        self.assertEqual(backend.close_calls, 1)

    def test_partial_summary_is_written_before_failure(self) -> None:
        class DummyBackend:
            def __init__(self) -> None:
                self.close_calls = 0

            def close(self) -> None:
                self.close_calls += 1

        backend = DummyBackend()

        def fake_run_support_skill(
            *,
            skill_dir_name,
            task,
            strategy_name,
            prompt_budget_threshold,
            llm_backend=None,
        ):
            if task["task_id"] == "task-two":
                raise RuntimeError("simulated failure")
            return {
                "skill_name": skill_dir_name,
                "task_id": task["task_id"],
                "optional_stage": "response_refinement",
                "decision_reason": "policy_open_support_refinement",
                "decision_notes": [],
                "skipped": False,
                "required_prompt_budget": 100,
                "consumed_prompt_tokens_before_stage": 80,
                "estimated_branch_prompt_tokens_runtime": 20,
                "estimated_branch_prompt_tokens_metadata": 20,
                "estimated_branch_prompt_tokens": 20,
                "suffix_prompt_tokens": 50,
                "prompt_tokens_total": 100,
                "input_view_tokens_total": 90,
                "final_state": {
                    "issue_type": "damaged_item",
                    "eligibility_decision": "needs_info",
                    "resolution_category": "replacement",
                    "handoff_status": "blocked",
                    "refinement_focus": ["blocker_clarity"],
                },
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "summary.json"
            markdown_path = Path(temp_dir) / "summary.md"

            with (
                patch.object(
                    support_budget_module,
                    "build_default_json_backend",
                    return_value=backend,
                ),
                patch.object(
                    support_budget_module,
                    "_load_task",
                    side_effect=[
                        {"task_id": "task-one"},
                        {"task_id": "task-two"},
                    ],
                ),
                patch.object(
                    support_budget_module,
                    "_run_support_skill",
                    side_effect=fake_run_support_skill,
                ),
                patch.object(
                    support_budget_module,
                    "_summarize_run",
                    side_effect=lambda result: result,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                    run_support_budget_experiments(
                        task_names=["task-one.json", "task-two.json"],
                        skill_names=["support_ticket"],
                        strategy_name="support-response-budget-policy",
                        output_path=output_path,
                        markdown_output_path=markdown_path,
                    )

            self.assertTrue(output_path.exists())
            self.assertTrue(markdown_path.exists())
            persisted = json.loads(output_path.read_text())
            self.assertEqual(persisted["status"], "failed")
            self.assertEqual(persisted["completed_tasks"], 1)
            self.assertEqual(persisted["tasks"][0]["task_id"], "task-one")
            self.assertEqual(persisted["failed_task"]["task_id"], "task-two")
            self.assertIn("RuntimeError: simulated failure", persisted["failed_task"]["error"])
            self.assertEqual(backend.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
