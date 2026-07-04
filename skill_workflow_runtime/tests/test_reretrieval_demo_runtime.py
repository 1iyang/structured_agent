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


class SuffixReretrievalDemoRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_skill_manifest(SKILLS_ROOT / "bug_triage_suffix_reretrieval_demo")
        self.open_task = json.loads(
            (TASKS_ROOT / "bug_triage_suffix_reretrieval_demo.json").read_text()
        )
        self.skip_task = json.loads(
            (TASKS_ROOT / "bug_triage_suffix_reretrieval_skip_demo.json").read_text()
        )

    def test_positive_suffix_reretrieval_demo_runs_end_to_end(self) -> None:
        runtime = SkillWorkflowRuntime()
        result = runtime.run(manifest=self.manifest, task=self.open_task)

        self.assertEqual(result["input_profile"], "reretrieval_demo")
        self.assertEqual(
            result["stage_groups"]["suffix_stages"],
            [
                "followup_query_expansion",
                "suffix_retrieval_refresh",
                "followup_evidence_compression",
                "followup_decision",
                "handoff_plan",
            ],
        )
        self.assertEqual(result["final_state"]["primary_file"], "app/utils.py")
        self.assertEqual(result["final_state"]["decision_status"], "supported")
        self.assertEqual(result["final_state"]["decision_source"], "refreshed_evidence")
        self.assertEqual(
            result["final_state"]["refined_candidates"][0],
            "app/utils.py",
        )
        self.assertIn(
            "Inspect and patch app/utils.py.",
            result["final_state"]["next_actions"],
        )
        self.assertEqual(
            result["static_lint"]["artifact_tool_boundary"]["suffix_retrieval_subloops"],
            [
                {
                    "tool_stage": "suffix_retrieval_refresh",
                    "tool_phase": "execute",
                    "post_tool_policy": "requires_compression",
                    "artifacts": ["followup_retrieved_artifacts"],
                    "compression_stage": "followup_evidence_compression",
                    "compression_phase": "execute",
                }
            ],
        )

        suffix_tool_record = next(
            stage for stage in result["stages"] if stage["name"] == "suffix_retrieval_refresh"
        )
        self.assertEqual(suffix_tool_record["metadata"]["tool_intent"], "re_retrieval")
        self.assertEqual(
            suffix_tool_record["metadata"]["post_tool_policy"],
            "requires_compression",
        )

        compression_record = next(
            stage for stage in result["stages"] if stage["name"] == "followup_evidence_compression"
        )
        self.assertEqual(
            compression_record["artifact_read_provenance"],
            {
                "followup_retrieved_artifacts": {
                    "stage": "suffix_retrieval_refresh",
                    "workflow_phase": "execute",
                    "stage_type": "retrieval",
                }
            },
        )

    def test_strategy_branch_opens_suffix_reretrieval_when_followup_queries_exist(self) -> None:
        runtime = SkillWorkflowRuntime(
            strategy=build_runtime_strategy("suffix-reretrieval-policy")
        )
        result = runtime.run(manifest=self.manifest, task=self.open_task)

        suffix_tool_record = next(
            stage for stage in result["stages"] if stage["name"] == "suffix_retrieval_refresh"
        )
        compression_record = next(
            stage for stage in result["stages"] if stage["name"] == "followup_evidence_compression"
        )
        self.assertFalse(suffix_tool_record.get("skipped"))
        self.assertFalse(compression_record.get("skipped"))
        self.assertEqual(suffix_tool_record["decision"]["reason"], "policy_open_suffix_reretrieval")
        self.assertEqual(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens_metadata"],
            380,
        )
        self.assertGreater(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens"],
            0,
        )
        self.assertGreater(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens_runtime"],
            0,
        )
        self.assertGreaterEqual(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens"],
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens_runtime"],
        )
        self.assertGreaterEqual(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens"],
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens_metadata"],
        )
        self.assertEqual(result["final_state"]["decision_source"], "refreshed_evidence")
        self.assertEqual(result["final_state"]["primary_file"], "app/utils.py")
        self.assertIsNone(result["strategy_config"]["prompt_budget_threshold"])
        self.assertEqual(
            result["strategy_config"]["branch_cost_model"],
            "max(runtime_estimator, skill_metadata_hint)",
        )

    def test_strategy_branch_skips_suffix_reretrieval_when_base_evidence_is_enough(self) -> None:
        runtime = SkillWorkflowRuntime(
            strategy=build_runtime_strategy("suffix-reretrieval-policy")
        )
        result = runtime.run(manifest=self.manifest, task=self.skip_task)

        suffix_tool_record = next(
            stage for stage in result["stages"] if stage["name"] == "suffix_retrieval_refresh"
        )
        compression_record = next(
            stage for stage in result["stages"] if stage["name"] == "followup_evidence_compression"
        )
        self.assertTrue(suffix_tool_record.get("skipped"))
        self.assertTrue(compression_record.get("skipped"))
        self.assertEqual(suffix_tool_record["decision"]["reason"], "policy_skip_suffix_reretrieval")
        self.assertEqual(compression_record["decision"]["reason"], "policy_skip_optional_compression")
        self.assertEqual(result["final_state"]["decision_source"], "base_evidence")
        self.assertEqual(result["final_state"]["primary_file"], "app/utils.py")
        self.assertEqual(result["final_state"]["decision_status"], "supported")

    def test_strategy_branch_opens_with_sufficient_budget_threshold(self) -> None:
        runtime = SkillWorkflowRuntime(
            strategy=build_runtime_strategy(
                "suffix-reretrieval-policy",
                prompt_budget_threshold=5000,
            )
        )
        result = runtime.run(manifest=self.manifest, task=self.open_task)

        suffix_tool_record = next(
            stage for stage in result["stages"] if stage["name"] == "suffix_retrieval_refresh"
        )
        self.assertFalse(suffix_tool_record.get("skipped"))
        self.assertEqual(suffix_tool_record["decision"]["reason"], "policy_open_suffix_reretrieval")
        self.assertEqual(result["strategy_config"]["prompt_budget_threshold"], 5000)
        self.assertEqual(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens_metadata"],
            380,
        )
        self.assertGreater(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens"],
            0,
        )
        self.assertEqual(result["final_state"]["decision_source"], "refreshed_evidence")

    def test_strategy_branch_skips_when_budget_threshold_is_exhausted(self) -> None:
        runtime = SkillWorkflowRuntime(
            strategy=build_runtime_strategy(
                "suffix-reretrieval-policy",
                prompt_budget_threshold=1,
            )
        )
        result = runtime.run(manifest=self.manifest, task=self.open_task)

        suffix_tool_record = next(
            stage for stage in result["stages"] if stage["name"] == "suffix_retrieval_refresh"
        )
        compression_record = next(
            stage for stage in result["stages"] if stage["name"] == "followup_evidence_compression"
        )
        self.assertTrue(suffix_tool_record.get("skipped"))
        self.assertTrue(compression_record.get("skipped"))
        self.assertEqual(
            suffix_tool_record["decision"]["reason"],
            "policy_skip_suffix_reretrieval_budget",
        )
        self.assertGreater(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens"],
            0,
        )
        self.assertEqual(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens_metadata"],
            380,
        )
        self.assertEqual(result["strategy_config"]["prompt_budget_threshold"], 1)
        self.assertEqual(result["final_state"]["decision_source"], "base_evidence")

    def test_strategy_branch_can_be_closed_by_metadata_hint_even_when_runtime_estimate_is_smaller(self) -> None:
        open_result = SkillWorkflowRuntime(
            strategy=build_runtime_strategy("suffix-reretrieval-policy")
        ).run(manifest=self.manifest, task=self.open_task)

        open_suffix_tool_record = next(
            stage for stage in open_result["stages"] if stage["name"] == "suffix_retrieval_refresh"
        )
        runtime_estimate = open_suffix_tool_record["strategy_context"][
            "estimated_optional_branch_prompt_tokens_runtime"
        ]
        metadata_hint = open_suffix_tool_record["strategy_context"][
            "estimated_optional_branch_prompt_tokens_metadata"
        ]
        consumed_before_stage = open_suffix_tool_record["strategy_context"][
            "consumed_prompt_tokens_before_stage"
        ]
        self.assertLess(runtime_estimate, metadata_hint)

        runtime = SkillWorkflowRuntime(
            strategy=build_runtime_strategy(
                "suffix-reretrieval-policy",
                prompt_budget_threshold=consumed_before_stage + metadata_hint - 1,
            )
        )
        result = runtime.run(manifest=self.manifest, task=self.open_task)

        suffix_tool_record = next(
            stage for stage in result["stages"] if stage["name"] == "suffix_retrieval_refresh"
        )
        self.assertTrue(suffix_tool_record.get("skipped"))
        self.assertEqual(
            suffix_tool_record["decision"]["reason"],
            "policy_skip_suffix_reretrieval_budget",
        )
        self.assertEqual(
            suffix_tool_record["strategy_context"]["estimated_optional_branch_prompt_tokens_metadata"],
            metadata_hint,
        )


if __name__ == "__main__":
    unittest.main()
