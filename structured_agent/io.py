"""I/O helpers for the support and bug-triage experiments."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List

from .eval import build_bug_triage_summary, build_support_ticket_summary
from .schemas import BugTriageIssueResult, SupportTicketResult


def save_json(path: str, payload: object) -> None:
    """Save a JSON-serializable payload."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _selected_read_lines(stage_trace) -> List[str]:
    lines: List[str] = []
    selected_reads = [record for record in stage_trace.read_records if record.selected]
    if not selected_reads:
        return ["- No selected reads"]
    for record in selected_reads:
        lines.append(
            "- "
            f"{record.artifact_label} "
            f"(`{record.artifact_type}:{record.view_name}`, "
            f"{record.estimated_tokens} tok est, "
            f"{record.estimated_latency_s:.3f}s est)"
        )
    return lines


def _method_transfer_totals(method) -> dict:
    return {
        "transferred_tokens": sum(trace.selected_context_tokens for trace in method.stage_traces),
        "required_tokens": sum(trace.required_context_tokens for trace in method.stage_traces),
        "optional_tokens": sum(trace.optional_context_tokens for trace in method.stage_traces),
        "estimated_read_time_s": sum(trace.estimated_context_read_time_s for trace in method.stage_traces),
    }


def _add_method_section(lines: List[str], method_label: str, method, quality=None) -> None:
    if method is None:
        return
    totals = _method_transfer_totals(method)
    lines.extend(
        [
            f"### {method_label}",
            "",
            f"- Total wall time (s): `{method.total_wall_time_s:.6f}`",
            f"- Prompt tokens: `{method.stats.prompt_tokens}`",
            f"- Output tokens: `{method.stats.output_tokens}`",
            f"- Model latency (s): `{method.stats.latency_s:.6f}`",
            f"- Transferred context tokens: `{totals['transferred_tokens']}`",
            f"- Required context tokens: `{totals['required_tokens']}`",
            f"- Optional context tokens: `{totals['optional_tokens']}`",
            f"- Estimated context read time (s): `{totals['estimated_read_time_s']:.6f}`",
            "",
        ]
    )
    if quality is not None and quality.reference_available:
        if hasattr(quality, "final_issue_type"):
            lines.extend(
                [
                    "Quality:",
                    "",
                    f"- Final issue type: `{quality.final_issue_type or 'N/A'}`",
                    f"- Final eligibility decision: `{quality.final_eligibility_decision or 'N/A'}`",
                    f"- Final resolution category: `{quality.final_resolution_category or 'N/A'}`",
                    f"- Final escalation needed: `{quality.final_escalation_needed}`",
                    f"- Exact match: `{quality.exact_match}`",
                    f"- Issue-type match: `{quality.issue_type_match}`",
                    f"- Eligibility match: `{quality.eligibility_match}`",
                    f"- Resolution match: `{quality.resolution_match}`",
                    f"- Escalation match: `{quality.escalation_match}`",
                    "- Policy-label coverage: "
                    f"`{0.0 if quality.policy_label_coverage is None else quality.policy_label_coverage:.3f}`",
                    "- Response commitment coverage: "
                    f"`{0.0 if quality.response_commitment_coverage is None else quality.response_commitment_coverage:.3f}`",
                    "",
                ]
            )
        elif hasattr(quality, "final_primary_file"):
            lines.extend(
                [
                    "Quality:",
                    "",
                    f"- Final primary evidence id: `{quality.final_primary_evidence_id or 'N/A'}`",
                    f"- Final primary file: `{quality.final_primary_file or 'N/A'}`",
                    f"- Final root-cause category: `{quality.final_root_cause_category or 'N/A'}`",
                    f"- Gold primary file in candidate files: `{quality.gold_primary_file_in_candidates}`",
                    f"- Exact match: `{quality.exact_match}`",
                    f"- Primary-file match: `{quality.primary_file_match}`",
                    f"- Primary-file match when in candidate files: `{quality.primary_file_match_when_in_candidates}`",
                    f"- Root-cause-category match: `{quality.root_cause_category_match}`",
                    "- Evidence-label coverage: "
                    f"`{0.0 if quality.evidence_label_coverage is None else quality.evidence_label_coverage:.3f}`",
                    "",
                ]
            )
    lines.extend(
        [
            "Secondary system metrics:",
            "",
            f"- Met SLO: `{method.met_slo}`",
            f"- Completion proxy: `{method.completion_proxy:.3f}`",
            "",
        ]
    )
    for stage_trace in method.stage_traces:
        lines.extend(
            [
                f"#### Stage `{stage_trace.stage_name}`",
                "",
                f"- Skipped: `{stage_trace.skipped}`",
                f"- Selected context tokens: `{stage_trace.selected_context_tokens}`",
                f"- Required context tokens: `{stage_trace.required_context_tokens}`",
                f"- Optional context tokens: `{stage_trace.optional_context_tokens}`",
                f"- Estimated read time (s): `{stage_trace.estimated_context_read_time_s:.6f}`",
                f"- Actual wall time (s): `{stage_trace.actual_stage_wall_time_s:.6f}`",
                "",
                "Selected reads:",
                "",
            ]
        )
        lines.extend(_selected_read_lines(stage_trace))
        if stage_trace.notes:
            lines.extend(["", "Notes:", ""])
            lines.extend(f"- {note}" for note in stage_trace.notes)
        lines.append("")


def save_support_ticket_json(path: str, tickets_file: str, rows: List[SupportTicketResult]) -> None:
    payload = {
        "tickets_file": tickets_file,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": build_support_ticket_summary(rows),
        "tickets": [asdict(row) for row in rows],
    }
    save_json(path, payload)


def save_support_ticket_report(path: str, tickets_file: str, rows: List[SupportTicketResult]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    summary = build_support_ticket_summary(rows)
    lines = [
        "# Support Ticket State Transfer Report",
        "",
        f"- Tickets file: `{tickets_file}`",
        f"- Generated at: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Tickets evaluated: `{summary['tickets']}`",
        f"- Source-pack chars observed: `{summary['source_pack_chars']}`",
        f"- Source-pack token estimate: `{summary['source_pack_token_estimate']}`",
        f"- Source-pack token estimate min: `{summary['source_pack_token_estimate_min']}`",
        f"- Source-pack token estimate median: `{summary['source_pack_token_estimate_median']}`",
        f"- Source-pack token estimate max: `{summary['source_pack_token_estimate_max']}`",
        f"- Labeled tickets: `{summary['labeled_tickets']}`",
        "",
        "## Main Experiment",
        "",
        f"- Structured-FixedDeps prompt tokens: `{summary['structured_fixed_deps_prompt_tokens']}`",
        f"- Full-History Carry prompt tokens: `{summary['full_history_carry_prompt_tokens']}`",
        f"- Compressed-History Carry prompt tokens: `{summary['compressed_history_carry_prompt_tokens']}`",
        f"- Structured-FixedDeps transferred context tokens: `{summary['structured_fixed_deps_transferred_context_tokens']}`",
        f"- Full-History Carry transferred context tokens: `{summary['full_history_carry_transferred_context_tokens']}`",
        f"- Compressed-History Carry transferred context tokens: `{summary['compressed_history_carry_transferred_context_tokens']}`",
        f"- Structured-FixedDeps wall time (s): `{summary['structured_fixed_deps_wall_time_s']:.6f}`",
        f"- Full-History Carry wall time (s): `{summary['full_history_carry_wall_time_s']:.6f}`",
        f"- Compressed-History Carry wall time (s): `{summary['compressed_history_carry_wall_time_s']:.6f}`",
        "",
        "## Downstream Transfer Efficiency",
        "",
        f"- Structured-FixedDeps downstream prompt tokens: `{summary['structured_fixed_deps_downstream_prompt_tokens']:.0f}`",
        f"- Full-History Carry downstream prompt tokens: `{summary['full_history_carry_downstream_prompt_tokens']:.0f}`",
        f"- Compressed-History Carry downstream prompt tokens: `{summary['compressed_history_carry_downstream_prompt_tokens']:.0f}`",
        f"- Structured-FixedDeps downstream transferred context tokens: `{summary['structured_fixed_deps_downstream_transferred_context_tokens']:.0f}`",
        f"- Full-History Carry downstream transferred context tokens: `{summary['full_history_carry_downstream_transferred_context_tokens']:.0f}`",
        f"- Compressed-History Carry downstream transferred context tokens: `{summary['compressed_history_carry_downstream_transferred_context_tokens']:.0f}`",
        f"- Structured-FixedDeps downstream context reduction vs Full-History: `{0.0 if summary['structured_fixed_deps_vs_full_history_downstream_context_reduction_ratio'] is None else summary['structured_fixed_deps_vs_full_history_downstream_context_reduction_ratio']:.3f}`",
        f"- Compressed-History Carry downstream context reduction vs Full-History: `{0.0 if summary['compressed_history_carry_vs_full_history_downstream_context_reduction_ratio'] is None else summary['compressed_history_carry_vs_full_history_downstream_context_reduction_ratio']:.3f}`",
        f"- Compressed-History compression stage count: `{summary['compressed_history_carry_compression_stage_count']}`",
        f"- Compressed-History compression applied count: `{summary['compressed_history_carry_compression_applied_count']}`",
        f"- Compressed-History compression applied rate: `{summary['compressed_history_carry_compression_applied_rate']:.3f}`",
        "",
        "## Quality Metrics",
        "",
        f"- Structured-FixedDeps exact match rate: `{summary['structured_fixed_deps_exact_match_rate']:.3f}`",
        f"- Full-History Carry exact match rate: `{summary['full_history_carry_exact_match_rate']:.3f}`",
        f"- Compressed-History Carry exact match rate: `{summary['compressed_history_carry_exact_match_rate']:.3f}`",
        f"- Structured-FixedDeps issue-type match rate: `{summary['structured_fixed_deps_issue_type_match_rate']:.3f}`",
        f"- Full-History Carry issue-type match rate: `{summary['full_history_carry_issue_type_match_rate']:.3f}`",
        f"- Compressed-History Carry issue-type match rate: `{summary['compressed_history_carry_issue_type_match_rate']:.3f}`",
        f"- Structured-FixedDeps eligibility match rate: `{summary['structured_fixed_deps_eligibility_match_rate']:.3f}`",
        f"- Full-History Carry eligibility match rate: `{summary['full_history_carry_eligibility_match_rate']:.3f}`",
        f"- Compressed-History Carry eligibility match rate: `{summary['compressed_history_carry_eligibility_match_rate']:.3f}`",
        f"- Structured-FixedDeps resolution match rate: `{summary['structured_fixed_deps_resolution_match_rate']:.3f}`",
        f"- Full-History Carry resolution match rate: `{summary['full_history_carry_resolution_match_rate']:.3f}`",
        f"- Compressed-History Carry resolution match rate: `{summary['compressed_history_carry_resolution_match_rate']:.3f}`",
        f"- Structured-FixedDeps escalation match rate: `{summary['structured_fixed_deps_escalation_match_rate']:.3f}`",
        f"- Full-History Carry escalation match rate: `{summary['full_history_carry_escalation_match_rate']:.3f}`",
        f"- Compressed-History Carry escalation match rate: `{summary['compressed_history_carry_escalation_match_rate']:.3f}`",
        f"- Structured-FixedDeps policy-label coverage: `{summary['structured_fixed_deps_policy_label_coverage']:.3f}`",
        f"- Full-History Carry policy-label coverage: `{summary['full_history_carry_policy_label_coverage']:.3f}`",
        f"- Compressed-History Carry policy-label coverage: `{summary['compressed_history_carry_policy_label_coverage']:.3f}`",
        f"- Structured-FixedDeps response commitment coverage: `{summary['structured_fixed_deps_response_commitment_coverage']:.3f}`",
        f"- Full-History Carry response commitment coverage: `{summary['full_history_carry_response_commitment_coverage']:.3f}`",
        f"- Compressed-History Carry response commitment coverage: `{summary['compressed_history_carry_response_commitment_coverage']:.3f}`",
        "",
    ]

    for row in rows:
        lines.extend(
            [
                f"## {row.ticket_id}",
                "",
                f"- Customer message: `{row.customer_message}`",
                f"- Source-pack chars: `{row.source_pack_chars}`",
                f"- Source-pack token estimate: `{row.source_pack_token_estimate}`",
                "",
            ]
        )
        _add_method_section(lines, "Structured-FixedDeps", row.structured_fixed_deps, row.structured_fixed_deps_quality)
        _add_method_section(lines, "Full-History Carry", row.full_history_carry, row.full_history_carry_quality)
        _add_method_section(lines, "Compressed-History Carry", row.compressed_history_carry, row.compressed_history_carry_quality)

    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")


def save_bug_triage_json(path: str, repo_root: str, issues_file: str, rows: List[BugTriageIssueResult]) -> None:
    payload = {
        "repo_root": repo_root,
        "issues_file": issues_file,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": build_bug_triage_summary(rows),
        "issues": [asdict(row) for row in rows],
    }
    save_json(path, payload)


def save_bug_triage_report(path: str, repo_root: str, issues_file: str, rows: List[BugTriageIssueResult]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    summary = build_bug_triage_summary(rows)
    lines = [
        "# Bug Triage State Transfer Report",
        "",
        f"- Repo root: `{repo_root}`",
        f"- Issues file: `{issues_file}`",
        f"- Generated at: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- Issues evaluated: `{summary['issues']}`",
        f"- Retrieved hits: `{summary['retrieved_hits']}`",
        f"- Labeled primary files: `{summary['labeled_primary_files']}`",
        f"- Labeled root-cause categories: `{summary['labeled_root_cause_categories']}`",
        f"- Labeled evidence-label sets: `{summary['labeled_evidence_labels']}`",
        "",
        "## Main Experiment",
        "",
        f"- Structured-FixedDeps prompt tokens: `{summary['structured_fixed_deps_prompt_tokens']}`",
        f"- Full-History Carry prompt tokens: `{summary['full_history_carry_prompt_tokens']}`",
        f"- Compressed-History Carry prompt tokens: `{summary['compressed_history_carry_prompt_tokens']}`",
        f"- Structured-FixedDeps transferred context tokens: `{summary['structured_fixed_deps_transferred_context_tokens']}`",
        f"- Full-History Carry transferred context tokens: `{summary['full_history_carry_transferred_context_tokens']}`",
        f"- Compressed-History Carry transferred context tokens: `{summary['compressed_history_carry_transferred_context_tokens']}`",
        f"- Structured-FixedDeps wall time (s): `{summary['structured_fixed_deps_wall_time_s']:.6f}`",
        f"- Full-History Carry wall time (s): `{summary['full_history_carry_wall_time_s']:.6f}`",
        f"- Compressed-History Carry wall time (s): `{summary['compressed_history_carry_wall_time_s']:.6f}`",
        "",
        "## Downstream Transfer Efficiency",
        "",
        f"- Structured-FixedDeps downstream prompt tokens: `{summary['structured_fixed_deps_downstream_prompt_tokens']:.0f}`",
        f"- Full-History Carry downstream prompt tokens: `{summary['full_history_carry_downstream_prompt_tokens']:.0f}`",
        f"- Compressed-History Carry downstream prompt tokens: `{summary['compressed_history_carry_downstream_prompt_tokens']:.0f}`",
        f"- Structured-FixedDeps downstream transferred context tokens: `{summary['structured_fixed_deps_downstream_transferred_context_tokens']:.0f}`",
        f"- Full-History Carry downstream transferred context tokens: `{summary['full_history_carry_downstream_transferred_context_tokens']:.0f}`",
        f"- Compressed-History Carry downstream transferred context tokens: `{summary['compressed_history_carry_downstream_transferred_context_tokens']:.0f}`",
        f"- Structured-FixedDeps downstream context reduction vs Full-History: `{0.0 if summary['structured_fixed_deps_vs_full_history_downstream_context_reduction_ratio'] is None else summary['structured_fixed_deps_vs_full_history_downstream_context_reduction_ratio']:.3f}`",
        f"- Compressed-History Carry downstream context reduction vs Full-History: `{0.0 if summary['compressed_history_carry_vs_full_history_downstream_context_reduction_ratio'] is None else summary['compressed_history_carry_vs_full_history_downstream_context_reduction_ratio']:.3f}`",
        f"- Compressed-History compression stage count: `{summary['compressed_history_carry_compression_stage_count']}`",
        f"- Compressed-History compression applied count: `{summary['compressed_history_carry_compression_applied_count']}`",
        f"- Compressed-History compression applied rate: `{summary['compressed_history_carry_compression_applied_rate']:.3f}`",
        "",
        "## Lightweight Quality Metrics",
        "",
        f"- Structured-FixedDeps gold primary-file raw retrieval recall: `{summary['structured_fixed_deps_gold_primary_file_raw_recall_rate']:.3f}`",
        f"- Full-History Carry gold primary-file raw retrieval recall: `{summary['full_history_carry_gold_primary_file_raw_recall_rate']:.3f}`",
        f"- Compressed-History Carry gold primary-file raw retrieval recall: `{summary['compressed_history_carry_gold_primary_file_raw_recall_rate']:.3f}`",
        f"- Structured-FixedDeps gold primary-file candidate recall: `{summary['structured_fixed_deps_gold_primary_file_candidate_recall_rate']:.3f}`",
        f"- Full-History Carry gold primary-file candidate recall: `{summary['full_history_carry_gold_primary_file_candidate_recall_rate']:.3f}`",
        f"- Compressed-History Carry gold primary-file candidate recall: `{summary['compressed_history_carry_gold_primary_file_candidate_recall_rate']:.3f}`",
        f"- Structured-FixedDeps primary-file match rate: `{summary['structured_fixed_deps_primary_file_match_rate']:.3f}`",
        f"- Full-History Carry primary-file match rate: `{summary['full_history_carry_primary_file_match_rate']:.3f}`",
        f"- Compressed-History Carry primary-file match rate: `{summary['compressed_history_carry_primary_file_match_rate']:.3f}`",
        f"- Structured-FixedDeps primary-file match rate | gold in candidates: `{summary['structured_fixed_deps_primary_file_match_rate_when_in_candidates']:.3f}`",
        f"- Full-History Carry primary-file match rate | gold in candidates: `{summary['full_history_carry_primary_file_match_rate_when_in_candidates']:.3f}`",
        f"- Compressed-History Carry primary-file match rate | gold in candidates: `{summary['compressed_history_carry_primary_file_match_rate_when_in_candidates']:.3f}`",
        f"- Structured-FixedDeps root-cause-category match rate: `{summary['structured_fixed_deps_root_cause_category_match_rate']:.3f}`",
        f"- Full-History Carry root-cause-category match rate: `{summary['full_history_carry_root_cause_category_match_rate']:.3f}`",
        f"- Compressed-History Carry root-cause-category match rate: `{summary['compressed_history_carry_root_cause_category_match_rate']:.3f}`",
        f"- Structured-FixedDeps retrieved-file hit rate: `{summary['structured_fixed_deps_retrieved_file_hit_rate']:.3f}`",
        f"- Full-History Carry retrieved-file hit rate: `{summary['full_history_carry_retrieved_file_hit_rate']:.3f}`",
        f"- Compressed-History Carry retrieved-file hit rate: `{summary['compressed_history_carry_retrieved_file_hit_rate']:.3f}`",
        f"- Structured-FixedDeps evidence-label coverage: `{summary['structured_fixed_deps_evidence_label_coverage']:.3f}`",
        f"- Full-History Carry evidence-label coverage: `{summary['full_history_carry_evidence_label_coverage']:.3f}`",
        f"- Compressed-History Carry evidence-label coverage: `{summary['compressed_history_carry_evidence_label_coverage']:.3f}`",
        "",
    ]
    if summary.get("review_ablation_enabled"):
        lines.extend(
            [
                "## Review Ablation",
                "",
                f"- Structured-FixedDeps review accept rate: `{summary['structured_fixed_deps_review_accept_rate']:.3f}`",
                f"- Full-History Carry review accept rate: `{summary['full_history_carry_review_accept_rate']:.3f}`",
                f"- Compressed-History Carry review accept rate: `{summary['compressed_history_carry_review_accept_rate']:.3f}`",
                f"- Structured-FixedDeps review override rate: `{summary['structured_fixed_deps_review_override_rate']:.3f}`",
                f"- Full-History Carry review override rate: `{summary['full_history_carry_review_override_rate']:.3f}`",
                f"- Compressed-History Carry review override rate: `{summary['compressed_history_carry_review_override_rate']:.3f}`",
                "",
            ]
        )

    for row in rows:
        lines.extend(
            [
                f"## {row.issue_id}",
                "",
                f"- Title: `{row.title}`",
                f"- Shared pre-stage retrieved hits: `{row.retrieved_hits_count}`",
                f"- Shared pre-stage retrieved files: `{', '.join(row.retrieved_files) or 'N/A'}`",
                f"- Gold primary file: `{row.gold_primary_file or 'N/A'}`",
                f"- Gold root-cause category: `{row.gold_root_cause_category or 'N/A'}`",
                f"- Gold evidence labels: `{', '.join(row.gold_evidence_labels) or 'N/A'}`",
                "",
            ]
        )
        _add_method_section(lines, "Structured-FixedDeps", row.structured_fixed_deps, row.structured_fixed_deps_quality)
        _add_method_section(lines, "Full-History Carry", row.full_history_carry, row.full_history_carry_quality)
        _add_method_section(lines, "Compressed-History Carry", row.compressed_history_carry, row.compressed_history_carry_quality)

    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
