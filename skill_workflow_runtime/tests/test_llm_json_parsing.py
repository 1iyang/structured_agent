from __future__ import annotations

import unittest
from unittest.mock import patch

import skill_workflow_runtime.skill_runtime.llm as llm_module
from skill_workflow_runtime.skill_runtime.llm import LocalVLLMJsonBackend, render_json_stage_prompt
from skill_workflow_runtime.skill_runtime.models import (
    OutputFieldDefinition,
    OutputSchemaDefinition,
    StageDefinition,
    StageMetadata,
    StageReads,
)


class LocalVLLMJsonParsingTest(unittest.TestCase):
    def _response_stage(self) -> StageDefinition:
        return StageDefinition(
            name="response_draft",
            executor="llm.json_stage",
            reads=StageReads(task=[], state=["response_guardrails", "refined_response_notes"], artifacts=[]),
            writes=["response_message"],
            metadata=StageMetadata(stage_type="response", workflow_phase="handoff"),
            output_schema="support.response_state",
            instruction="Draft the final customer-facing response from structured state.",
        )

    def _response_schema(self) -> OutputSchemaDefinition:
        return OutputSchemaDefinition(
            name="support.response_state",
            fields={
                "response_message": OutputFieldDefinition(name="response_message", type_spec="string"),
            },
        )

    def test_local_vllm_defaults_match_offline_single_task_workload(self) -> None:
        backend = LocalVLLMJsonBackend(model="dummy")
        startup_profiles = backend._build_startup_profiles()
        requested_profile_name, requested_profile = startup_profiles[0]
        profile_names = [name for name, _ in startup_profiles]

        self.assertEqual(requested_profile_name, "requested")
        self.assertEqual(requested_profile["max_num_seqs"], 16)
        self.assertTrue(requested_profile["enforce_eager"])
        self.assertEqual(requested_profile["gpu_memory_utilization"], 0.6)
        self.assertEqual(
            profile_names,
            [
                "requested",
                "memory_safe",
                "memory_safe_low",
                "memory_safe_tight",
                "memory_safe_minimal",
            ],
        )

    def test_extract_json_object_ignores_trailing_explanation_and_fenced_repeat(self) -> None:
        backend = LocalVLLMJsonBackend(model="dummy")
        raw_text = """
{
  "issue_type": "damaged_item",
  "customer_goal": "replacement",
  "urgency": "high",
  "missing_information": [
    "photos of the damaged item"
  ]
}
Okay, let's break this down.
```json
{
  "issue_type": "damaged_item",
  "customer_goal": "replacement",
  "urgency": "high",
  "missing_information": [
    "photos of the damaged item"
  ]
}
```
"""

        parsed = backend._extract_json_object(raw_text)

        self.assertEqual(
            parsed,
            {
                "issue_type": "damaged_item",
                "customer_goal": "replacement",
                "urgency": "high",
                "missing_information": ["photos of the damaged item"],
            },
        )

    def test_select_best_output_candidate_ignores_echoed_task_view(self) -> None:
        backend = LocalVLLMJsonBackend(model="dummy")
        schema = OutputSchemaDefinition(
            name="support.case_assessment_state",
            fields={
                "issue_type": OutputFieldDefinition(name="issue_type", type_spec="string"),
                "customer_goal": OutputFieldDefinition(name="customer_goal", type_spec="string"),
                "urgency": OutputFieldDefinition(name="urgency", type_spec="string"),
            },
        )
        raw_text = """
Task view:
{
  "customer_message": "My headphones arrived cracked.",
  "context_pack": "Policy excerpt: request one or two photos."
}

Final answer:
{
  "issue_type": "damaged_item",
  "customer_goal": "replacement",
  "urgency": "high"
}
"""

        parsed_objects = backend._extract_json_objects(raw_text)
        normalized_candidates = [
            backend._normalize_output_shape(parsed, schema) for parsed in parsed_objects
        ]
        best = backend._select_best_output_candidate(normalized_candidates, schema)

        self.assertEqual(
            best.projected,
            {
                "issue_type": "damaged_item",
                "customer_goal": "replacement",
                "urgency": "high",
            },
        )

    def test_normalize_output_shape_unwraps_common_wrapper(self) -> None:
        backend = LocalVLLMJsonBackend(model="dummy")
        schema = OutputSchemaDefinition(
            name="support.case_assessment_state",
            fields={
                "issue_type": OutputFieldDefinition(name="issue_type", type_spec="string"),
                "customer_goal": OutputFieldDefinition(name="customer_goal", type_spec="string"),
                "urgency": OutputFieldDefinition(name="urgency", type_spec="string"),
            },
        )
        parsed = {
            "state": {
                "issue_type": "damaged_item",
                "customer_goal": "replacement",
                "urgency": "high",
            }
        }

        normalized = backend._normalize_output_shape(parsed, schema)

        self.assertEqual(
            normalized,
            {
                "issue_type": "damaged_item",
                "customer_goal": "replacement",
                "urgency": "high",
            },
        )

    def test_render_json_stage_prompt_highlights_required_and_input_only_keys(self) -> None:
        prompt = render_json_stage_prompt(
            stage=self._response_stage(),
            instruction="Draft the final response.",
            task_view={},
            state_view={
                "response_guardrails": ["Mention the missing photo first."],
                "refined_response_notes": ["Lead with blocker clarity."],
            },
            artifact_view={},
            output_schema=self._response_schema(),
            prompt_header="Header",
        )

        self.assertIn('Output format: {"response_message": "<final text for this stage>"}', prompt)
        self.assertIn("response_guardrails", prompt)
        self.assertIn("refined_response_notes", prompt)
        self.assertNotIn("Required top-level keys:", prompt)

    def test_select_best_output_candidate_prefers_valid_schema_match(self) -> None:
        backend = LocalVLLMJsonBackend(model="dummy")
        assessment = backend._select_best_output_candidate(
            [
                {"refined_response_notes": ["Lead with blocker clarity."]},
                {"response_message": "Please send the photo so we can continue the replacement.", "debug": "drop"},
            ],
            self._response_schema(),
        )

        self.assertTrue(assessment.valid)
        self.assertEqual(
            assessment.projected,
            {"response_message": "Please send the photo so we can continue the replacement."},
        )
        self.assertEqual(assessment.extra_fields, ["debug"])

    def test_generate_json_repairs_schema_mismatch_and_projects_to_schema(self) -> None:
        backend = LocalVLLMJsonBackend(model="dummy")
        prompts = []
        generations = iter(
            [
                '{"refined_response_notes": ["Lead with blocker clarity."], "refinement_focus": ["blocker_clarity"], "response_guardrails": ["Mention the missing photo first."]}',
                '{"response_message": "Please send the damage photo so we can continue the replacement.", "debug": "drop"}',
            ]
        )

        def fake_generate(prompt: str, max_tokens: int) -> str:
            prompts.append(prompt)
            return next(generations)

        with patch.object(backend, "_generate_once", side_effect=fake_generate):
            result = backend.generate_json(
                stage=self._response_stage(),
                instruction="Draft the final customer-facing response from structured state.",
                task_view={},
                state_view={
                    "customer_goal": "Replacement for cracked headphones.",
                    "eligibility_decision": "needs_info",
                    "resolution_category": "replacement",
                    "resolution_steps": ["Collect damage_proof.", "Continue with replacement."],
                    "handoff_status": "blocked",
                    "response_guardrails": ["Mention the missing photo first."],
                    "refinement_focus": ["blocker_clarity"],
                    "refined_response_notes": ["Lead with blocker clarity."],
                },
                artifact_view={},
                output_schema=self._response_schema(),
            )

        self.assertEqual(
            result,
            {"response_message": "Please send the damage photo so we can continue the replacement."},
        )
        self.assertEqual(len(prompts), 2)
        self.assertIn("Schema feedback: missing required keys ['response_message']", prompts[1])
        self.assertIn('Output format: {"response_message": "<final text for this stage>"}', prompts[1])
        self.assertNotIn("Previous answer:", prompts[1])

    def test_generate_json_coerces_plain_text_for_single_string_schema(self) -> None:
        backend = LocalVLLMJsonBackend(model="dummy")

        with patch.object(
            backend,
            "_generate_once",
            return_value="I'm sorry the headphones arrived damaged. Please send a photo of the crack, and we will continue the replacement right away.",
        ):
            result = backend.generate_json(
                stage=self._response_stage(),
                instruction="Draft the final customer-facing response from structured state.",
                task_view={},
                state_view={"response_guardrails": ["Mention the missing photo first."]},
                artifact_view={},
                output_schema=self._response_schema(),
            )

        self.assertEqual(
            result,
            {
                "response_message": (
                    "I'm sorry the headphones arrived damaged. Please send a photo of the crack, "
                    "and we will continue the replacement right away."
                )
            },
        )

    def test_generate_json_rejects_prompt_echo_for_single_string_schema(self) -> None:
        backend = LocalVLLMJsonBackend(model="dummy")
        prompts = []
        generations = iter(
            [
                "So, the response should be a message that:\n- Is friendly\n- Does not mention any specific internal state views",
                '{"response_message": "Please send a photo of the damage so we can continue the replacement."}',
            ]
        )

        def fake_generate(prompt: str, max_tokens: int) -> str:
            prompts.append(prompt)
            return next(generations)

        with patch.object(backend, "_generate_once", side_effect=fake_generate):
            result = backend.generate_json(
                stage=self._response_stage(),
                instruction="Draft the final customer-facing response from structured state.",
                task_view={},
                state_view={"response_guardrails": ["Mention the missing photo first."]},
                artifact_view={},
                output_schema=self._response_schema(),
            )

        self.assertEqual(
            result,
            {"response_message": "Please send a photo of the damage so we can continue the replacement."},
        )
        self.assertEqual(len(prompts), 2)
        self.assertIn("Your previous answer was invalid for this workflow stage.", prompts[1])
        self.assertNotIn("Previous answer:", prompts[1])

    def test_ensure_llm_retries_with_more_conservative_profile(self) -> None:
        calls = []

        class DummyLLM:
            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    raise RuntimeError("Engine core initialization failed. See root cause above. Failed core proc(s): {}")

        backend = LocalVLLMJsonBackend(
            model="dummy",
            gpu_memory_utilization=0.7,
            max_num_seqs=64,
            enforce_eager=False,
        )

        with (
            patch.object(llm_module, "LLM", DummyLLM),
            patch.object(llm_module, "SamplingParams", object),
            patch.object(backend, "_probe_free_gpu_memory_gib", return_value=None),
        ):
            backend._ensure_llm()

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["max_num_seqs"], 64)
        self.assertFalse(calls[0]["enforce_eager"])
        self.assertEqual(calls[1]["max_num_seqs"], 8)
        self.assertTrue(calls[1]["enforce_eager"])
        self.assertEqual(backend._active_engine_profile, "memory_safe")

    def test_ensure_llm_can_fall_through_to_tighter_gpu_profiles(self) -> None:
        calls = []

        class DummyLLM:
            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                if len(calls) < 4:
                    raise RuntimeError("Engine core initialization failed. See root cause above. Failed core proc(s): {}")

        backend = LocalVLLMJsonBackend(
            model="dummy",
            gpu_memory_utilization=0.55,
            max_num_seqs=8,
            enforce_eager=True,
        )

        with (
            patch.object(llm_module, "LLM", DummyLLM),
            patch.object(llm_module, "SamplingParams", object),
            patch.object(backend, "_probe_free_gpu_memory_gib", return_value=None),
        ):
            backend._ensure_llm()

        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0]["gpu_memory_utilization"], 0.55)
        self.assertEqual(calls[1]["gpu_memory_utilization"], 0.5)
        self.assertEqual(calls[1]["max_num_seqs"], 4)
        self.assertEqual(calls[1]["max_num_batched_tokens"], 4096)
        self.assertEqual(calls[2]["gpu_memory_utilization"], 0.42)
        self.assertEqual(calls[2]["max_num_seqs"], 2)
        self.assertEqual(calls[2]["max_num_batched_tokens"], 2048)
        self.assertEqual(calls[3]["gpu_memory_utilization"], 0.38)
        self.assertEqual(calls[3]["max_num_seqs"], 1)
        self.assertEqual(calls[3]["max_num_batched_tokens"], 1024)
        self.assertEqual(backend._active_engine_profile, "memory_safe_minimal")

    def test_format_startup_error_calls_out_when_even_tightest_profile_cannot_fit(self) -> None:
        backend = LocalVLLMJsonBackend(
            model="dummy",
            gpu_memory_utilization=0.8,
            max_num_seqs=4,
            enforce_eager=True,
        )
        backend._startup_attempts = [
            {
                "profile": "requested",
                "config": {
                    "gpu_memory_utilization": 0.8,
                    "max_num_seqs": 4,
                    "enforce_eager": True,
                    "max_model_len": 8192,
                },
                "status": "failed",
                "error": (
                    "Free memory on device (4.50/23.59 GiB) on startup is less than desired GPU memory "
                    "utilization (0.38, 8.96 GiB). Decrease GPU memory utilization or reduce GPU memory "
                    "used by other processes."
                ),
            }
        ]
        exc = RuntimeError("Engine core initialization failed. See root cause above. Failed core proc(s): {}")

        formatted = backend._format_startup_error(exc)
        message = str(formatted)

        self.assertIn("Current free VRAM is below the runtime's tightest configured startup profile", message)
        self.assertIn("memory_safe_minimal", message)
        self.assertIn("4.50 GiB", message)

    def test_ensure_llm_skips_startup_when_preflight_detects_insufficient_vram(self) -> None:
        backend = LocalVLLMJsonBackend(
            model="dummy",
            gpu_memory_utilization=0.85,
            max_num_seqs=4,
            enforce_eager=True,
        )

        with (
            patch.object(backend, "_probe_free_gpu_memory_gib", return_value=(3.32, 23.59)),
            patch.object(llm_module, "LLM", side_effect=AssertionError("LLM should not start when preflight fails")),
            patch.object(llm_module, "SamplingParams", object),
        ):
            with self.assertRaises(RuntimeError) as raised:
                backend._ensure_llm()

        message = str(raised.exception)
        self.assertIn("Local vLLM backend failed to start because the GPU does not have enough free VRAM.", message)
        self.assertIn("Current free VRAM is below the runtime's tightest configured startup profile", message)
        self.assertIn("memory_safe_minimal", message)
        self.assertIn("3.32", message)
        self.assertEqual(
            [attempt["status"] for attempt in backend._startup_attempts],
            ["skipped", "skipped", "skipped", "skipped"],
        )


if __name__ == "__main__":
    unittest.main()
