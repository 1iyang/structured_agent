from __future__ import annotations

import json
import unittest
from pathlib import Path

from skill_workflow_runtime.skill_runtime.loader import load_skill_manifest
from skill_workflow_runtime.skill_runtime.runtime import SkillWorkflowRuntime
from skill_workflow_runtime.skill_runtime.strategy import build_runtime_strategy


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skill_workflow_runtime" / "skills"
TASKS_ROOT = REPO_ROOT / "skill_workflow_runtime" / "tasks"


def _load_task(name: str) -> dict:
    return json.loads((TASKS_ROOT / name).read_text())


class SupportSkillRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = SkillWorkflowRuntime()
        self.realistic_task = _load_task("support_ticket_realistic_profile_demo.json")
        self.enterprise_task = _load_task("support_ticket_account_recovery_complex_demo.json")
        self.low_risk_task = _load_task("support_ticket_low_risk_ready_demo.json")

    def _run_skill(self, skill_dir_name: str, task: dict | None = None) -> dict:
        manifest = load_skill_manifest(SKILLS_ROOT / skill_dir_name)
        return self.runtime.run(manifest=manifest, task=task or self.realistic_task)

    def test_support_suffix_chain_uses_llm_executor(self) -> None:
        minimal_result = self._run_skill("support_ticket")
        full_result = self._run_skill("support_ticket_full_evidence")

        suffix_stage_names = [
            "eligibility_assessment",
            "resolution_plan",
            "resolution_verification",
            "response_refinement",
            "response_draft",
        ]
        minimal_suffix = [
            stage for stage in minimal_result["stages"] if stage["name"] in suffix_stage_names
        ]
        full_suffix = [
            stage for stage in full_result["stages"] if stage["name"] in suffix_stage_names
        ]

        self.assertEqual([stage["name"] for stage in minimal_suffix], suffix_stage_names)
        self.assertEqual([stage["name"] for stage in full_suffix], suffix_stage_names)
        self.assertTrue(all(stage["executor"] == "llm.json_stage" for stage in minimal_suffix))
        self.assertTrue(all(stage["executor"] == "llm.json_stage" for stage in full_suffix))
        self.assertTrue(all((stage["token_usage"]["prompt_tokens"] or 0) > 0 for stage in minimal_suffix))
        self.assertTrue(all((stage["token_usage"]["prompt_tokens"] or 0) > 0 for stage in full_suffix))
        self.assertEqual(minimal_result["skill_name"], "support-ticket-skill")
        self.assertEqual(full_result["skill_name"], "support-ticket-full-evidence-baseline")
        self.assertIn("verify", minimal_result["stage_groups"]["workflow_phase_groups"])
        self.assertEqual(
            minimal_result["stage_groups"]["workflow_phase_groups"]["verify"],
            ["resolution_verification", "response_refinement"],
        )
        self.assertEqual(
            minimal_result["static_lint"]["task_boundary"]["suffix_raw_task_reads"],
            [],
        )

    def test_full_evidence_suffix_prompt_cost_exceeds_minimal_transfer(self) -> None:
        minimal_result = self._run_skill("support_ticket")
        full_result = self._run_skill("support_ticket_full_evidence")

        self.assertGreater(
            full_result["token_summary"]["suffix_prompt_tokens"],
            minimal_result["token_summary"]["suffix_prompt_tokens"],
        )
        self.assertGreater(
            full_result["token_summary"]["input_view_tokens_total"],
            minimal_result["token_summary"]["input_view_tokens_total"],
        )

    def test_structured_minimal_transfer_preserves_operational_path(self) -> None:
        minimal_result = self._run_skill("support_ticket")
        full_result = self._run_skill("support_ticket_full_evidence")

        self.assertEqual(minimal_result["final_state"]["eligibility_decision"], "needs_info")
        self.assertEqual(full_result["final_state"]["eligibility_decision"], "needs_info")
        self.assertEqual(minimal_result["final_state"]["resolution_category"], "replacement")
        self.assertEqual(full_result["final_state"]["resolution_category"], "replacement")
        self.assertEqual(minimal_result["final_state"]["blocking_requirements"], ["customer_evidence"])
        self.assertEqual(full_result["final_state"]["blocking_requirements"], ["customer_evidence"])
        self.assertEqual(minimal_result["final_state"]["handoff_status"], "blocked")
        self.assertEqual(full_result["final_state"]["handoff_status"], "blocked")
        self.assertTrue(minimal_result["final_state"]["response_guardrails"])
        self.assertTrue(full_result["final_state"]["response_guardrails"])
        self.assertEqual(
            minimal_result["state_provenance"]["handoff_status"]["stage"],
            "resolution_verification",
        )

    def test_raw_context_baseline_has_no_anchor_groups(self) -> None:
        baseline_result = self._run_skill("support_ticket_raw_context_baseline")

        self.assertEqual(baseline_result["stage_groups"]["anchor_stages"], [])
        self.assertEqual(
            [stage["name"] for stage in baseline_result["stages"]],
            [
                "ticket_intake",
                "policy_evidence",
                "eligibility_assessment",
                "resolution_plan",
                "resolution_verification",
                "response_refinement",
                "response_draft",
            ],
        )
        self.assertTrue(all(stage["executor"] == "llm.json_stage" for stage in baseline_result["stages"]))
        self.assertGreater(baseline_result["token_summary"]["prompt_tokens_total"], 0)
        self.assertEqual(
            baseline_result["token_summary"]["prompt_tokens_total"],
            baseline_result["token_summary"]["suffix_prompt_tokens"],
        )
        self.assertIn("eligibility_decision", baseline_result["final_state"])
        self.assertIn("response_message", baseline_result["final_state"])
        self.assertIn(
            "allow_suffix_raw_task_reads",
            baseline_result["static_lint"]["declared_exception_constraints"],
        )

    def test_complex_enterprise_case_hits_escalation_and_verify_path(self) -> None:
        result = self._run_skill("support_ticket", task=self.enterprise_task)

        self.assertEqual(result["final_state"]["issue_type"], "account_access")
        self.assertEqual(result["final_state"]["preferred_resolution_path"], "account_recovery")
        self.assertEqual(result["final_state"]["eligibility_decision"], "escalate")
        self.assertEqual(result["final_state"]["resolution_category"], "escalation")
        self.assertEqual(result["final_state"]["handoff_status"], "escalate")
        self.assertIn("identity_verification_required", result["final_state"]["policy_signals"])
        self.assertIn("internal_review", result["final_state"]["blocking_requirements"])
        self.assertTrue(
            any("specialist" in item.lower() for item in result["final_state"]["response_guardrails"])
        )

    def test_support_budget_policy_opens_refinement_for_complex_state(self) -> None:
        runtime = SkillWorkflowRuntime(
            strategy=build_runtime_strategy("support-response-budget-policy")
        )
        manifest = load_skill_manifest(SKILLS_ROOT / "support_ticket")
        result = runtime.run(manifest=manifest, task=self.realistic_task)

        refinement_record = next(
            stage for stage in result["stages"] if stage["name"] == "response_refinement"
        )
        self.assertFalse(refinement_record.get("skipped"))
        self.assertEqual(
            refinement_record["decision"]["reason"],
            "policy_open_support_refinement",
        )
        self.assertEqual(
            refinement_record["strategy_context"]["estimated_optional_branch_prompt_tokens_metadata"],
            120,
        )
        self.assertGreater(
            refinement_record["strategy_context"]["estimated_optional_branch_prompt_tokens_runtime"],
            0,
        )
        self.assertIn("refinement_focus", result["final_state"])
        self.assertTrue(result["final_state"]["refined_response_notes"])

    def test_support_budget_policy_skips_refinement_for_low_risk_ready_case(self) -> None:
        runtime = SkillWorkflowRuntime(
            strategy=build_runtime_strategy("support-response-budget-policy")
        )
        manifest = load_skill_manifest(SKILLS_ROOT / "support_ticket")
        result = runtime.run(manifest=manifest, task=self.low_risk_task)

        refinement_record = next(
            stage for stage in result["stages"] if stage["name"] == "response_refinement"
        )
        self.assertTrue(refinement_record.get("skipped"))
        self.assertEqual(
            refinement_record["decision"]["reason"],
            "policy_skip_support_refinement_state",
        )
        self.assertEqual(result["final_state"]["eligibility_decision"], "approved")
        self.assertEqual(result["final_state"]["handoff_status"], "ready")

    def test_support_budget_policy_crossover_budget_opens_minimal_but_closes_full_and_raw(self) -> None:
        strategy_name = "support-response-budget-policy"

        def run_skill(skill_dir_name: str, prompt_budget_threshold: int | None = None) -> dict:
            runtime = SkillWorkflowRuntime(
                strategy=build_runtime_strategy(
                    strategy_name,
                    prompt_budget_threshold=prompt_budget_threshold,
                )
            )
            manifest = load_skill_manifest(SKILLS_ROOT / skill_dir_name)
            return runtime.run(manifest=manifest, task=self.realistic_task)

        no_budget_results = {
            skill: run_skill(skill)
            for skill in [
                "support_ticket",
                "support_ticket_full_evidence",
                "support_ticket_raw_context_baseline",
            ]
        }

        required_thresholds = {}
        for skill_name, result in no_budget_results.items():
            refinement_record = next(
                stage for stage in result["stages"] if stage["name"] == "response_refinement"
            )
            self.assertEqual(
                refinement_record["decision"]["reason"],
                "policy_open_support_refinement",
            )
            required_thresholds[skill_name] = (
                refinement_record["strategy_context"]["consumed_prompt_tokens_before_stage"]
                + refinement_record["strategy_context"]["estimated_optional_branch_prompt_tokens"]
            )

        crossover_budget = min(
            required_thresholds["support_ticket_full_evidence"],
            required_thresholds["support_ticket_raw_context_baseline"],
        ) - 1
        self.assertGreaterEqual(crossover_budget, required_thresholds["support_ticket"])

        minimal_budgeted = run_skill("support_ticket", prompt_budget_threshold=crossover_budget)
        full_budgeted = run_skill("support_ticket_full_evidence", prompt_budget_threshold=crossover_budget)
        raw_budgeted = run_skill("support_ticket_raw_context_baseline", prompt_budget_threshold=crossover_budget)

        minimal_refinement = next(
            stage for stage in minimal_budgeted["stages"] if stage["name"] == "response_refinement"
        )
        full_refinement = next(
            stage for stage in full_budgeted["stages"] if stage["name"] == "response_refinement"
        )
        raw_refinement = next(
            stage for stage in raw_budgeted["stages"] if stage["name"] == "response_refinement"
        )

        self.assertFalse(minimal_refinement.get("skipped"))
        self.assertEqual(minimal_refinement["decision"]["reason"], "policy_open_support_refinement")
        self.assertTrue(full_refinement.get("skipped"))
        self.assertEqual(full_refinement["decision"]["reason"], "policy_skip_support_refinement_budget")
        self.assertTrue(raw_refinement.get("skipped"))
        self.assertEqual(raw_refinement["decision"]["reason"], "policy_skip_support_refinement_budget")


if __name__ == "__main__":
    unittest.main()
