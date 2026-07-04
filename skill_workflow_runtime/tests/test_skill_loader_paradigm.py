from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from skill_workflow_runtime.skill_runtime.loader import load_skill_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]


class SkillLoaderParadigmTest(unittest.TestCase):
    def test_loader_parses_constraints_and_workflow_phase(self) -> None:
        manifest = load_skill_manifest(
            REPO_ROOT / "skill_workflow_runtime" / "skills" / "support_ticket"
        )

        self.assertEqual(
            [constraint.name for constraint in manifest.constraints],
            [
                "minimal_state_handoff",
                "blocker_before_commitment",
                "no_unsupported_promise",
            ],
        )
        self.assertEqual(manifest.stages[0].metadata.workflow_phase, "observe")
        self.assertEqual(manifest.stages[-1].metadata.workflow_phase, "handoff")

    def test_loader_rejects_phase_regression(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: invalid-phase-skill
            description: bad order
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Output Schemas

            ### test.observe_state
            - observed: string

            ### test.abstract_state
            - abstracted: string

            ## Workflow

            ### route_first
            - executor: llm.json_stage
            - stage_type: decision
            - workflow_phase: route
            - anchor_stage: false
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: observed
            - output_schema: test.observe_state

            route too early

            ### observe_second
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: observed
            - reads.artifacts: none
            - writes: abstracted
            - output_schema: test.abstract_state

            observe too late
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "regresses workflow_phase ordering"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_duplicate_state_writes(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: duplicate-write-skill
            description: duplicate field writes
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Output Schemas

            ### test.observe_state
            - observed: string

            ## Workflow

            ### observe_one
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: observed
            - output_schema: test.observe_state

            first writer

            ### observe_two
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: observed
            - reads.artifacts: none
            - writes: observed
            - output_schema: test.observe_state

            second writer
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "rewrites state field"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_suffix_raw_task_reads_without_explicit_constraint(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: suffix-raw-read-skill
            description: rereads raw context in suffix
            ---

            ## Canonical Task
            - task_id
            - customer_message
            - context_pack

            ## Input Profiles

            ### generic
            - match: customer_message
            - map.task_id: task_id
            - map.customer_message: customer_message
            - map.context_pack: context_pack

            ## Output Schemas

            ### test.observe_state
            - issue_type: string

            ### test.route_state
            - eligibility_decision: string

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: customer_message, context_pack
            - reads.state: none
            - reads.artifacts: none
            - writes: issue_type
            - output_schema: test.observe_state

            observe raw evidence

            ### route_stage
            - executor: llm.json_stage
            - stage_type: decision
            - workflow_phase: route
            - anchor_stage: false
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: customer_message, context_pack
            - reads.state: issue_type
            - reads.artifacts: none
            - writes: eligibility_decision
            - output_schema: test.route_state

            rereads raw evidence in suffix
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "rereads heavy raw task fields"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_anchor_answer_like_outputs(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: anchor-answer-skill
            description: anchor writes final response
            ---

            ## Canonical Task
            - task_id
            - customer_message

            ## Input Profiles

            ### generic
            - match: customer_message
            - map.task_id: task_id
            - map.customer_message: customer_message

            ## Output Schemas

            ### test.observe_state
            - response_message: string

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: customer_message
            - reads.state: none
            - reads.artifacts: none
            - writes: response_message
            - output_schema: test.observe_state

            writes the final answer too early
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "Anchor stage .* writes answer-like fields"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_stage_that_mixes_too_many_control_families(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: mixed-control-skill
            description: stage mixes blocker path decision and action
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Output Schemas

            ### test.observe_state
            - policy_signals: list[string]

            ### test.verify_state
            - blocking_requirements: list[string]
            - preferred_resolution_path: string
            - eligibility_decision: string
            - resolution_steps: list[string]

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: policy_signals
            - output_schema: test.observe_state

            build evidence state

            ### verify_stage
            - executor: llm.json_stage
            - stage_type: review
            - workflow_phase: verify
            - anchor_stage: false
            - criticality: medium
            - optional_stage: false
            - tool_mode: none
            - reads.task: none
            - reads.state: policy_signals
            - reads.artifacts: none
            - writes: blocking_requirements, preferred_resolution_path, eligibility_decision, resolution_steps
            - output_schema: test.verify_state

            mixes too many control surfaces
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "mixes too many control families"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_suffix_heavy_artifact_reads_without_explicit_constraint(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: suffix-heavy-artifact-skill
            description: suffix reads heavy artifacts
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Artifacts

            ### retrieved_artifacts

            Heavy retrieved evidence bundle.

            ## Output Schemas

            ### test.observe_state
            - bug_summary: string

            ### test.execute_state
            - resolution_steps: list[string]

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: bug_summary
            - output_schema: test.observe_state

            observe stage

            ### execute_stage
            - executor: llm.json_stage
            - stage_type: plan
            - workflow_phase: execute
            - anchor_stage: false
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: none
            - reads.state: bug_summary
            - reads.artifacts: retrieved_artifacts
            - writes: resolution_steps
            - output_schema: test.execute_state

            suffix rereads heavy artifact
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "rereads heavy artifacts"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_suffix_read_only_tool_calls_without_explicit_constraint(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: suffix-tool-skill
            description: suffix calls read-only tool
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Tools

            ### repo.search
            - kind: read_only

            repository search tool

            ## Output Schemas

            ### test.observe_state
            - bug_summary: string

            ### test.execute_state
            - resolution_steps: list[string]

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: bug_summary
            - output_schema: test.observe_state

            observe stage

            ### execute_stage
            - executor: llm.json_stage
            - stage_type: plan
            - workflow_phase: execute
            - anchor_stage: false
            - criticality: high
            - optional_stage: false
            - tool_mode: read_only
            - tool_intent: retrieval
            - reads.task: none
            - reads.state: bug_summary
            - reads.artifacts: none
            - uses.tools: repo.search
            - writes: resolution_steps
            - output_schema: test.execute_state

            suffix opens evidence tool again
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "uses read-only tools"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_heavy_tool_artifact_without_compression_stage(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: uncompressed-tool-artifact-skill
            description: tool artifact is never compressed
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Tools

            ### repo.search
            - kind: read_only

            repository search tool

            ## Artifacts

            ### retrieved_artifacts

            Heavy retrieved evidence bundle.

            ## Output Schemas

            ### test.retrieval_state
            - retrieval_summary: string

            ### test.retrieval_state_two
            - retrieval_summary_two: string

            ## Workflow

            ### retrieve_stage
            - executor: llm.json_stage
            - stage_type: retrieval
            - workflow_phase: abstract
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: read_only
            - tool_intent: retrieval
            - post_tool_policy: requires_compression
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - uses.tools: repo.search
            - writes: retrieval_summary
            - output_schema: test.retrieval_state
            - writes.artifacts: retrieved_artifacts

            retrieve evidence but never compress it
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(
                ValueError,
                "writes heavy artifact .* but no later compression boundary converts it into reusable state",
            ):
                load_skill_manifest(skill_path)

    def test_loader_emits_artifact_tool_static_lint_report(self) -> None:
        manifest = load_skill_manifest(
            REPO_ROOT / "skill_workflow_runtime" / "skills" / "bug_triage"
        )

        self.assertEqual(manifest.static_lint["version"], "artifact-tool-lint-v3")
        artifact_tool = manifest.static_lint["artifact_tool_boundary"]
        artifact_lineage = manifest.static_lint["artifact_lineage"]
        self.assertIn("retrieved_artifacts", artifact_tool["heavy_artifacts"])
        self.assertEqual(len(artifact_tool["suffix_heavy_artifact_reads"]), 0)
        self.assertEqual(len(artifact_tool["suffix_read_only_tool_stages"]), 0)
        self.assertEqual(
            artifact_tool["heavy_tool_artifact_boundaries"],
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
        self.assertEqual(
            artifact_tool["tool_contracts"],
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
        self.assertEqual(artifact_tool["suffix_retrieval_subloops"], [])
        self.assertEqual(
            artifact_lineage,
            {
                "declared_artifacts": ["retrieved_artifacts"],
                "artifact_producers": {
                    "retrieved_artifacts": [
                        {
                            "stage": "high_recall_retrieval",
                            "workflow_phase": "abstract",
                        }
                    ]
                },
                "artifact_consumers": {
                    "retrieved_artifacts": [
                        {
                            "stage": "evidence_screen",
                            "workflow_phase": "abstract",
                        }
                    ]
                },
                "unproduced_artifacts": [],
                "multi_producer_artifacts": {},
                "suffix_artifact_lineage_reads": [],
            },
        )

    def test_loader_accepts_positive_suffix_reretrieval_demo_contract(self) -> None:
        manifest = load_skill_manifest(
            REPO_ROOT / "skill_workflow_runtime" / "skills" / "bug_triage_suffix_reretrieval_demo"
        )

        self.assertEqual(manifest.static_lint["version"], "artifact-tool-lint-v3")
        self.assertEqual(
            manifest.static_lint["declared_exception_constraints"],
            [
                "allow_suffix_heavy_artifact_reads",
                "allow_suffix_read_only_tool_calls",
            ],
        )
        artifact_tool = manifest.static_lint["artifact_tool_boundary"]
        self.assertEqual(
            artifact_tool["suffix_read_only_tool_stages"],
            [
                {
                    "stage": "suffix_retrieval_refresh",
                    "workflow_phase": "execute",
                    "tools": ["bug.local_repo_search"],
                    "tool_intent": "re_retrieval",
                    "post_tool_policy": "requires_compression",
                    "allowed_by_constraint": True,
                }
            ],
        )
        self.assertEqual(
            artifact_tool["suffix_retrieval_subloops"],
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
        self.assertEqual(
            artifact_tool["heavy_tool_artifact_boundaries"],
            [
                {
                    "artifact": "retrieved_artifacts",
                    "producer_stage": "high_recall_retrieval",
                    "producer_phase": "abstract",
                    "tools": ["bug.local_repo_search"],
                    "compressed_by": ["evidence_screen"],
                },
                {
                    "artifact": "followup_retrieved_artifacts",
                    "producer_stage": "suffix_retrieval_refresh",
                    "producer_phase": "execute",
                    "tools": ["bug.local_repo_search"],
                    "compressed_by": ["followup_evidence_compression"],
                },
            ],
        )
        followup_compression = next(
            stage for stage in manifest.stages if stage.name == "followup_evidence_compression"
        )
        self.assertEqual(followup_compression.metadata.prompt_cost_hint, 380)

    def test_loader_rejects_tool_stage_without_explicit_tool_intent(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: missing-tool-intent-skill
            description: tool stage missing intent
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Tools

            ### repo.search
            - kind: read_only

            repository search tool

            ## Output Schemas

            ### test.retrieval_state
            - retrieval_summary: string

            ## Workflow

            ### retrieve_stage
            - executor: llm.json_stage
            - stage_type: retrieval
            - workflow_phase: abstract
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: read_only
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - uses.tools: repo.search
            - writes: retrieval_summary
            - output_schema: test.retrieval_state

            tool stage without explicit intent
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "does not declare tool_intent"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_re_retrieval_without_compression_policy(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: bad-reretrieval-policy-skill
            description: re-retrieval without compression policy
            ---

            ## Canonical Task
            - task_id

            ## Constraints

            ### allow_suffix_read_only_tool_calls

            Explicitly allow suffix tool calls for this test.

            ### allow_suffix_heavy_artifact_reads

            Explicitly allow suffix heavy artifact reads for this test.

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Tools

            ### repo.search
            - kind: read_only

            repository search tool

            ## Artifacts

            ### retrieved_artifacts

            Heavy retrieved evidence bundle.

            ## Output Schemas

            ### test.observe_state
            - bug_summary: string

            ### test.retrieval_state
            - retrieval_summary: string

            ### test.verify_state
            - review_notes: list[string]

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: bug_summary
            - output_schema: test.observe_state

            observe stage

            ### reread_stage
            - executor: llm.json_stage
            - stage_type: retrieval
            - workflow_phase: verify
            - anchor_stage: false
            - criticality: medium
            - optional_stage: false
            - tool_mode: read_only
            - tool_intent: re_retrieval
            - post_tool_policy: direct_state
            - reads.task: none
            - reads.state: bug_summary
            - reads.artifacts: none
            - uses.tools: repo.search
            - writes: retrieval_summary
            - output_schema: test.retrieval_state
            - writes.artifacts: retrieved_artifacts

            bad re-retrieval policy

            ### compress_stage
            - executor: llm.json_stage
            - stage_type: compression
            - workflow_phase: handoff
            - anchor_stage: false
            - criticality: medium
            - optional_stage: false
            - tool_mode: none
            - reads.task: none
            - reads.state: retrieval_summary
            - reads.artifacts: retrieved_artifacts
            - writes: review_notes
            - output_schema: test.verify_state

            compression stage exists but policy is wrong
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "Re-retrieval must return through a compression stage"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_re_retrieval_without_immediate_compression_stage(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: bad-reretrieval-subloop-skill
            description: re-retrieval not immediately compressed
            ---

            ## Canonical Task
            - task_id

            ## Constraints

            ### allow_suffix_read_only_tool_calls

            Explicitly allow suffix tool calls for this test.

            ### allow_suffix_heavy_artifact_reads

            Explicitly allow suffix heavy artifact reads for this test.

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Tools

            ### repo.search
            - kind: read_only

            repository search tool

            ## Artifacts

            ### retrieved_artifacts

            Heavy retrieved evidence bundle.

            ## Output Schemas

            ### test.observe_state
            - bug_summary: string

            ### test.retrieval_state
            - retrieval_summary: string

            ### test.handoff_state
            - fix_plan: list[string]

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: bug_summary
            - output_schema: test.observe_state

            observe stage

            ### reread_stage
            - executor: llm.json_stage
            - stage_type: retrieval
            - workflow_phase: verify
            - anchor_stage: false
            - criticality: medium
            - optional_stage: false
            - tool_mode: read_only
            - tool_intent: re_retrieval
            - post_tool_policy: requires_compression
            - reads.task: none
            - reads.state: bug_summary
            - reads.artifacts: none
            - uses.tools: repo.search
            - writes: retrieval_summary
            - output_schema: test.retrieval_state
            - writes.artifacts: retrieved_artifacts

            re-retrieval stage

            ### handoff_stage
            - executor: llm.json_stage
            - stage_type: plan
            - workflow_phase: handoff
            - anchor_stage: false
            - criticality: medium
            - optional_stage: false
            - tool_mode: none
            - reads.task: none
            - reads.state: retrieval_summary
            - reads.artifacts: none
            - writes: fix_plan
            - output_schema: test.handoff_state

            missing compression stage after re-retrieval
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "next stage .* is not a compression stage"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_declared_artifact_that_is_never_produced(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: unproduced-artifact-skill
            description: artifact declared but never produced
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Artifacts

            ### repo_context

            Declared but never produced.

            ## Output Schemas

            ### test.observe_state
            - bug_summary: string

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: bug_summary
            - output_schema: test.observe_state

            simple stage
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "Artifacts declared but never produced"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_artifact_rewrite(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: artifact-rewrite-skill
            description: artifact rewritten twice
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Artifacts

            ### retrieved_artifacts

            Rewritten artifact.

            ## Output Schemas

            ### test.retrieval_state
            - retrieval_summary: string

            ### test.retrieval_state_two
            - retrieval_summary_two: string

            ## Workflow

            ### retrieve_one
            - executor: llm.json_stage
            - stage_type: retrieval
            - workflow_phase: abstract
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: none
            - writes: retrieval_summary
            - output_schema: test.retrieval_state
            - writes.artifacts: retrieved_artifacts

            first producer

            ### retrieve_two
            - executor: llm.json_stage
            - stage_type: retrieval
            - workflow_phase: abstract
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: retrieval_summary
            - reads.artifacts: none
            - writes: retrieval_summary_two
            - output_schema: test.retrieval_state_two
            - writes.artifacts: retrieved_artifacts

            second producer
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "rewrites artifact"):
                load_skill_manifest(skill_path)

    def test_loader_rejects_artifact_read_before_producer(self) -> None:
        skill_text = textwrap.dedent(
            """
            ---
            name: early-artifact-read-skill
            description: reads artifact before it exists
            ---

            ## Canonical Task
            - task_id

            ## Input Profiles

            ### generic
            - match: task_id
            - map.task_id: task_id

            ## Artifacts

            ### retrieved_artifacts

            Read too early.

            ## Output Schemas

            ### test.observe_state
            - bug_summary: string

            ### test.retrieval_state
            - retrieval_summary: string

            ## Workflow

            ### observe_stage
            - executor: llm.json_stage
            - stage_type: evidence
            - workflow_phase: observe
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: none
            - reads.artifacts: retrieved_artifacts
            - writes: bug_summary
            - output_schema: test.observe_state

            reads before production

            ### retrieve_stage
            - executor: llm.json_stage
            - stage_type: retrieval
            - workflow_phase: abstract
            - anchor_stage: true
            - criticality: high
            - optional_stage: false
            - tool_mode: none
            - reads.task: task_id
            - reads.state: bug_summary
            - reads.artifacts: none
            - writes: retrieval_summary
            - output_schema: test.retrieval_state
            - writes.artifacts: retrieved_artifacts

            produces too late
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = Path(tmpdir) / "SKILL.md"
            skill_path.write_text(skill_text)
            with self.assertRaisesRegex(ValueError, "reads artifacts that have not been produced yet"):
                load_skill_manifest(skill_path)


if __name__ == "__main__":
    unittest.main()
