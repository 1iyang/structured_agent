"""Local code-repository bug-triage workflow for state-transfer experiments."""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .schemas import (
    BugEvidenceState,
    BugRootCauseCategory,
    BugTriageQualityMetrics,
    BugFixPlanState,
    BugHypothesisState,
    BugTriageCompressedMemory,
    BugTriageIssue,
    BugTriageIssueResult,
    BugSignalExtractionState,
    BugQueryExpansionState,
    SLOPolicyConfig,
    StageDependencyContract,
    StageRoutingTrace,
    StateDependencySpec,
    StateTransferMethodArtifacts,
    StoredStateArtifact,
    TriageDecisionState,
    TriageReviewState,
)
from .state_transfer import StructuredStateStore, remaining_slo_seconds, routing_metrics_delta
from .transfer_methods import (
    InitialStructuredStage,
    RoutedStructuredStage,
    TransferMethodRuntime,
    default_history_compression_config,
)
from .transfer_methods.common import finalize_method
from .utils import compact_json, count_tokens_simple


@dataclass(frozen=True)
class SearchHit:
    """One raw local-search hit."""

    file_path: str
    line_number: int
    matched_line: str
    excerpt: str
    matched_term: str
    source_kind: str


@dataclass(frozen=True)
class FileEvidence:
    """Grouped local-search evidence for one file."""

    evidence_id: str
    file_path: str
    matched_terms: List[str]
    source_kinds: List[str]
    line_numbers: List[int]
    excerpts: List[str]


@dataclass
class PreparedBugTriageTask:
    """Prepared bug-triage task shared by all transfer strategies."""

    repo_root: Path
    issue: BugTriageIssue
    search_hits: List[SearchHit]
    evidences: List[FileEvidence]
    retrieval_round: int = 0


class BugTriagePromptBuilder:
    """Prompt builder for the local code-triage workflow."""

    ROOT_CAUSE_CATEGORY_OPTIONS = (
        "configuration",
        "state_management",
        "error_recovery",
        "artifact_lifecycle",
        "api_contract_mismatch",
        "invalid_input_handling",
        "test_gap",
        "unknown",
    )
    EVIDENCE_LABEL_OPTIONS = (
        "recovery_logic_gap",
        "lifecycle_scope_error",
        "preflight_guard_missing",
        "heuristic_underestimate",
        "stale_contract_assumption",
        "workflow_branch_mismatch",
        "stage_boundary_misalignment",
    )

    @classmethod
    def _enum_text(cls, values: Sequence[str]) -> str:
        return ", ".join(values)

    @classmethod
    def _root_cause_rubric(cls) -> str:
        return (
            "Root-cause-category taxonomy:\n"
            "- configuration: thresholds, defaults, backend limits, or workflow policy settings are mis-set or not preflighted.\n"
            "- state_management: shared state shape, propagation, workflow bookkeeping, or cross-stage accounting is wrong.\n"
            "- error_recovery: retries, parsing recovery, conservative fallback, or exception handling is incomplete.\n"
            "- artifact_lifecycle: output cleanup, reset behavior, or artifact retention/deletion policy is the main problem.\n"
            "- api_contract_mismatch: one component expects a different interface, field contract, or method behavior than another.\n"
            "- invalid_input_handling: malformed or edge-case input is not validated or handled correctly.\n"
            "- test_gap: the primary issue is a missing guardrail or missing test coverage rather than production logic alone.\n"
            "- unknown: use only when none of the above fits cleanly."
        )

    @classmethod
    def _evidence_label_rubric(cls) -> str:
        return (
            "Evidence-label taxonomy:\n"
            "- recovery_logic_gap: fallback, retry, or repair logic exists but does not safely complete the recovery path.\n"
            "- lifecycle_scope_error: cleanup, reset, or retention logic applies at the wrong time or to the wrong scope.\n"
            "- preflight_guard_missing: the system fails to validate an impossible or unsafe request before delegating it downstream.\n"
            "- heuristic_underestimate: a cheap estimate or heuristic systematically understates real cost or size.\n"
            "- stale_contract_assumption: shared helpers still assume an older workflow shape, field layout, or cross-module contract.\n"
            "- workflow_branch_mismatch: task routing or output branching falls through logic meant for a different workflow family.\n"
            "- stage_boundary_misalignment: the control policy waits until the wrong stage boundary, so mitigation arrives too late."
        )

    @staticmethod
    def build_signal_extractor(issue: BugTriageIssue, rule_context_text: str) -> str:
        return f"""You are the `signal_extractor` skill.

Your job is to normalize the raw issue into retrieval-ready bug signals.
Do not decide the root cause or propose a fix in this stage.

You will receive rule-extracted signals plus the raw issue text.
Use the rule signals when they are concrete, then add higher-level concepts that could help later retrieval.

Requirements:
- Output valid JSON only.
- Keep `error_messages`, `exception_types`, `file_hints`, `path_hints`, `symbol_hints`, and `error_codes` concrete.
- `suspected_modules`, `related_concepts`, and `synonym_terms` may add semantic cues that are not literal text matches.
- `acceptance_checks` should capture the behaviors a fix must preserve.
- Do not include final root-cause labels or fix proposals.

Schema:
{{
  "issue_title": string,
  "bug_summary": string,
  "observed_behavior": string,
  "expected_behavior": string,
  "error_messages": [string],
  "exception_types": [string],
  "file_hints": [string],
  "path_hints": [string],
  "symbol_hints": [string],
  "error_codes": [string],
  "trigger_conditions": [string],
  "suspected_modules": [string],
  "related_concepts": [string],
  "synonym_terms": [string],
  "acceptance_checks": [string]
}}

Rule-extracted signals:
{rule_context_text}

Issue title:
{issue.title}

Issue description:
{issue.description}

Expected behavior:
{issue.expected_behavior or "N/A"}

Log excerpt:
{issue.log_excerpt or "N/A"}

Stacktrace excerpt:
{issue.stacktrace_excerpt or "N/A"}
"""

    @staticmethod
    def build_query_expansion(task: PreparedBugTriageTask, context_text: str) -> str:
        issue = task.issue
        return f"""You are the `query_expansion` skill.

You will receive structured bug signals from `signal_extractor`.
Build a retrieval plan that mixes precise lexical queries with broader semantic and structural queries.

Requirements:
- Output valid JSON only.
- `exact_queries` should keep precise identifiers, errors, and file/path references.
- `semantic_queries` should describe the behavior or failure mode in more flexible language.
- `symbol_queries` should focus on function, method, class, field, or helper names.
- `graph_queries` should focus on caller/callee, shared-state, routing, or neighboring-module exploration.
- `config_queries` should focus on thresholds, limits, flags, defaults, policy values, or workflow settings.
- Keep each query short and retrieval-ready.
- Do not include more than 8 items in any list.

Schema:
{{
  "retrieval_objective": string,
  "exact_queries": [string],
  "semantic_queries": [string],
  "symbol_queries": [string],
  "graph_queries": [string],
  "config_queries": [string]
}}

Issue:
{issue.title}

Log excerpt:
{issue.log_excerpt or "N/A"}

Stacktrace excerpt:
{issue.stacktrace_excerpt or "N/A"}

State views read for this stage:
{context_text or "(no additional state views)"}
"""

    @staticmethod
    def build_evidence_screen(task: PreparedBugTriageTask, context_text: str) -> str:
        issue = task.issue
        return f"""You are the `evidence_screen` skill.

You will receive extracted bug signals, the query-expansion plan, and retrieved repo evidence.
Your job is to convert the raw repo evidence into a compact structured evidence screen for downstream reasoning.

Requirements:
- Output valid JSON only.
- `applied_evidence_labels` must contain only labels from this list: {BugTriagePromptBuilder._enum_text(BugTriagePromptBuilder.EVIDENCE_LABEL_OPTIONS)}.
- `candidate_files` should contain at most 5 entries.
- Every `evidence_id` and `file_path` must come from the provided context.
- Rank candidates instead of hard-filtering aggressively. Keep plausible files when the evidence is still ambiguous.
- Prefer files that likely contain the smallest viable edit locus, including shared helpers or workflow/state files when they appear to own the faulty behavior.
- `relevance_reason` should explain why the file matters for this issue.
- `suspicious_signals` should stay concrete and evidence-backed.
- `missing_evidence` should mention only evidence gaps that would materially improve confidence.
- Set `reretrieval_needed` to true only if the current candidate pool is missing a clearly needed evidence slice.
- `additional_queries` should contain short follow-up retrieval prompts when reretrieval is needed.

{BugTriagePromptBuilder._evidence_label_rubric()}

Schema:
{{
  "evidence_summary": string,
  "applied_evidence_labels": [enum],
  "candidate_files": [
    {{
      "evidence_id": string,
      "file_path": string,
      "relevance_reason": string,
      "suspicious_signals": [string]
    }}
  ],
  "missing_evidence": [string],
  "reretrieval_needed": boolean,
  "additional_queries": [string]
}}

Issue:
{issue.title}

State views read for this stage:
{context_text or "(no additional state views)"}
"""

    @staticmethod
    def build_hypothesis_shortlist(task: PreparedBugTriageTask, context_text: str) -> str:
        issue = task.issue
        return f"""You are the `hypothesis_shortlist` skill.

You will receive extracted bug signals, a structured evidence screen, and selected retrieved evidence.
Generate a short list of plausible root-cause hypotheses before the final triage decision.

Requirements:
- Output valid JSON only.
- `hypotheses` should contain 2 or 3 entries when possible.
- `hypothesis_label` should be short and concrete.
- `primary_evidence_id` must come from the evidence screen.
- `primary_file` must copy the exact `file_path` string associated with that evidence id.
- Do not output a bare evidence id or a shortened basename in `primary_file`.
- `root_cause_category` must be exactly one of: {BugTriagePromptBuilder._enum_text(BugTriagePromptBuilder.ROOT_CAUSE_CATEGORY_OPTIONS)}.
- `supporting_evidence` should cite evidence ids or file paths from the structured evidence state or the retrieved repo context.

{BugTriagePromptBuilder._root_cause_rubric()}

Schema:
{{
  "shortlist_rationale": string,
  "hypotheses": [
    {{
      "hypothesis_label": string,
      "primary_evidence_id": string,
      "primary_file": string,
      "root_cause_category": string,
      "explanation": string,
      "supporting_evidence": [string]
    }}
  ]
}}

Issue:
{issue.title}

State views read for this stage:
{context_text or "(no additional state views)"}
"""

    @staticmethod
    def build_triage_decision(task: PreparedBugTriageTask, context_text: str) -> str:
        issue = task.issue
        return f"""You are the `triage_decision` skill.

You will receive extracted bug signals and a structured hypothesis shortlist.
Choose the strongest current root-cause direction.

Requirements:
- Output valid JSON only.
- `primary_evidence_id` should match the best-supported hypothesis.
- `primary_file` should copy the exact `file_path` string associated with that evidence id.
- Do not output only an evidence id or only a basename in `primary_file`.
- `root_cause_category` must be exactly one of: {BugTriagePromptBuilder._enum_text(BugTriagePromptBuilder.ROOT_CAUSE_CATEGORY_OPTIONS)}.
- `root_cause_category` should reuse the shortlist category rather than inventing a new taxonomy.
- `proposed_fix` should describe a concrete repair direction, not a full patch.
- `supporting_evidence` should contain evidence ids or file paths that appeared in prior state.
- Keep `alternatives` to 1 or 2 items.
- `validation_steps` should explain how to verify the fix.
- `confidence` should be a number between 0 and 1.

{BugTriagePromptBuilder._root_cause_rubric()}

Schema:
{{
  "primary_evidence_id": string,
  "primary_file": string,
  "root_cause_category": string,
  "root_cause": string,
  "proposed_fix": string,
  "rationale": string,
  "supporting_evidence": [string],
  "alternatives": [string],
  "validation_steps": [string],
  "confidence": number
}}

Issue:
{issue.title}

State views read for this stage:
{context_text or "(no additional state views)"}
"""

    @staticmethod
    def build_fix_plan(task: PreparedBugTriageTask, context_text: str) -> str:
        issue = task.issue
        return f"""You are the `fix_plan` skill.

You will receive the structured triage decision plus the minimum supporting state needed to plan the repair.
Turn that into a compact implementation and validation plan.

Requirements:
- Output valid JSON only.
- `patch_scope` should name the files or code areas likely to change.
- `implementation_steps` should be concrete and ordered.
- `validation_steps` should reinforce or refine the decision stage's checks.
- `regression_risks` should mention behaviors that could be broken by the fix.
- `rollout_notes` should contain follow-up or communication guidance only when useful.

Schema:
{{
  "patch_scope": [string],
  "implementation_steps": [string],
  "validation_steps": [string],
  "regression_risks": [string],
  "rollout_notes": [string]
}}

Issue:
{issue.title}

State views read for this stage:
{context_text or "(no additional state views)"}
"""

    @staticmethod
    def build_review(task: PreparedBugTriageTask, context_text: str) -> str:
        issue = task.issue
        return f"""You are the `triage_review` skill.

You will receive the current triage decision, the follow-on fix plan, and the minimal upstream checks.
Check for:
- whether the evidence chain is sufficient
- whether `primary_file` is still supported
- whether the fix plan and validation steps are specific enough

Requirements:
- Output valid JSON only.
- Set `decision_ok` to true if the current triage is basically sound.
- If revising the file choice, fill both `revised_primary_evidence_id` and `revised_primary_file`.
- `revised_primary_file` must copy the exact file path string tied to that evidence id.
- Fill `revised_fix` only when a small correction is truly needed.
- Do not rewrite the full triage response.

Schema:
{{
  "decision_ok": boolean,
  "review_notes": [string],
  "revised_primary_evidence_id": string,
  "revised_primary_file": string,
  "revised_fix": string,
  "missing_evidence": [string],
  "residual_risks": [string]
}}

Issue:
{issue.title}

State views read for this stage:
{context_text or "(no additional state views)"}
"""

    @staticmethod
    def build_dependency_plan(
        task: PreparedBugTriageTask,
        stage_name: str,
        available_reads_text: str,
    ) -> str:
        issue = task.issue
        return f"""You are the `structured_dependency_planner` skill.

Your job is to select the smallest additional set of structured reads needed for the current bug-triage stage.

Rules:
- Select `read_id` values only from the provided list.
- Do not select duplicates.
- Prefer concise structured reads before heavier full-code views.
- Return an empty list if the base dependencies are already enough.

Schema:
{{
  "selected_reads": [
    {{
      "read_id": string,
      "reason": string
    }}
  ],
  "notes": [string]
}}

Issue:
{issue.title}

Current stage:
{stage_name}

Optional additional reads:
{available_reads_text or "(no optional reads available)"}
"""

    @staticmethod
    def build_history_compression(
        task: PreparedBugTriageTask,
        stage_name: str,
        history_text: str,
    ) -> str:
        issue = task.issue
        return f"""You are the `history_compressor` skill.

Compress the bug-triage workflow's running history into structured working memory for the next stage.

Keep:
- the bug summary and expected behavior
- the retrieval objective and the highest-signal exact/symbol clues
- the evidence labels that describe the leading failure pattern
- the most relevant candidate files
- the leading hypotheses or chosen direction
- the validation focus and unresolved risks

Drop:
- repeated excerpts
- duplicated reasoning
- irrelevant code fragments

Requirements:
- Output valid JSON only.
- Keep every field concise.
- `applied_evidence_labels` must contain only labels from this list: {BugTriagePromptBuilder._enum_text(BugTriagePromptBuilder.EVIDENCE_LABEL_OPTIONS)}.
- `root_cause_category` values inside `hypotheses` must be exactly one of: {BugTriagePromptBuilder._enum_text(BugTriagePromptBuilder.ROOT_CAUSE_CATEGORY_OPTIONS)}.
- When a hypothesis refers to a candidate file, keep both `primary_evidence_id` and the exact `primary_file` path together.

Schema:
{{
  "bug_summary": string,
  "observed_behavior": string,
  "expected_behavior": string,
  "retrieval_objective": string,
  "applied_evidence_labels": [enum],
  "candidate_files": [string],
  "hypotheses": [
    {{
      "primary_evidence_id": string,
      "primary_file": string,
      "root_cause_category": string,
      "explanation": string,
      "supporting_evidence": [string]
    }}
  ],
  "chosen_direction": string,
  "validation_focus": [string],
  "open_risks": [string]
}}

Issue:
{issue.title}

Next stage:
{stage_name}

Current running history:
{history_text}
"""


class BugTriageTransferAdapter:
    """Task-specific bug-triage semantics plugged into shared transfer methods."""

    def __init__(self, prompts: BugTriagePromptBuilder, include_review: bool = False) -> None:
        self.prompts = prompts
        self.include_review = include_review

    @staticmethod
    def _bullet_lines(items: Sequence[str], empty_text: str = "N/A") -> str:
        values = [item.strip() for item in items if item and item.strip()]
        if not values:
            return empty_text
        return "\n".join(f"- {item}" for item in values)

    @staticmethod
    def _json_view(payload: Dict) -> str:
        return compact_json(payload)

    @staticmethod
    def _rule_extract_signals(issue: BugTriageIssue) -> Dict[str, List[str]]:
        return BugTriageWorkflow._rule_extract_signals(issue)

    @staticmethod
    def _render_rule_signals(rule_signals: Dict[str, List[str]]) -> str:
        return BugTriageWorkflow._render_rule_signals(rule_signals)

    def _render_compressed_memory(self, memory: BugTriageCompressedMemory) -> str:
        return self._json_view(memory.model_dump())

    def _structured_evidence_artifact(self, evidence: FileEvidence, method_name: str) -> StoredStateArtifact:
        payload = {
            "evidence_id": evidence.evidence_id,
            "file_path": evidence.file_path,
            "matched_terms": evidence.matched_terms,
            "source_kinds": evidence.source_kinds,
            "line_numbers": evidence.line_numbers,
            "excerpt_count": len(evidence.excerpts),
        }
        snippet_heads = [excerpt.splitlines()[0] for excerpt in evidence.excerpts if excerpt]
        supporting = "\n".join(
            [
                f"evidence_id: {evidence.evidence_id}",
                f"file: {evidence.file_path}",
                f"matched_terms: {', '.join(evidence.matched_terms) or 'N/A'}",
                f"source_kinds: {', '.join(evidence.source_kinds) or 'N/A'}",
                f"line_numbers: {', '.join(str(line) for line in evidence.line_numbers) or 'N/A'}",
                "snippet_heads:",
                self._bullet_lines(snippet_heads),
            ]
        )
        full = json.dumps(
            {
                "evidence_id": evidence.evidence_id,
                "file_path": evidence.file_path,
                "matched_terms": evidence.matched_terms,
                "source_kinds": evidence.source_kinds,
                "line_numbers": evidence.line_numbers,
                "excerpts": evidence.excerpts,
            },
            ensure_ascii=False,
            indent=2,
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:repo_context:{evidence.file_path}",
            method_name=method_name,
            stage_name="repo_context",
            artifact_type="repo_context",
            label=evidence.file_path,
            payload_format="json",
            payload=payload,
            views={
                "brief": self._json_view(payload),
                "supporting": supporting,
                "full": full,
            },
        )

    def _signal_artifact(
        self,
        signals: BugSignalExtractionState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = signals.model_dump()
        return StoredStateArtifact(
            artifact_id=f"{method_name}:signal_state",
            method_name=method_name,
            stage_name="signal_extractor",
            artifact_type="signal_state",
            label="signal_state",
            payload_format="json",
            payload=payload,
            views={
                "summary": (
                    f"bug_summary: {signals.bug_summary}\n"
                    f"observed_behavior: {signals.observed_behavior or 'N/A'}\n"
                    f"expected_behavior: {signals.expected_behavior or 'N/A'}\n"
                    f"exception_types: {', '.join(signals.exception_types) or 'N/A'}\n"
                    f"file_hints: {', '.join(signals.file_hints) or 'N/A'}\n"
                    f"symbol_hints: {', '.join(signals.symbol_hints) or 'N/A'}\n"
                    f"suspected_modules:\n{self._bullet_lines(signals.suspected_modules)}\n"
                    f"related_concepts:\n{self._bullet_lines(signals.related_concepts)}"
                ),
                "checks": self._bullet_lines(signals.acceptance_checks),
                "full_json": self._json_view(payload),
            },
        )

    def _query_artifact(
        self,
        query_state: BugQueryExpansionState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = query_state.model_dump()
        summary = "\n".join(
            [
                f"retrieval_objective: {query_state.retrieval_objective or 'N/A'}",
                f"exact_queries:\n{self._bullet_lines(query_state.exact_queries)}",
                f"semantic_queries:\n{self._bullet_lines(query_state.semantic_queries)}",
                f"symbol_queries:\n{self._bullet_lines(query_state.symbol_queries)}",
                f"graph_queries:\n{self._bullet_lines(query_state.graph_queries)}",
                f"config_queries:\n{self._bullet_lines(query_state.config_queries)}",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:query_state",
            method_name=method_name,
            stage_name="query_expansion",
            artifact_type="query_state",
            label="query_state",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def _evidence_state_artifact(
        self,
        evidence_state: BugEvidenceState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = evidence_state.model_dump()
        candidate_lines = []
        for candidate in evidence_state.candidate_files:
            candidate_lines.append(
                f"- {candidate.file_path} [{candidate.evidence_id}]: {candidate.relevance_reason}"
            )
            for signal in candidate.suspicious_signals:
                candidate_lines.append(f"  - signal: {signal}")
        summary = "\n".join(
            [
                f"evidence_summary: {evidence_state.evidence_summary}",
                f"applied_evidence_labels: {', '.join(evidence_state.applied_evidence_labels) or 'N/A'}",
                "candidate_files:",
                "\n".join(candidate_lines) if candidate_lines else "- N/A",
                f"missing_evidence:\n{self._bullet_lines(evidence_state.missing_evidence)}",
                f"reretrieval_needed: {evidence_state.reretrieval_needed}",
                f"additional_queries:\n{self._bullet_lines(evidence_state.additional_queries)}",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:evidence_state",
            method_name=method_name,
            stage_name="evidence_screen",
            artifact_type="evidence_state",
            label="evidence_state",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def _hypothesis_state_artifact(
        self,
        hypothesis_state: BugHypothesisState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = hypothesis_state.model_dump()
        hypothesis_lines = []
        for hypothesis in hypothesis_state.hypotheses:
            hypothesis_lines.append(
                f"- {hypothesis.hypothesis_label}: [{hypothesis.primary_evidence_id or 'N/A'}] "
                f"{hypothesis.primary_file} ({hypothesis.root_cause_category})"
            )
            hypothesis_lines.append(f"  - explanation: {hypothesis.explanation}")
            if hypothesis.supporting_evidence:
                hypothesis_lines.append(
                    f"  - supporting_evidence: {', '.join(hypothesis.supporting_evidence)}"
                )
        summary = "\n".join(
            [
                f"shortlist_rationale: {hypothesis_state.shortlist_rationale}",
                "hypotheses:",
                "\n".join(hypothesis_lines) if hypothesis_lines else "- N/A",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:hypothesis_state",
            method_name=method_name,
            stage_name="hypothesis_shortlist",
            artifact_type="hypothesis_state",
            label="hypothesis_state",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def _decision_artifact(
        self,
        decision: TriageDecisionState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = decision.model_dump()
        summary = (
            f"primary_evidence_id: {decision.primary_evidence_id or 'N/A'}\n"
            f"primary_file: {decision.primary_file}\n"
            f"root_cause_category: {decision.root_cause_category}\n"
            f"root_cause: {decision.root_cause}\n"
            f"proposed_fix: {decision.proposed_fix}\n"
            f"supporting_evidence: {', '.join(decision.supporting_evidence) or 'N/A'}\n"
            f"confidence: {decision.confidence:.2f}"
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:decision_state",
            method_name=method_name,
            stage_name="triage_decision",
            artifact_type="decision_state",
            label="decision_state",
            payload_format="json",
            payload=payload,
            views={
                "summary": summary,
                "risks": self._bullet_lines(decision.alternatives),
                "full_json": self._json_view(payload),
            },
        )

    def _plan_artifact(
        self,
        plan: BugFixPlanState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = plan.model_dump()
        summary = "\n".join(
            [
                f"patch_scope:\n{self._bullet_lines(plan.patch_scope)}",
                f"implementation_steps:\n{self._bullet_lines(plan.implementation_steps)}",
                f"validation_steps:\n{self._bullet_lines(plan.validation_steps)}",
                f"regression_risks:\n{self._bullet_lines(plan.regression_risks)}",
            ]
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:plan_state",
            method_name=method_name,
            stage_name="fix_plan",
            artifact_type="plan_state",
            label="plan_state",
            payload_format="json",
            payload=payload,
            views={"summary": summary, "full_json": self._json_view(payload)},
        )

    def _review_artifact(
        self,
        review: TriageReviewState,
        method_name: str,
    ) -> StoredStateArtifact:
        payload = review.model_dump()
        summary = (
            f"decision_ok: {review.decision_ok}\n"
            f"review_notes:\n{self._bullet_lines(review.review_notes)}\n"
            f"revised_primary_evidence_id: {review.revised_primary_evidence_id or 'N/A'}\n"
            f"revised_primary_file: {review.revised_primary_file or 'N/A'}"
        )
        return StoredStateArtifact(
            artifact_id=f"{method_name}:review_state",
            method_name=method_name,
            stage_name="review",
            artifact_type="review_state",
            label="review_state",
            payload_format="json",
            payload=payload,
            views={
                "summary": summary,
                "full_json": self._json_view(payload),
            },
        )

    def build_structured_store(self, task: PreparedBugTriageTask, method_name: str) -> StructuredStateStore:
        return StructuredStateStore(method_name)

    def build_history_seed(self, task: PreparedBugTriageTask) -> str:
        issue = task.issue
        sections = [
            f"[issue_title]\n{issue.title}",
            f"[issue_description]\n{issue.description}",
            f"[expected_behavior]\n{issue.expected_behavior or 'N/A'}",
        ]
        if issue.log_excerpt:
            sections.append(f"[log_excerpt]\n{issue.log_excerpt}")
        if issue.stacktrace_excerpt:
            sections.append(f"[stacktrace_excerpt]\n{issue.stacktrace_excerpt}")
        return "\n\n".join(section for section in sections if section.strip())

    def initial_stage(self) -> InitialStructuredStage[PreparedBugTriageTask]:
        return InitialStructuredStage(
            stage_name="signal_extractor",
            result_field="signal_state",
            schema_model=BugSignalExtractionState,
            prompt_builder=lambda task: self.prompts.build_signal_extractor(
                task.issue,
                self._render_rule_signals(self._rule_extract_signals(task.issue)),
            ),
            artifact_builder=self._signal_artifact,
            max_tokens=320,
            notes=("Extracted structured bug signals from the raw issue.",),
        )

    def routed_stages(self) -> Sequence[RoutedStructuredStage[PreparedBugTriageTask]]:
        stages: List[RoutedStructuredStage[PreparedBugTriageTask]] = [
            RoutedStructuredStage(
                stage_name="query_expansion",
                result_field="query_state",
                schema_model=BugQueryExpansionState,
                prompt_builder=lambda task, context: self.prompts.build_query_expansion(task, context),
                artifact_builder=self._query_artifact,
                max_tokens=320,
            ),
            RoutedStructuredStage(
                stage_name="evidence_screen",
                result_field="evidence_state",
                schema_model=BugEvidenceState,
                prompt_builder=lambda task, context: self.prompts.build_evidence_screen(task, context),
                artifact_builder=self._evidence_state_artifact,
                max_tokens=520,
            ),
            RoutedStructuredStage(
                stage_name="hypothesis_shortlist",
                result_field="hypothesis_state",
                schema_model=BugHypothesisState,
                prompt_builder=lambda task, context: self.prompts.build_hypothesis_shortlist(task, context),
                artifact_builder=self._hypothesis_state_artifact,
                max_tokens=420,
            ),
            RoutedStructuredStage(
                stage_name="triage_decision",
                result_field="decision_state",
                schema_model=TriageDecisionState,
                prompt_builder=lambda task, context: self.prompts.build_triage_decision(task, context),
                artifact_builder=self._decision_artifact,
                max_tokens=420,
            ),
            RoutedStructuredStage(
                stage_name="fix_plan",
                result_field="plan_state",
                schema_model=BugFixPlanState,
                prompt_builder=lambda task, context: self.prompts.build_fix_plan(task, context),
                artifact_builder=self._plan_artifact,
                max_tokens=360,
            ),
        ]
        if self.include_review:
            stages.append(
                RoutedStructuredStage(
                    stage_name="review",
                    result_field="review_state",
                    schema_model=TriageReviewState,
                    prompt_builder=lambda task, context: self.prompts.build_review(task, context),
                    artifact_builder=self._review_artifact,
                    max_tokens=240,
                )
            )
        return tuple(stages)

    @staticmethod
    def fixed_dependency_contracts() -> Dict[str, StageDependencyContract]:
        return {
            "query_expansion": StageDependencyContract(
                stage_name="query_expansion",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="signal_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                ],
            ),
            "evidence_screen": StageDependencyContract(
                stage_name="evidence_screen",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="signal_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="query_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="repo_context",
                        view_name="supporting",
                        required=True,
                        max_items=8,
                    ),
                ],
            ),
            "hypothesis_shortlist": StageDependencyContract(
                stage_name="hypothesis_shortlist",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="signal_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="evidence_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="repo_context",
                        view_name="supporting",
                        required=True,
                        max_items=4,
                    ),
                ],
            ),
            "triage_decision": StageDependencyContract(
                stage_name="triage_decision",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="signal_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="hypothesis_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                ],
            ),
            "fix_plan": StageDependencyContract(
                stage_name="fix_plan",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="decision_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="evidence_state",
                        view_name="summary",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="signal_state",
                        view_name="checks",
                        required=True,
                        max_items=1,
                    ),
                ],
            ),
            "review": StageDependencyContract(
                stage_name="review",
                required_dependencies=[
                    StateDependencySpec(
                        artifact_type="decision_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="plan_state",
                        view_name="full_json",
                        required=True,
                        max_items=1,
                    ),
                    StateDependencySpec(
                        artifact_type="signal_state",
                        view_name="checks",
                        required=True,
                        max_items=1,
                    ),
                ],
            ),
        }

    @staticmethod
    def planner_optional_specs(stage_name: str) -> Sequence[StateDependencySpec]:
        if stage_name == "query_expansion":
            return (
                StateDependencySpec(artifact_type="signal_state", view_name="summary", max_items=1),
            )
        if stage_name == "evidence_screen":
            return (
                StateDependencySpec(artifact_type="repo_context", view_name="full", max_items=5),
                StateDependencySpec(artifact_type="signal_state", view_name="checks", max_items=1),
            )
        if stage_name == "hypothesis_shortlist":
            return (
                StateDependencySpec(artifact_type="repo_context", view_name="supporting", max_items=4),
                StateDependencySpec(artifact_type="repo_context", view_name="full", max_items=2),
                StateDependencySpec(artifact_type="query_state", view_name="summary", max_items=1),
            )
        if stage_name == "triage_decision":
            return (
                StateDependencySpec(artifact_type="evidence_state", view_name="summary", max_items=1),
                StateDependencySpec(artifact_type="repo_context", view_name="supporting", max_items=3),
            )
        if stage_name == "fix_plan":
            return (
                StateDependencySpec(artifact_type="hypothesis_state", view_name="summary", max_items=1),
                StateDependencySpec(artifact_type="repo_context", view_name="supporting", max_items=2),
            )
        return (
            StateDependencySpec(artifact_type="evidence_state", view_name="summary", max_items=1),
            StateDependencySpec(artifact_type="hypothesis_state", view_name="summary", max_items=1),
            StateDependencySpec(artifact_type="repo_context", view_name="supporting", max_items=2),
        )

    def build_planner_prompt(
        self,
        task: PreparedBugTriageTask,
        stage_name: str,
        available_reads_text: str,
    ) -> str:
        return self.prompts.build_dependency_plan(task, stage_name, available_reads_text)

    def build_history_compression_prompt(
        self,
        task: PreparedBugTriageTask,
        next_stage_name: str,
        history_text: str,
    ) -> str:
        return self.prompts.build_history_compression(task, next_stage_name, history_text)

    def history_compression_config(self):
        return default_history_compression_config(
            schema_model=BugTriageCompressedMemory,
            render_model=self._render_compressed_memory,
            skip_stage_names=("query_expansion", "evidence_screen"),
        )


class BugTriageWorkflow:
    """Run the local code-triage state-transfer experiment."""

    TEXT_EXTENSIONS = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".go",
        ".rs",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".cc",
        ".cs",
        ".rb",
        ".php",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".sh",
    }

    def __init__(
        self,
        runner,
        max_search_hits: int = 20,
        max_files: int = 8,
        snippet_context_lines: int = 6,
        include_review: bool = False,
        max_reretrieval_rounds: int = 2,
    ) -> None:
        self.runner = runner
        self.prompts = BugTriagePromptBuilder()
        self.adapter = BugTriageTransferAdapter(self.prompts, include_review=include_review)
        self.runtime = TransferMethodRuntime(
            runner,
            token_counter=getattr(runner, "count_tokens", count_tokens_simple),
        )
        self._active_policy: Optional[SLOPolicyConfig] = None
        self.max_search_hits = max_search_hits
        self.max_files = max_files
        self.snippet_context_lines = snippet_context_lines
        self.max_reretrieval_rounds = max_reretrieval_rounds
        self._active_repo_subpaths: List[str] = []

    @staticmethod
    def load_issues(path: str) -> List[BugTriageIssue]:
        source = Path(path).expanduser().resolve()
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".jsonl":
            payloads = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            raw = json.loads(text)
            payloads = raw["issues"] if isinstance(raw, dict) and "issues" in raw else raw
        return [BugTriageIssue.model_validate(item) for item in payloads]

    @staticmethod
    def _issue_dir(base_output_dir: Path, issue_id: str) -> Path:
        safe_name = re.sub(r"[^0-9A-Za-z._-]+", "_", issue_id).strip("._") or "issue"
        return base_output_dir / safe_name

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _dedupe_keep_order(values: Sequence[str], *, min_len: int = 1, max_items: Optional[int] = None) -> List[str]:
        results: List[str] = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            lowered = text.lower()
            if len(text) < min_len or lowered in seen:
                continue
            seen.add(lowered)
            results.append(text)
            if max_items is not None and len(results) >= max_items:
                break
        return results

    @staticmethod
    def _rule_extract_signals(issue: BugTriageIssue) -> Dict[str, List[str]]:
        text = " ".join(
            part
            for part in (
                issue.title,
                issue.description,
                issue.expected_behavior,
                issue.log_excerpt,
                issue.stacktrace_excerpt,
            )
            if part
        )
        error_messages = re.findall(r"'([^']{4,120})'|\"([^\"]{4,120})\"", text)
        exception_types = re.findall(r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception|Failure)\b", text)
        file_hints = re.findall(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|json|yaml|yml|toml|ini|cfg)\b", text)
        path_hints = [value for value in file_hints if "/" in value or "\\" in value]
        symbol_hints = re.findall(r"\b[a-zA-Z_][A-Za-z0-9_]{2,}\b", text)
        error_codes = re.findall(r"\b(?:[A-Z]{2,}[-_]\d+|HTTP ?\d{3}|[A-Z_]{3,}\d{2,})\b", text)
        normalized_error_messages = [
            first or second
            for first, second in error_messages
            if (first or second) and len((first or second).strip()) >= 4
        ]
        return {
            "error_messages": BugTriageWorkflow._dedupe_keep_order(normalized_error_messages, min_len=4, max_items=6),
            "exception_types": BugTriageWorkflow._dedupe_keep_order(exception_types, min_len=4, max_items=6),
            "file_hints": BugTriageWorkflow._dedupe_keep_order(file_hints, min_len=4, max_items=8),
            "path_hints": BugTriageWorkflow._dedupe_keep_order(path_hints, min_len=4, max_items=8),
            "symbol_hints": BugTriageWorkflow._dedupe_keep_order(symbol_hints, min_len=3, max_items=10),
            "error_codes": BugTriageWorkflow._dedupe_keep_order(error_codes, min_len=3, max_items=6),
        }

    @staticmethod
    def _render_rule_signals(rule_signals: Dict[str, List[str]]) -> str:
        sections = []
        for key in (
            "error_messages",
            "exception_types",
            "file_hints",
            "path_hints",
            "symbol_hints",
            "error_codes",
        ):
            values = rule_signals.get(key, [])
            sections.append(f"{key}: {', '.join(values) or 'N/A'}")
        return "\n".join(sections)

    def _retrieval_queries(
        self,
        task: PreparedBugTriageTask,
        signal_state: Optional[Dict],
        query_state: Optional[Dict],
        *,
        additional_queries: Optional[Sequence[str]] = None,
    ) -> Dict[str, List[str]]:
        issue = task.issue
        rule_signals = self._rule_extract_signals(issue)
        signal_state = signal_state or {}
        query_state = query_state or {}
        extra_queries = [str(value) for value in (additional_queries or []) if str(value).strip()]

        exact_queries = self._dedupe_keep_order(
            rule_signals["error_messages"]
            + rule_signals["exception_types"]
            + rule_signals["file_hints"]
            + rule_signals["path_hints"]
            + rule_signals["error_codes"]
            + list(signal_state.get("error_messages") or [])
            + list(signal_state.get("exception_types") or [])
            + list(signal_state.get("file_hints") or [])
            + list(signal_state.get("path_hints") or [])
            + list(query_state.get("exact_queries") or [])
            + extra_queries,
            min_len=2,
            max_items=12,
        )
        symbol_queries = self._dedupe_keep_order(
            rule_signals["symbol_hints"]
            + list(signal_state.get("symbol_hints") or [])
            + list(query_state.get("symbol_queries") or []),
            min_len=3,
            max_items=10,
        )
        semantic_queries = self._dedupe_keep_order(
            list(signal_state.get("related_concepts") or [])
            + list(signal_state.get("synonym_terms") or [])
            + list(query_state.get("semantic_queries") or [])
            + extra_queries,
            min_len=3,
            max_items=10,
        )
        graph_queries = self._dedupe_keep_order(
            list(signal_state.get("suspected_modules") or [])
            + list(query_state.get("graph_queries") or [])
            + extra_queries,
            min_len=3,
            max_items=8,
        )
        config_queries = self._dedupe_keep_order(
            list(query_state.get("config_queries") or [])
            + list(signal_state.get("error_codes") or [])
            + extra_queries,
            min_len=3,
            max_items=8,
        )
        return {
            "exact": exact_queries,
            "symbol": symbol_queries,
            "semantic": semantic_queries,
            "graph": graph_queries,
            "config": config_queries,
        }

    def _collect_search_terms(self, issue: BugTriageIssue) -> List[str]:
        terms: List[str] = []
        seen = set()
        fallback_text = " ".join(
            part
            for part in (
                issue.title,
                issue.description,
                issue.expected_behavior,
                issue.log_excerpt,
                issue.stacktrace_excerpt,
            )
            if part
        )
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]+", fallback_text):
            lowered = token.lower()
            if lowered in seen or len(token) < 3:
                continue
            seen.add(lowered)
            terms.append(token)
            if len(terms) >= 8:
                break
        return terms

    @staticmethod
    def _tokenize_query_text(queries: Sequence[str]) -> List[str]:
        tokens: List[str] = []
        for query in queries:
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]+", str(query or "")):
                if len(token) >= 3:
                    tokens.append(token)
        return tokens

    @staticmethod
    def _file_priority_score(file_path: str, repo_subpaths: Sequence[str]) -> int:
        normalized = file_path.replace("\\", "/")
        name = Path(normalized).name
        score = 0
        normalized_subpaths = [
            subpath.replace("\\", "/").strip("./")
            for subpath in repo_subpaths
            if subpath and subpath.strip(". /")
        ]
        if normalized_subpaths:
            if any(
                normalized == subpath or normalized.startswith(f"{subpath}/")
                for subpath in normalized_subpaths
            ):
                score += 4
        if normalized.startswith(("examples/", "docs/", "runs/")):
            score -= 4
        if name == "__init__.py":
            score -= 3
        if name.startswith("run_"):
            score -= 2
        if name in {"README.md", "pyproject.toml"}:
            score -= 2
        return score

    @staticmethod
    def _source_kind_rank(source_kinds: Sequence[str]) -> int:
        weights = {
            "exact": 5,
            "symbol": 4,
            "config": 4,
            "semantic": 3,
            "graph": 2,
            "file_expansion": 1,
        }
        return sum(weights.get(kind, 0) for kind in set(source_kinds))

    @staticmethod
    def _should_include_search_path(file_path: str) -> bool:
        normalized = file_path.replace("\\", "/").strip()
        excluded_prefixes = (
            ".git/",
            ".venv/",
            "__pycache__/",
            ".mypy_cache/",
            ".pytest_cache/",
            "runs/",
            "examples/",
            "docs/",
        )
        return not normalized.startswith(excluded_prefixes)

    def _rg_search(
        self,
        repo_root: Path,
        terms: Sequence[str],
        subpaths: Sequence[str],
        source_kind: str,
    ) -> List[Tuple[str, int, str, str, str]]:
        hits: List[Tuple[str, int, str, str, str]] = []
        seen = set()
        targets = list(subpaths) if subpaths else ["."]
        per_term_limit = max(4, self.max_search_hits)
        total_limit = max(self.max_search_hits * 4, per_term_limit)
        for term in terms:
            candidate_paths: List[str] = []
            cmd = ["rg", "-l", "-i", "--color", "never", term, *targets]
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=repo_root,
                )
            except FileNotFoundError:
                return []
            if completed.returncode not in (0, 1):
                continue
            for line in completed.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    relative_path = str(Path(line).resolve().relative_to(repo_root))
                except ValueError:
                    continue
                if not self._should_include_search_path(relative_path):
                    continue
                candidate_paths.append(relative_path)
                if len(candidate_paths) >= per_term_limit:
                    break
            for relative_path in candidate_paths:
                match_cmd = [
                    "rg",
                    "-n",
                    "-i",
                    "-m",
                    "2",
                    "--no-heading",
                    "--color",
                    "never",
                    term,
                    str((repo_root / relative_path).resolve()),
                ]
                matched = subprocess.run(
                    match_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=repo_root,
                )
                if matched.returncode not in (0, 1):
                    continue
                for match_line in matched.stdout.splitlines():
                    parts = match_line.split(":", 2)
                    if not parts:
                        continue
                    if parts[0].isdigit():
                        line_text = parts[0]
                        matched_line = ":".join(parts[1:])
                    elif len(parts) >= 2 and parts[1].isdigit():
                        line_text = parts[1]
                        matched_line = ":".join(parts[2:])
                    else:
                        continue
                    try:
                        line_number = int(line_text)
                    except ValueError:
                        continue
                    key = (relative_path, line_number, term.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append((relative_path, line_number, matched_line.strip(), term, source_kind))
                    if len(hits) >= total_limit:
                        return hits
        return hits

    def _python_search(
        self,
        repo_root: Path,
        terms: Sequence[str],
        subpaths: Sequence[str],
        source_kind: str,
    ) -> List[Tuple[str, int, str, str, str]]:
        hits: List[Tuple[str, int, str, str, str]] = []
        seen = set()
        roots = [repo_root / subpath for subpath in subpaths] if subpaths else [repo_root]
        per_term_limit = max(4, self.max_search_hits)
        total_limit = max(self.max_search_hits * 4, per_term_limit)
        for base in roots:
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in self.TEXT_EXTENSIONS:
                    continue
                relative_path = str(path.resolve().relative_to(repo_root.resolve()))
                if not self._should_include_search_path(relative_path):
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                lines = text.splitlines()
                lowered_lines = [line.lower() for line in lines]
                for term in terms:
                    term_hits = 0
                    lowered_term = term.lower()
                    for idx, lowered_line in enumerate(lowered_lines, start=1):
                        if lowered_term in lowered_line:
                            key = (relative_path, idx, lowered_term)
                            if key in seen:
                                continue
                            seen.add(key)
                            hits.append((relative_path, idx, lines[idx - 1].strip(), term, source_kind))
                            term_hits += 1
                            if len(hits) >= total_limit:
                                return hits
                            if term_hits >= per_term_limit:
                                break
        return hits

    def _read_excerpt(self, path: Path, line_number: int) -> str:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return ""
        start = max(1, line_number - self.snippet_context_lines)
        end = min(len(lines), line_number + self.snippet_context_lines)
        excerpt_lines = []
        for current in range(start, end + 1):
            marker = ">" if current == line_number else " "
            excerpt_lines.append(f"{marker}{current:04d}: {lines[current - 1]}")
        return "\n".join(excerpt_lines)

    def _search_with_fallback(
        self,
        repo_root: Path,
        terms: Sequence[str],
        subpaths: Sequence[str],
        source_kind: str,
    ) -> List[Tuple[str, int, str, str, str]]:
        raw_hits = self._rg_search(repo_root, terms, subpaths, source_kind)
        if not raw_hits:
            raw_hits = self._python_search(repo_root, terms, subpaths, source_kind)
        return raw_hits

    def _materialize_hits(
        self,
        repo_root: Path,
        raw_hits: Sequence[Tuple[str, int, str, str, str]],
    ) -> List[SearchHit]:
        seen = set()
        hits: List[SearchHit] = []
        for file_path, line_number, matched_line, term, source_kind in raw_hits:
            key = (file_path, line_number, term, source_kind)
            if key in seen:
                continue
            seen.add(key)
            excerpt = self._read_excerpt(repo_root / file_path, line_number)
            hits.append(
                SearchHit(
                    file_path=file_path,
                    line_number=line_number,
                    matched_line=matched_line,
                    excerpt=excerpt,
                    matched_term=term,
                    source_kind=source_kind,
                )
            )
        return hits

    @staticmethod
    def _seed_files_from_hits(hits: Sequence[SearchHit], limit: int = 4) -> List[str]:
        ranked: Dict[str, Tuple[int, int]] = {}
        for hit in hits:
            matched_terms, line_count = ranked.get(hit.file_path, (0, 0))
            ranked[hit.file_path] = (matched_terms + 1, line_count + 1)
        ordered = sorted(
            ranked.items(),
            key=lambda item: (-item[1][0], -item[1][1], item[0]),
        )
        return [file_path for file_path, _ in ordered[:limit]]

    def _graph_expansion_hits(
        self,
        repo_root: Path,
        seed_files: Sequence[str],
        graph_queries: Sequence[str],
        subpaths: Sequence[str],
    ) -> List[SearchHit]:
        expanded: List[SearchHit] = []
        symbol_terms = self._tokenize_query_text(graph_queries)[:8]
        search_roots = list(subpaths) if subpaths else ["."]
        raw_hits = self._search_with_fallback(repo_root, symbol_terms, search_roots, "graph")
        expanded.extend(self._materialize_hits(repo_root, raw_hits))
        for file_path in seed_files:
            current = repo_root / file_path
            if not current.exists():
                continue
            parent = current.parent
            siblings = sorted(
                candidate
                for candidate in parent.glob("*.py")
                if candidate.is_file() and candidate != current
            )[:2]
            for sibling in siblings:
                relative_path = str(sibling.resolve().relative_to(repo_root))
                if not self._should_include_search_path(relative_path):
                    continue
                expanded.append(
                    SearchHit(
                        file_path=relative_path,
                        line_number=1,
                        matched_line="same-module expansion",
                        excerpt=self._read_excerpt(sibling, 1),
                        matched_term="same_module",
                        source_kind="graph",
                    )
                )
        return expanded

    def _file_expansion_hits(
        self,
        repo_root: Path,
        seed_files: Sequence[str],
    ) -> List[SearchHit]:
        expanded: List[SearchHit] = []
        for file_path in seed_files:
            current = repo_root / file_path
            if not current.exists():
                continue
            stem = current.stem
            candidates = [
                current.with_name(f"test_{stem}.py"),
                current.with_name(f"{stem}_test.py"),
                current.with_name(f"{stem}_tests.py"),
            ]
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                relative_path = str(candidate.resolve().relative_to(repo_root))
                if not self._should_include_search_path(relative_path):
                    continue
                expanded.append(
                    SearchHit(
                        file_path=relative_path,
                        line_number=1,
                        matched_line="related test expansion",
                        excerpt=self._read_excerpt(candidate, 1),
                        matched_term="related_test",
                        source_kind="file_expansion",
                    )
                )
        return expanded

    def _run_high_recall_retrieval(
        self,
        task: PreparedBugTriageTask,
        signal_state: Optional[Dict],
        query_state: Optional[Dict],
        *,
        additional_queries: Optional[Sequence[str]] = None,
    ) -> Tuple[List[SearchHit], List[FileEvidence], Dict[str, List[str]]]:
        query_groups = self._retrieval_queries(
            task,
            signal_state,
            query_state,
            additional_queries=additional_queries,
        )
        repo_root = task.repo_root
        subpaths = task.issue.repo_subpaths or ["."]
        self._active_repo_subpaths = list(subpaths)
        raw_hits: List[Tuple[str, int, str, str, str]] = []
        raw_hits.extend(self._search_with_fallback(repo_root, query_groups["exact"], subpaths, "exact"))
        raw_hits.extend(self._search_with_fallback(repo_root, query_groups["symbol"], subpaths, "symbol"))
        semantic_terms = self._tokenize_query_text(query_groups["semantic"])[:10]
        raw_hits.extend(self._search_with_fallback(repo_root, semantic_terms, subpaths, "semantic"))
        config_terms = self._tokenize_query_text(query_groups["config"])[:8]
        raw_hits.extend(self._search_with_fallback(repo_root, config_terms, subpaths, "config"))

        lexical_hits = self._materialize_hits(repo_root, raw_hits)
        seed_files = self._seed_files_from_hits(lexical_hits)
        graph_hits = self._graph_expansion_hits(repo_root, seed_files, query_groups["graph"], subpaths)
        file_hits = self._file_expansion_hits(repo_root, seed_files)
        combined_hits = lexical_hits + graph_hits + file_hits
        evidences = self._group_hits(combined_hits)
        return combined_hits, evidences, query_groups

    def _group_hits(self, hits: Sequence[SearchHit]) -> List[FileEvidence]:
        grouped_terms: Dict[str, List[str]] = defaultdict(list)
        grouped_sources: Dict[str, List[str]] = defaultdict(list)
        grouped_lines: Dict[str, List[int]] = defaultdict(list)
        grouped_excerpts: Dict[str, List[str]] = defaultdict(list)
        for hit in hits:
            if hit.matched_term not in grouped_terms[hit.file_path]:
                grouped_terms[hit.file_path].append(hit.matched_term)
            if hit.source_kind not in grouped_sources[hit.file_path]:
                grouped_sources[hit.file_path].append(hit.source_kind)
            if hit.line_number not in grouped_lines[hit.file_path]:
                grouped_lines[hit.file_path].append(hit.line_number)
            if hit.excerpt and len(grouped_excerpts[hit.file_path]) < 2:
                grouped_excerpts[hit.file_path].append(hit.excerpt)

        evidences = [
            FileEvidence(
                evidence_id="",
                file_path=file_path,
                matched_terms=grouped_terms[file_path],
                source_kinds=grouped_sources[file_path],
                line_numbers=sorted(grouped_lines[file_path]),
                excerpts=grouped_excerpts[file_path],
            )
            for file_path in grouped_terms
        ]
        evidences.sort(
            key=lambda item: (
                -len(item.matched_terms),
                -self._source_kind_rank(item.source_kinds),
                -len(item.source_kinds),
                -len(item.line_numbers),
                -self._file_priority_score(item.file_path, self._active_repo_subpaths),
                len(item.file_path),
                item.file_path,
            )
        )
        return [
            FileEvidence(
                evidence_id=f"E{index}",
                file_path=evidence.file_path,
                matched_terms=evidence.matched_terms,
                source_kinds=evidence.source_kinds,
                line_numbers=evidence.line_numbers,
                excerpts=evidence.excerpts,
            )
            for index, evidence in enumerate(evidences[: self.max_files], start=1)
        ]

    def _prepare_task(self, repo_root: Path, issue: BugTriageIssue) -> PreparedBugTriageTask:
        return PreparedBugTriageTask(repo_root=repo_root, issue=issue, search_hits=[], evidences=[])

    @staticmethod
    def _replace_artifacts(store: StructuredStateStore, artifact_type: str, replacements: Sequence[StoredStateArtifact]) -> None:
        store.artifacts = [artifact for artifact in store.artifacts if artifact.artifact_type != artifact_type]
        for artifact in replacements:
            store.add(artifact)

    @staticmethod
    def _merge_search_hits(existing: Sequence[SearchHit], new_hits: Sequence[SearchHit]) -> List[SearchHit]:
        merged: List[SearchHit] = []
        seen = set()
        for hit in list(existing) + list(new_hits):
            key = (hit.file_path, hit.line_number, hit.matched_term, hit.source_kind)
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
        return merged

    def _refresh_repo_context_artifacts(
        self,
        task: PreparedBugTriageTask,
        store: StructuredStateStore,
        method_name: str,
    ) -> None:
        repo_artifacts = [
            self.adapter._structured_evidence_artifact(evidence, method_name)
            for evidence in task.evidences
        ]
        self._replace_artifacts(store, "repo_context", repo_artifacts)

    @staticmethod
    def _retrieval_history_payload(
        task: PreparedBugTriageTask,
        query_groups: Dict[str, List[str]],
        stage_label: str,
    ) -> Dict[str, object]:
        return {
            "stage": stage_label,
            "retrieval_round": task.retrieval_round,
            "query_groups": query_groups,
            "retrieved_files": [evidence.file_path for evidence in task.evidences],
            "retrieved_hits_count": len(task.search_hits),
        }

    def _run_retrieval_stage(
        self,
        task: PreparedBugTriageTask,
        artifacts: StateTransferMethodArtifacts,
        store: StructuredStateStore,
        method_name: str,
        stage_name: str,
        *,
        additional_queries: Optional[Sequence[str]] = None,
    ) -> Dict[str, object]:
        signal_state = artifacts.signal_state or {}
        query_state = artifacts.query_state or {}
        start = time.perf_counter()
        remaining_before = remaining_slo_seconds(self._active_policy, artifacts) if hasattr(self, "_active_policy") else 0.0
        new_hits, evidences, query_groups = self._run_high_recall_retrieval(
            task,
            signal_state,
            query_state,
            additional_queries=additional_queries,
        )
        elapsed = time.perf_counter() - start
        if additional_queries:
            task.search_hits = self._merge_search_hits(task.search_hits, new_hits)
            task.evidences = self._group_hits(task.search_hits)
            task.retrieval_round += 1
        else:
            task.search_hits = list(new_hits)
            task.evidences = list(evidences)
            task.retrieval_round = max(task.retrieval_round, 1)
        self._refresh_repo_context_artifacts(task, store, method_name)
        trace = StageRoutingTrace(
            stage_name=stage_name,
            remaining_slo_before_s=remaining_before,
            remaining_slo_after_s=max(0.0, remaining_before - elapsed),
            actual_stage_wall_time_s=elapsed,
            notes=[
                f"Retrieved {len(task.search_hits)} hits across {len(task.evidences)} grouped files.",
                f"Files: {', '.join(evidence.file_path for evidence in task.evidences) or 'N/A'}",
            ],
        )
        artifacts.total_wall_time_s += elapsed
        artifacts.stage_traces.append(trace)
        return self._retrieval_history_payload(task, query_groups, stage_name)

    def _stage_map(self) -> Dict[str, RoutedStructuredStage[PreparedBugTriageTask]]:
        return {stage.stage_name: stage for stage in self.adapter.routed_stages()}

    @staticmethod
    def _seed_method_artifacts(
        artifacts: StateTransferMethodArtifacts,
        signal_state: BugSignalExtractionState,
        query_state: BugQueryExpansionState,
        evidence_state: BugEvidenceState,
    ) -> None:
        artifacts.signal_state = signal_state.model_dump()
        artifacts.query_state = query_state.model_dump()
        artifacts.evidence_state = evidence_state.model_dump()

    def _bootstrap_method_store(
        self,
        task: PreparedBugTriageTask,
        method_name: str,
        signal_state: BugSignalExtractionState,
        query_state: BugQueryExpansionState,
        evidence_state: BugEvidenceState,
    ) -> StructuredStateStore:
        store = self.adapter.build_structured_store(task, method_name)
        store.add(self.adapter._signal_artifact(signal_state, method_name))
        store.add(self.adapter._query_artifact(query_state, method_name))
        for evidence in task.evidences:
            store.add(self.adapter._structured_evidence_artifact(evidence, method_name))
        store.add(self.adapter._evidence_state_artifact(evidence_state, method_name))
        return store

    def _run_shared_prestage(
        self,
        task: PreparedBugTriageTask,
        policy: SLOPolicyConfig,
    ) -> Tuple[PreparedBugTriageTask, BugSignalExtractionState, BugQueryExpansionState, BugEvidenceState, str]:
        shared_artifacts = StateTransferMethodArtifacts(method_name="shared_prestage")
        shared_store = StructuredStateStore("shared_prestage")
        history_text = self.adapter.build_history_seed(task)

        initial_stage = self.adapter.initial_stage()
        signal_state = self.runtime.run_initial_stage(task, policy, shared_artifacts, shared_store, initial_stage)
        history_text = self.runtime.append_history(history_text, initial_stage.stage_name, signal_state.model_dump())

        stage_map = self._stage_map()
        contracts = self.adapter.fixed_dependency_contracts()

        query_state = self.runtime.run_fixeddeps_stage(
            task,
            policy,
            shared_artifacts,
            shared_store,
            contracts["query_expansion"],
            stage_map["query_expansion"],
        )
        history_text = self.runtime.append_history(history_text, "query_expansion", query_state.model_dump())

        retrieval_payload = self._run_retrieval_stage(
            task,
            shared_artifacts,
            shared_store,
            "shared_prestage",
            "high_recall_retrieval",
        )
        history_text = self.runtime.append_history(history_text, "high_recall_retrieval", retrieval_payload)

        evidence_state = self.runtime.run_fixeddeps_stage(
            task,
            policy,
            shared_artifacts,
            shared_store,
            contracts["evidence_screen"],
            stage_map["evidence_screen"],
        )
        history_text = self.runtime.append_history(history_text, "evidence_screen", evidence_state.model_dump())

        reretrieval_rounds = 0
        while (
            evidence_state.reretrieval_needed
            and evidence_state.additional_queries
            and reretrieval_rounds < self.max_reretrieval_rounds
        ):
            reretrieval_rounds += 1
            retrieval_payload = self._run_retrieval_stage(
                task,
                shared_artifacts,
                shared_store,
                "shared_prestage",
                "re_retrieval",
                additional_queries=evidence_state.additional_queries,
            )
            history_text = self.runtime.append_history(history_text, "re_retrieval", retrieval_payload)
            self._replace_artifacts(shared_store, "evidence_state", [])
            evidence_state = self.runtime.run_fixeddeps_stage(
                task,
                policy,
                shared_artifacts,
                shared_store,
                contracts["evidence_screen"],
                stage_map["evidence_screen"],
            )
            history_text = self.runtime.append_history(history_text, "evidence_screen", evidence_state.model_dump())

        return task, signal_state, query_state, evidence_state, history_text

    def _execute_structured_fixed_deps(
        self,
        task: PreparedBugTriageTask,
        policy: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        signal_state: BugSignalExtractionState,
        query_state: BugQueryExpansionState,
        evidence_state: BugEvidenceState,
    ) -> StructuredStateStore:
        self._seed_method_artifacts(artifacts, signal_state, query_state, evidence_state)
        store = self._bootstrap_method_store(
            task,
            artifacts.method_name,
            signal_state,
            query_state,
            evidence_state,
        )
        stage_map = self._stage_map()
        contracts = self.adapter.fixed_dependency_contracts()
        for stage_name in ("hypothesis_shortlist", "triage_decision", "fix_plan"):
            self.runtime.run_fixeddeps_stage(task, policy, artifacts, store, contracts[stage_name], stage_map[stage_name])
        if self.adapter.include_review:
            self.runtime.run_fixeddeps_stage(task, policy, artifacts, store, contracts["review"], stage_map["review"])
        return store

    def _execute_full_history_carry(
        self,
        task: PreparedBugTriageTask,
        policy: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        signal_state: BugSignalExtractionState,
        query_state: BugQueryExpansionState,
        evidence_state: BugEvidenceState,
        history_text: str,
    ) -> StructuredStateStore:
        self._seed_method_artifacts(artifacts, signal_state, query_state, evidence_state)
        store = self._bootstrap_method_store(
            task,
            artifacts.method_name,
            signal_state,
            query_state,
            evidence_state,
        )
        stage_map = self._stage_map()
        for stage_name in ("hypothesis_shortlist", "triage_decision", "fix_plan"):
            stage = stage_map[stage_name]
            parsed = self.runtime.run_history_stage(task, policy, artifacts, stage, history_text)
            store.add(stage.artifact_builder(parsed, artifacts.method_name))
            history_text = self.runtime.append_history(history_text, stage.stage_name, parsed.model_dump())
        if self.adapter.include_review:
            stage = stage_map["review"]
            parsed = self.runtime.run_history_stage(task, policy, artifacts, stage, history_text)
            store.add(stage.artifact_builder(parsed, artifacts.method_name))
            history_text = self.runtime.append_history(history_text, stage.stage_name, parsed.model_dump())
        return store

    def _execute_compressed_history_carry(
        self,
        task: PreparedBugTriageTask,
        policy: SLOPolicyConfig,
        artifacts: StateTransferMethodArtifacts,
        signal_state: BugSignalExtractionState,
        query_state: BugQueryExpansionState,
        evidence_state: BugEvidenceState,
        history_text: str,
    ) -> StructuredStateStore:
        self._seed_method_artifacts(artifacts, signal_state, query_state, evidence_state)
        store = self._bootstrap_method_store(
            task,
            artifacts.method_name,
            signal_state,
            query_state,
            evidence_state,
        )
        compression_config = self.adapter.history_compression_config()
        stage_map = self._stage_map()
        for stage_name in ("hypothesis_shortlist", "triage_decision", "fix_plan"):
            stage = stage_map[stage_name]
            compressed_history = self.runtime.compress_history(
                task,
                policy,
                artifacts,
                stage.stage_name,
                history_text,
                self.adapter.build_history_compression_prompt,
                compression_config,
            )
            parsed = self.runtime.run_history_stage(task, policy, artifacts, stage, compressed_history)
            store.add(stage.artifact_builder(parsed, artifacts.method_name))
            history_text = self.runtime.append_history(compressed_history, stage.stage_name, parsed.model_dump())
        if self.adapter.include_review:
            stage = stage_map["review"]
            compressed_history = self.runtime.compress_history(
                task,
                policy,
                artifacts,
                stage.stage_name,
                history_text,
                self.adapter.build_history_compression_prompt,
                compression_config,
            )
            parsed = self.runtime.run_history_stage(task, policy, artifacts, stage, compressed_history)
            store.add(stage.artifact_builder(parsed, artifacts.method_name))
            history_text = self.runtime.append_history(compressed_history, stage.stage_name, parsed.model_dump())
        return store

    def _save_issue_inputs(self, issue: BugTriageIssue, issue_dir: Path) -> None:
        self._write_json(issue_dir / "issue.json", issue.model_dump())

    def _save_method_context_snapshot(
        self,
        issue_dir: Path,
        method_name: str,
        task: PreparedBugTriageTask,
        store: StructuredStateStore,
    ) -> None:
        self._write_json(issue_dir / f"{method_name}_search_hits.json", [asdict(hit) for hit in task.search_hits])
        self._write_json(
            issue_dir / f"{method_name}_repo_context.json",
            [artifact.payload for artifact in store.list("repo_context")],
        )

    def _set_completion_proxy(self, artifacts: StateTransferMethodArtifacts) -> None:
        if self.adapter.include_review:
            artifacts.completion_proxy = min(
                float(bool(artifacts.signal_state)) * 0.12
                + float(bool(artifacts.query_state)) * 0.12
                + float(bool(artifacts.evidence_state)) * 0.20
                + float(bool(artifacts.hypothesis_state)) * 0.17
                + float(bool(artifacts.decision_state)) * 0.17
                + float(bool(artifacts.plan_state)) * 0.12
                + float(bool(artifacts.review_state)) * 0.10,
                1.0,
            )
            return
        artifacts.completion_proxy = min(
            float(bool(artifacts.signal_state)) * 0.14
            + float(bool(artifacts.query_state)) * 0.14
            + float(bool(artifacts.evidence_state)) * 0.22
            + float(bool(artifacts.hypothesis_state)) * 0.20
            + float(bool(artifacts.decision_state)) * 0.20
            + float(bool(artifacts.plan_state)) * 0.10,
            1.0,
        )

    @staticmethod
    def _normalize_eval_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).lower()

    @staticmethod
    def _label_coverage(targets: Sequence[str], predictions: Sequence[str]) -> Optional[float]:
        gold = [str(value).strip() for value in targets if str(value).strip()]
        if not gold:
            return None
        predicted = {str(value).strip() for value in predictions if str(value).strip()}
        if not predicted:
            return 0.0
        hits = sum(1 for label in gold if label in predicted)
        return hits / len(gold)

    @staticmethod
    def _candidate_file_map(artifacts: StateTransferMethodArtifacts) -> Dict[str, str]:
        evidence_state = artifacts.evidence_state or {}
        mapping: Dict[str, str] = {}
        for item in evidence_state.get("candidate_files") or []:
            evidence_id = str(item.get("evidence_id") or "").strip()
            file_path = str(item.get("file_path") or "").strip()
            if evidence_id and file_path:
                mapping[evidence_id] = file_path
        return mapping

    @staticmethod
    def _canonicalize_candidate_file(raw_value: str, candidate_map: Dict[str, str]) -> str:
        value = str(raw_value or "").strip()
        if not value:
            return ""
        if value in candidate_map:
            return candidate_map[value]
        candidate_paths = list(candidate_map.values())
        if value in candidate_paths:
            return value

        normalized = value.replace("\\", "/").strip()
        suffix_matches = [path for path in candidate_paths if path.endswith(normalized)]
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        basename = Path(normalized).name
        if basename:
            basename_matches = [path for path in candidate_paths if Path(path).name == basename]
            if len(basename_matches) == 1:
                return basename_matches[0]
        return normalized

    def _resolve_final_primary_choice(
        self,
        artifacts: StateTransferMethodArtifacts,
    ) -> Tuple[str, str]:
        decision = artifacts.decision_state or {}
        review = artifacts.review_state or {}
        candidate_map = self._candidate_file_map(artifacts)

        evidence_id = str(
            review.get("revised_primary_evidence_id")
            or decision.get("primary_evidence_id")
            or ""
        ).strip()
        raw_primary = str(
            review.get("revised_primary_file")
            or decision.get("primary_file")
            or ""
        ).strip()

        if evidence_id and evidence_id in candidate_map:
            return candidate_map[evidence_id], evidence_id

        canonical = self._canonicalize_candidate_file(raw_primary, candidate_map)
        if raw_primary and raw_primary in candidate_map:
            evidence_id = raw_primary
        elif not evidence_id:
            for key, file_path in candidate_map.items():
                if file_path == canonical:
                    evidence_id = key
                    break
        return canonical, evidence_id

    def _evaluate_method(
        self,
        issue: BugTriageIssue,
        artifacts: StateTransferMethodArtifacts,
    ) -> BugTriageQualityMetrics:
        evidence = artifacts.evidence_state or {}
        final_primary, final_evidence_id = self._resolve_final_primary_choice(artifacts)
        final_category = str((artifacts.decision_state or {}).get("root_cause_category") or "").strip()
        candidate_map = self._candidate_file_map(artifacts)
        candidate_files = list(candidate_map.values())
        metrics = BugTriageQualityMetrics(
            reference_available=bool(
                str(issue.gold_primary_file or "").strip()
                or str(issue.gold_root_cause_category or "").strip()
                or issue.gold_evidence_labels
            ),
            final_primary_file=final_primary,
            final_primary_evidence_id=final_evidence_id,
            final_root_cause_category=final_category,
        )
        if not metrics.reference_available:
            return metrics

        gold_primary = self._canonicalize_candidate_file(issue.gold_primary_file, candidate_map)
        gold_primary_key = self._normalize_eval_text(gold_primary)
        final_primary_key = self._normalize_eval_text(final_primary)
        gold_category_key = self._normalize_eval_text(issue.gold_root_cause_category)
        final_category_key = self._normalize_eval_text(final_category)

        metrics.gold_primary_file_in_candidates = gold_primary in candidate_files
        metrics.primary_file_match = bool(final_primary_key and final_primary_key == gold_primary_key)
        metrics.primary_file_match_when_in_candidates = (
            metrics.primary_file_match if metrics.gold_primary_file_in_candidates else None
        )
        metrics.root_cause_category_match = bool(final_category_key and final_category_key == gold_category_key)
        metrics.exact_match = bool(metrics.primary_file_match and metrics.root_cause_category_match)
        metrics.evidence_label_coverage = self._label_coverage(
            [str(value) for value in issue.gold_evidence_labels],
            [str(value) for value in evidence.get("applied_evidence_labels") or []],
        )
        return metrics

    def run(
        self,
        repo_root: str,
        issues_file: str,
        artifact_output_dir: str,
        policy_config: Optional[SLOPolicyConfig] = None,
    ) -> List[BugTriageIssueResult]:
        policy = policy_config or SLOPolicyConfig(total_slo_seconds=8.0)
        self._active_policy = policy
        issues = self.load_issues(issues_file)
        repo = Path(repo_root).expanduser().resolve()
        artifact_base = Path(artifact_output_dir).expanduser().resolve()

        results: List[BugTriageIssueResult] = []
        for issue in issues:
            issue_dir = self._issue_dir(artifact_base, issue.issue_id)
            self._save_issue_inputs(issue, issue_dir)

            shared_task = self._prepare_task(repo, issue)
            structured_fixed_deps = StateTransferMethodArtifacts(method_name="structured_fixed_deps")
            full_history_carry = StateTransferMethodArtifacts(method_name="full_history_carry")
            compressed_history_carry = StateTransferMethodArtifacts(method_name="compressed_history_carry")

            (
                shared_task,
                shared_signal_state,
                shared_query_state,
                shared_evidence_state,
                shared_history_text,
            ) = self._run_shared_prestage(shared_task, policy)
            self._write_text(issue_dir / "full_history_seed.txt", shared_history_text)
            self._save_method_context_snapshot(
                issue_dir,
                "shared_prestage",
                shared_task,
                self._bootstrap_method_store(
                    shared_task,
                    "shared_prestage",
                    shared_signal_state,
                    shared_query_state,
                    shared_evidence_state,
                ),
            )

            structured_task = self._prepare_task(repo, issue)
            structured_task.search_hits = list(shared_task.search_hits)
            structured_task.evidences = list(shared_task.evidences)
            structured_task.retrieval_round = shared_task.retrieval_round
            structured_fixed_store = self._execute_structured_fixed_deps(
                structured_task,
                policy,
                structured_fixed_deps,
                shared_signal_state,
                shared_query_state,
                shared_evidence_state,
            )
            finalize_method(structured_fixed_deps, policy, structured_fixed_store)
            self._set_completion_proxy(structured_fixed_deps)
            self._save_method_context_snapshot(
                issue_dir,
                "structured_fixed_deps",
                structured_task,
                structured_fixed_store,
            )

            full_task = self._prepare_task(repo, issue)
            full_task.search_hits = list(shared_task.search_hits)
            full_task.evidences = list(shared_task.evidences)
            full_task.retrieval_round = shared_task.retrieval_round
            full_history_store = self._execute_full_history_carry(
                full_task,
                policy,
                full_history_carry,
                shared_signal_state,
                shared_query_state,
                shared_evidence_state,
                shared_history_text,
            )
            finalize_method(full_history_carry, policy, full_history_store)
            self._set_completion_proxy(full_history_carry)
            self._save_method_context_snapshot(
                issue_dir,
                "full_history_carry",
                full_task,
                full_history_store,
            )

            compressed_task = self._prepare_task(repo, issue)
            compressed_task.search_hits = list(shared_task.search_hits)
            compressed_task.evidences = list(shared_task.evidences)
            compressed_task.retrieval_round = shared_task.retrieval_round
            compressed_history_store = self._execute_compressed_history_carry(
                compressed_task,
                policy,
                compressed_history_carry,
                shared_signal_state,
                shared_query_state,
                shared_evidence_state,
                shared_history_text,
            )
            finalize_method(compressed_history_carry, policy, compressed_history_store)
            self._set_completion_proxy(compressed_history_carry)
            self._save_method_context_snapshot(
                issue_dir,
                "compressed_history_carry",
                compressed_task,
                compressed_history_store,
            )

            result = BugTriageIssueResult(
                issue_id=issue.issue_id,
                title=issue.title,
                repo_root=str(repo),
                retrieved_hits_count=len(shared_task.search_hits),
                retrieved_files=[evidence.file_path for evidence in shared_task.evidences],
                policy_config=policy,
                gold_primary_file=issue.gold_primary_file,
                gold_root_cause_category=issue.gold_root_cause_category,
                gold_evidence_labels=[str(value) for value in issue.gold_evidence_labels],
                structured_fixed_deps=structured_fixed_deps,
                full_history_carry=full_history_carry,
                compressed_history_carry=compressed_history_carry,
                structured_fixed_deps_quality=self._evaluate_method(issue, structured_fixed_deps),
                full_history_carry_quality=self._evaluate_method(issue, full_history_carry),
                compressed_history_carry_quality=self._evaluate_method(issue, compressed_history_carry),
                structured_fixed_deps_vs_full_history_metrics_delta=routing_metrics_delta(
                    structured_fixed_deps,
                    full_history_carry,
                ),
                compressed_history_carry_vs_full_history_metrics_delta=routing_metrics_delta(
                    compressed_history_carry,
                    full_history_carry,
                ),
            )
            self._write_json(issue_dir / "bug_triage_result.json", asdict(result))
            results.append(result)

        self._active_policy = None
        return results
