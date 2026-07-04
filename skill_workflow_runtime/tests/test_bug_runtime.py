from __future__ import annotations

import json
import unittest
from pathlib import Path

from skill_workflow_runtime.skill_runtime.loader import load_skill_manifest
from skill_workflow_runtime.skill_runtime.runtime import SkillWorkflowRuntime


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skill_workflow_runtime" / "skills"
TASKS_ROOT = REPO_ROOT / "skill_workflow_runtime" / "tasks"


def _load_task(name: str) -> dict:
    return json.loads((TASKS_ROOT / name).read_text())


class BugSkillRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = SkillWorkflowRuntime()
        self.manifest = load_skill_manifest(SKILLS_ROOT / "bug_triage")
        self.labeled_task = _load_task("bug_triage_labeled_profile_demo.json")
        self.complex_task = _load_task("bug_triage_complex_demo.json")

    def test_bug_skill_exposes_phase_groups_and_constraints(self) -> None:
        result = self.runtime.run(manifest=self.manifest, task=self.labeled_task)

        self.assertEqual(result["input_profile"], "bug_labeled_artifacts")
        self.assertEqual(
            result["stage_groups"]["workflow_phase_groups"]["observe"],
            ["signal_extractor"],
        )
        self.assertEqual(
            result["stage_groups"]["workflow_phase_groups"]["verify"],
            ["decision_verification"],
        )
        self.assertEqual(
            result["stage_groups"]["workflow_phase_groups"]["handoff"],
            ["fix_plan"],
        )
        self.assertEqual(
            [constraint["name"] for constraint in result["constraints"]],
            ["retrieval_before_decision", "candidate_grounding", "verify_before_fix_plan"],
        )
        self.assertIn("static_lint", result)
        self.assertEqual(
            result["static_lint"]["artifact_tool_boundary"]["heavy_artifacts"],
            ["retrieved_artifacts"],
        )
        self.assertEqual(
            result["static_lint"]["artifact_tool_boundary"]["tool_contracts"],
            [
                {
                    "stage": "high_recall_retrieval",
                    "workflow_phase": "abstract",
                    "tool_mode": "read_only",
                    "tool_intent": "retrieval",
                    "post_tool_policy": "requires_compression",
                    "tools": ["bug.local_repo_search"],
                    "writes_artifacts": ["retrieved_artifacts"],
                }
            ],
        )
        self.assertEqual(
            result["static_lint"]["artifact_tool_boundary"]["suffix_retrieval_subloops"],
            [],
        )

    def test_bug_skill_verifies_supported_helper_grounded_decision(self) -> None:
        result = self.runtime.run(manifest=self.manifest, task=self.labeled_task)

        self.assertEqual(result["final_state"]["primary_file"], "app/utils.py")
        self.assertEqual(result["final_state"]["root_cause_category"], "error_recovery")
        self.assertEqual(result["final_state"]["decision_status"], "supported")
        self.assertEqual(
            result["state_provenance"]["decision_status"]["stage"],
            "decision_verification",
        )
        self.assertTrue(result["final_state"]["fix_plan"])
        self.assertEqual(
            result["static_lint"]["artifact_tool_boundary"]["heavy_tool_artifact_boundaries"],
            [
                {
                    "artifact": "retrieved_artifacts",
                    "producer_stage": "high_recall_retrieval",
                    "producer_phase": "abstract",
                    "tools": ["bug.local_repo_search"],
                    "compressed_by": ["evidence_screen"],
                }
            ],
        )
        evidence_screen_record = next(
            stage for stage in result["stages"] if stage["name"] == "evidence_screen"
        )
        retrieval_record = next(
            stage for stage in result["stages"] if stage["name"] == "high_recall_retrieval"
        )
        self.assertEqual(retrieval_record["metadata"]["tool_intent"], "retrieval")
        self.assertEqual(
            retrieval_record["metadata"]["post_tool_policy"],
            "requires_compression",
        )
        self.assertEqual(
            evidence_screen_record["artifact_read_provenance"],
            {
                "retrieved_artifacts": {
                    "stage": "high_recall_retrieval",
                    "workflow_phase": "abstract",
                    "stage_type": "retrieval",
                }
            },
        )

    def test_bug_skill_handles_more_competitive_artifact_pool(self) -> None:
        result = self.runtime.run(manifest=self.manifest, task=self.complex_task)

        self.assertIn("app/utils.py", result["final_state"]["candidate_files"])
        self.assertEqual(result["final_state"]["primary_file"], "app/utils.py")
        self.assertEqual(result["final_state"]["decision_status"], "supported")
        self.assertTrue(
            any("regression test" in step.lower() for step in result["final_state"]["fix_plan"])
        )


if __name__ == "__main__":
    unittest.main()
