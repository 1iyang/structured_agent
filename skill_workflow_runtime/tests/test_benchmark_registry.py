from __future__ import annotations

import unittest

from skill_workflow_runtime.skill_runtime.benchmark_registry import (
    build_benchmark_validation_plan,
    render_benchmark_validation_markdown,
)


class BenchmarkRegistryTest(unittest.TestCase):
    def test_plan_contains_expected_layers_and_targets(self) -> None:
        plan = build_benchmark_validation_plan()
        self.assertEqual(
            [layer.key for layer in plan.layers],
            [
                "mechanism_validation",
                "localization_validation",
                "external_transfer_validation",
            ],
        )

        targets = {target.key: target for target in plan.targets}
        self.assertIn("tau_knowledge", targets)
        self.assertIn("mulocbench", targets)
        self.assertIn("swe_bench", targets)
        self.assertIn("multi_swe_bench", targets)

        self.assertEqual(targets["tau_knowledge"].validation_layer, "external_transfer_validation")
        self.assertEqual(targets["mulocbench"].validation_layer, "localization_validation")
        self.assertEqual(targets["swe_bench"].validation_layer, "external_transfer_validation")
        self.assertEqual(targets["multi_swe_bench"].validation_layer, "external_transfer_validation")

    def test_plan_execution_order_starts_with_internal_workflows(self) -> None:
        plan = build_benchmark_validation_plan()
        self.assertEqual(
            plan.execution_order[:2],
            ["internal_support_workflow", "internal_bug_workflow"],
        )
        self.assertIn("mulocbench", plan.execution_order)

    def test_markdown_render_mentions_external_targets(self) -> None:
        plan = build_benchmark_validation_plan()
        markdown = render_benchmark_validation_markdown(plan)
        self.assertIn("tau-Knowledge", markdown)
        self.assertIn("MULocBench", markdown)
        self.assertIn("SWE-bench", markdown)
        self.assertIn("Multi-SWE-bench", markdown)


if __name__ == "__main__":
    unittest.main()
