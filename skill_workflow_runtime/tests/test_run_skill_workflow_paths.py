from __future__ import annotations

import unittest
from pathlib import Path

from skill_workflow_runtime.run_skill_workflow import (
    PROJECT_ROOT,
    default_run_record_path,
    resolve_output_path,
)


class RunSkillWorkflowPathTest(unittest.TestCase):
    def test_default_run_record_path_is_project_local_and_stable(self) -> None:
        path = default_run_record_path("support-ticket-skill", "damaged-headphones-replacement")

        self.assertEqual(
            path,
            PROJECT_ROOT / "runs" / "skill_runtime" / "support-ticket-skill" / "damaged-headphones-replacement.json",
        )

    def test_default_run_record_path_includes_strategy_and_budget_when_present(self) -> None:
        path = default_run_record_path(
            "bug-triage-suffix-reretrieval-demo",
            "json-tail-truncation",
            strategy_name="suffix-reretrieval-policy",
            strategy_budget_threshold=1200,
        )

        self.assertEqual(
            path,
            PROJECT_ROOT
            / "runs"
            / "skill_runtime"
            / "bug-triage-suffix-reretrieval-demo"
            / "json-tail-truncation__strategy-suffix-reretrieval-policy__budget-1200.json",
        )

    def test_resolve_output_path_preserves_explicit_override(self) -> None:
        explicit = "tmp/custom/output.json"

        path = resolve_output_path(explicit, "support-ticket-skill", "damaged-headphones-replacement")

        self.assertEqual(path, Path(explicit))


if __name__ == "__main__":
    unittest.main()
