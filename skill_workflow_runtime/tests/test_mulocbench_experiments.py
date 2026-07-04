from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import skill_workflow_runtime.run_mulocbench_localization_experiments as mulocbench_module
from skill_workflow_runtime.run_mulocbench_localization_experiments import (
    run_mulocbench_localization_experiments,
)


class MULocBenchExperimentsTest(unittest.TestCase):
    def test_first_batch_pipeline_writes_tasks_runs_and_summary(self) -> None:
        raw_records = [
            {
                "organization": "octo",
                "repo_name": "demo-repo",
                "issue_number": 17,
                "title": "JSON tail recovery fails on truncated output",
                "body": (
                    "Long structured generations can fail with JSONDecodeError during recovery.\n"
                    "Traceback:\n"
                    "File \"app/engine.py\", line 83, in generate_json\n"
                    "File \"app/utils.py\", line 141, in safe_json_loads\n"
                ),
                "loctype": {"code": ["app/utils.py"]},
                "file_loc": {"files": [{"path": "app/utils.py"}]},
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            repos_root = temp_root / "repos"
            repo_path = repos_root / "octo" / "demo-repo"
            (repo_path / "app").mkdir(parents=True)
            (repo_path / "app" / "engine.py").write_text(
                "def generate_json(raw_text):\n"
                "    return safe_json_loads(raw_text)\n"
            )
            (repo_path / "app" / "utils.py").write_text(
                "def safe_json_loads(raw_text):\n"
                "    # JSONDecodeError recovery and truncated output handling live here.\n"
                "    return raw_text\n"
            )

            tasks_output = temp_root / "tasks.json"
            runs_dir = temp_root / "runs"
            summary_output = temp_root / "summary.json"
            summary_markdown_output = temp_root / "summary.md"

            summary = run_mulocbench_localization_experiments(
                raw_records=raw_records,
                skill_dir_name="bug_triage",
                repos_root=str(repos_root),
                repo_layout="nested",
                strategy_name="default",
                tasks_output_path=tasks_output,
                runs_dir=runs_dir,
                summary_output_path=summary_output,
                summary_markdown_output_path=summary_markdown_output,
            )

            self.assertEqual(summary["source_dataset"], "MULocBench")
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["total_tasks"], 1)
            self.assertEqual(summary["tasks_with_runs"], 1)
            self.assertAlmostEqual(summary["candidate_recall_rate_any_gold"], 1.0)
            self.assertAlmostEqual(summary["primary_file_match_rate"], 1.0)
            self.assertTrue(tasks_output.exists())
            self.assertTrue(summary_output.exists())
            self.assertTrue(summary_markdown_output.exists())
            run_files = sorted(runs_dir.glob("*.json"))
            self.assertEqual(len(run_files), 1)
            markdown = summary_markdown_output.read_text()
            self.assertIn("MULocBench Localization Summary", markdown)
            self.assertIn("app/utils.py", markdown)
            self.assertIn("- status: `complete`", markdown)
            parsed_tasks = json.loads(tasks_output.read_text())
            self.assertEqual(parsed_tasks[0]["gold_primary_file"], "app/utils.py")

    def test_partial_summary_is_written_before_failure(self) -> None:
        class FakeRuntime:
            def __init__(self, *args, **kwargs) -> None:
                self.close_calls = 0

            def run(self, *, manifest, task):
                if task["task_id"] == "task-two":
                    raise RuntimeError("simulated failure")
                return {
                    "task_id": task["task_id"],
                    "final_state": {
                        "candidate_files": ["app/utils.py"],
                        "primary_file": "app/utils.py",
                    },
                }

            def close(self) -> None:
                self.close_calls += 1

        tasks = [
            {
                "task_id": "task-one",
                "gold_primary_file": "app/utils.py",
                "gold_localized_files": ["app/utils.py"],
            },
            {
                "task_id": "task-two",
                "gold_primary_file": "app/engine.py",
                "gold_localized_files": ["app/engine.py"],
            },
        ]
        runtime = FakeRuntime()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            tasks_output = temp_root / "tasks.json"
            runs_dir = temp_root / "runs"
            summary_output = temp_root / "summary.json"
            summary_markdown_output = temp_root / "summary.md"

            with (
                patch.object(mulocbench_module, "adapt_mulocbench_records", return_value=tasks),
                patch.object(
                    mulocbench_module,
                    "load_skill_manifest",
                    return_value=SimpleNamespace(name="bug-triage-skill"),
                ),
                patch.object(mulocbench_module, "SkillWorkflowRuntime", return_value=runtime),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated failure"):
                    run_mulocbench_localization_experiments(
                        raw_records=[{"id": 1}, {"id": 2}],
                        skill_dir_name="bug_triage",
                        repos_root=None,
                        repo_layout="nested",
                        strategy_name="default",
                        tasks_output_path=tasks_output,
                        runs_dir=runs_dir,
                        summary_output_path=summary_output,
                        summary_markdown_output_path=summary_markdown_output,
                    )

            self.assertTrue(tasks_output.exists())
            self.assertTrue(summary_output.exists())
            self.assertTrue(summary_markdown_output.exists())
            run_files = sorted(runs_dir.glob("*.json"))
            self.assertEqual(len(run_files), 1)
            persisted = json.loads(summary_output.read_text())
            self.assertEqual(persisted["status"], "failed")
            self.assertEqual(persisted["completed_tasks"], 1)
            self.assertEqual(persisted["tasks_with_runs"], 1)
            self.assertEqual(persisted["failed_task"]["task_id"], "task-two")
            self.assertIn("RuntimeError: simulated failure", persisted["failed_task"]["error"])
            markdown = summary_markdown_output.read_text()
            self.assertIn("- status: `failed`", markdown)
            self.assertIn("- failed task: `task-two`", markdown)
            self.assertEqual(runtime.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
