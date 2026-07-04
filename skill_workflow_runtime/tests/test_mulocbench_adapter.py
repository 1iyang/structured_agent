from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skill_workflow_runtime.benchmark_adapters.mulocbench import (
    adapt_mulocbench_record,
    adapt_mulocbench_records,
)
from skill_workflow_runtime.skill_runtime.loader import load_skill_manifest
from skill_workflow_runtime.skill_runtime.runtime import SkillWorkflowRuntime
from skill_workflow_runtime.summarize_mulocbench_runs import summarize_mulocbench_runs


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skill_workflow_runtime" / "skills"


class MULocBenchAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.record = {
            "organization": "octo",
            "repo_name": "demo-repo",
            "issue_number": 17,
            "title": "JSON tail recovery fails on truncated output",
            "body": (
                "Long structured generations can fail with JSONDecodeError during recovery.\n"
                "Traceback:\n"
                "File \"app/engine.py\", line 83, in generate_json\n"
                "File \"app/utils.py\", line 141, in safe_json_loads\n"
                "The helper should recover a valid JSON prefix instead of crashing."
            ),
            "iss_html_url": "https://example.com/octo/demo-repo/issues/17",
            "iss_label": "bug",
            "iss_has_pr": True,
            "base_commit": "abc123",
            "loctype": {
                "code": ["app/utils.py"],
                "test": ["tests/test_utils.py"],
            },
            "file_loc": {
                "files": [
                    {"path": "app/utils.py"},
                    {"path": "tests/test_utils.py"},
                ]
            },
        }

    def test_adapter_extracts_gold_files_and_repo_path(self) -> None:
        adapted = adapt_mulocbench_record(
            self.record,
            repos_root="/tmp/mulocbench_repos",
            repo_layout="nested",
        )

        self.assertEqual(adapted["source_dataset"], "MULocBench")
        self.assertEqual(adapted["gold_primary_file"], "app/utils.py")
        self.assertEqual(
            adapted["gold_localized_files"],
            ["app/utils.py", "tests/test_utils.py"],
        )
        self.assertEqual(
            adapted["repo_subpaths"],
            ["/tmp/mulocbench_repos/octo/demo-repo"],
        )
        self.assertIn("JSONDecodeError", adapted["log_excerpt"])
        self.assertIn('File "app/utils.py"', adapted["stacktrace_excerpt"])

    def test_adapter_to_runtime_chain_localizes_helper_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repos_root = Path(temp_dir) / "repos"
            repo_path = repos_root / "octo" / "demo-repo"
            (repo_path / "app").mkdir(parents=True)
            (repo_path / "tests").mkdir(parents=True)
            (repo_path / "app" / "engine.py").write_text(
                "def generate_json(raw_text):\n"
                "    # generate_json forwards the payload to safe_json_loads after generation.\n"
                "    return safe_json_loads(raw_text)\n"
            )
            (repo_path / "app" / "utils.py").write_text(
                "def safe_json_loads(raw_text):\n"
                "    # Conservative JSONDecodeError recovery for truncated output tails.\n"
                "    # dangling comma recovery and valid JSON prefix trimming live here.\n"
                "    return raw_text\n"
            )
            (repo_path / "tests" / "test_utils.py").write_text(
                "def test_safe_json_loads_tail_recovery():\n"
                "    assert True\n"
            )

            adapted = adapt_mulocbench_record(
                self.record,
                repos_root=str(repos_root),
                repo_layout="nested",
            )
            runtime = SkillWorkflowRuntime()
            manifest = load_skill_manifest(SKILLS_ROOT / "bug_triage")
            result = runtime.run(manifest=manifest, task=adapted)

            self.assertEqual(result["input_profile"], "bug_generic")
            self.assertIn("app/utils.py", result["final_state"]["candidate_files"])
            self.assertEqual(result["final_state"]["primary_file"], "app/utils.py")
            self.assertEqual(result["final_state"]["decision_status"], "supported")

    def test_mulocbench_summary_uses_gold_file_sets(self) -> None:
        tasks = adapt_mulocbench_records([self.record], repos_root="/tmp/repos", repo_layout="nested")
        run_records = {
            tasks[0]["task_id"]: {
                "task_id": tasks[0]["task_id"],
                "final_state": {
                    "candidate_files": ["app/utils.py", "app/engine.py"],
                    "primary_file": "app/utils.py",
                },
            }
        }
        summary = summarize_mulocbench_runs(tasks, run_records)
        self.assertEqual(summary["total_tasks"], 1)
        self.assertEqual(summary["tasks_with_runs"], 1)
        self.assertAlmostEqual(summary["candidate_recall_rate_any_gold"], 1.0)
        self.assertAlmostEqual(summary["primary_file_match_rate"], 1.0)
        self.assertAlmostEqual(summary["primary_file_any_gold_match_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
