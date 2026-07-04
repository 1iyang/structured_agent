"""Evaluation helpers for the support and bug-triage experiments."""

from __future__ import annotations

from typing import Dict, List, Sequence

from .schemas import BugTriageIssueResult, SupportTicketResult

PRIMARY_METHOD_NAMES = (
    "structured_fixed_deps",
    "full_history_carry",
    "compressed_history_carry",
)


def _safe_reduction(candidate: float, reference: float):
    if reference > 0:
        return 1.0 - (candidate / reference)
    return None


def _sum_method_field(rows: Sequence, method_name: str, field_name: str) -> float:
    total = 0.0
    for item in rows:
        method = getattr(item, method_name, None)
        if method is None:
            continue
        value = method
        for part in field_name.split("."):
            value = getattr(value, part)
        total += float(value)
    return total


def _sum_stage_trace_field(
    rows: Sequence,
    method_name: str,
    field_name: str,
    *,
    stage_name_prefix: str | None = None,
) -> float:
    total = 0.0
    for item in rows:
        method = getattr(item, method_name, None)
        if method is None:
            continue
        for trace in method.stage_traces:
            if stage_name_prefix and not trace.stage_name.startswith(stage_name_prefix):
                continue
            total += float(getattr(trace, field_name))
    return total


def _sum_stage_trace_field_for_names(
    rows: Sequence,
    method_name: str,
    field_name: str,
    stage_names: Sequence[str],
) -> float:
    selected = set(stage_names)
    total = 0.0
    for item in rows:
        method = getattr(item, method_name, None)
        if method is None:
            continue
        for trace in method.stage_traces:
            if trace.stage_name not in selected:
                continue
            total += float(getattr(trace, field_name))
    return total


def _count_stage_traces(
    rows: Sequence,
    method_name: str,
    *,
    stage_name_prefix: str | None = None,
    skipped: bool | None = None,
) -> int:
    total = 0
    for item in rows:
        method = getattr(item, method_name, None)
        if method is None:
            continue
        for trace in method.stage_traces:
            if stage_name_prefix and not trace.stage_name.startswith(stage_name_prefix):
                continue
            if skipped is not None and bool(trace.skipped) != skipped:
                continue
            total += 1
    return total


def _sum_log_field_for_suffixes(
    rows: Sequence,
    method_name: str,
    field_name: str,
    stage_name_suffixes: Sequence[str],
) -> float:
    suffixes = tuple(stage_name_suffixes)
    total = 0.0
    for item in rows:
        method = getattr(item, method_name, None)
        if method is None:
            continue
        for log in method.logs:
            stage_name = str(log.get("stage_name", ""))
            if not stage_name.endswith(suffixes):
                continue
            total += float(log.get(field_name, 0.0))
    return total


def _average_quality_metric(rows: Sequence, quality_field_name: str, metric_name: str) -> float:
    values = []
    for item in rows:
        quality = getattr(item, quality_field_name)
        if quality is None:
            continue
        value = getattr(quality, metric_name, None)
        if value is not None:
            values.append(float(value))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _count_reference_available(rows: Sequence, quality_field_name: str) -> int:
    return sum(
        1
        for item in rows
        if getattr(item, quality_field_name) is not None
        and getattr(item, quality_field_name).reference_available
    )


def _average_bug_triage_review_accept(rows: Sequence, method_name: str) -> float:
    values = []
    for item in rows:
        method = getattr(item, method_name, None)
        if method is None:
            continue
        review_state = method.review_state or {}
        decision_ok = review_state.get("decision_ok")
        if isinstance(decision_ok, bool):
            values.append(float(decision_ok))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _average_bug_triage_review_override(rows: Sequence, method_name: str) -> float:
    values = []
    for item in rows:
        method = getattr(item, method_name, None)
        if method is None:
            continue
        review_state = method.review_state or {}
        revised_primary = str(review_state.get("revised_primary_file") or "").strip()
        revised_fix = str(review_state.get("revised_fix") or "").strip()
        values.append(float(bool(revised_primary or revised_fix)))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _bug_triage_final_primary_file(item, method_name: str) -> str:
    quality = getattr(item, f"{method_name}_quality", None)
    if quality is not None:
        final_primary = str(getattr(quality, "final_primary_file", "") or "").strip()
        if final_primary:
            return final_primary
    method = getattr(item, method_name, None)
    if method is None:
        return ""
    decision_state = method.decision_state or {}
    review_state = method.review_state or {}
    revised_primary = str(review_state.get("revised_primary_file") or "").strip()
    if revised_primary:
        return revised_primary
    return str(decision_state.get("primary_file") or "").strip()


def _bug_triage_repo_context_files(item, method_name: str) -> List[str]:
    method = getattr(item, method_name, None)
    if method is None:
        return list(getattr(item, "retrieved_files", []))
    files = [
        str(artifact.label)
        for artifact in getattr(method, "state_artifacts", [])
        if getattr(artifact, "artifact_type", "") == "repo_context"
    ]
    return files or list(getattr(item, "retrieved_files", []))


def _average_bug_triage_retrieved_file_hit(rows: Sequence, method_name: str) -> float:
    values = []
    for item in rows:
        primary_file = _bug_triage_final_primary_file(item, method_name)
        if not primary_file:
            continue
        values.append(float(primary_file in _bug_triage_repo_context_files(item, method_name)))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _count_bug_triage_gold(rows: Sequence, field_name: str) -> int:
    return sum(1 for item in rows if str(getattr(item, field_name, "") or "").strip())


def _average_bug_triage_primary_file_match(rows: Sequence, method_name: str) -> float:
    values = []
    for item in rows:
        gold = str(getattr(item, "gold_primary_file", "") or "").strip()
        if not gold:
            continue
        primary_file = _bug_triage_final_primary_file(item, method_name)
        values.append(float(bool(primary_file) and primary_file == gold))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _average_bug_triage_primary_file_match_when_in_candidates(rows: Sequence, method_name: str) -> float:
    values = []
    for item in rows:
        quality = getattr(item, f"{method_name}_quality", None)
        if quality is None:
            continue
        value = getattr(quality, "primary_file_match_when_in_candidates", None)
        if value is not None:
            values.append(float(value))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _average_bug_triage_gold_in_candidates(rows: Sequence, method_name: str) -> float:
    values = []
    for item in rows:
        quality = getattr(item, f"{method_name}_quality", None)
        if quality is None:
            continue
        value = getattr(quality, "gold_primary_file_in_candidates", None)
        if value is not None:
            values.append(float(value))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _average_bug_triage_gold_in_raw_retrieval(rows: Sequence, method_name: str) -> float:
    values = []
    for item in rows:
        gold = str(getattr(item, "gold_primary_file", "") or "").strip()
        if not gold:
            continue
        values.append(float(gold in _bug_triage_repo_context_files(item, method_name)))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _average_bug_triage_category_match(rows: Sequence, method_name: str) -> float:
    values = []
    for item in rows:
        gold = str(getattr(item, "gold_root_cause_category", "") or "").strip().lower()
        if not gold:
            continue
        method = getattr(item, method_name, None)
        if method is None:
            continue
        decision_state = method.decision_state or {}
        predicted = str(decision_state.get("root_cause_category") or "").strip().lower()
        values.append(float(bool(predicted) and predicted == gold))
    if not values:
        return 0.0
    return sum(values) / len(values)


def _method_has_review(rows: Sequence, method_name: str) -> bool:
    return any(
        bool(getattr(item, method_name, None) and getattr(item, method_name, None).review_state)
        for item in rows
    )


def _method_summary(rows: Sequence, method_name: str, divisor: int) -> Dict[str, float]:
    wall_time_s = _sum_method_field(rows, method_name, "total_wall_time_s")
    transferred_context_tokens = _sum_stage_trace_field(rows, method_name, "selected_context_tokens")
    return {
        "prompt_tokens": _sum_method_field(rows, method_name, "stats.prompt_tokens"),
        "output_tokens": _sum_method_field(rows, method_name, "stats.output_tokens"),
        "model_latency_s": _sum_method_field(rows, method_name, "stats.latency_s"),
        "wall_time_s": wall_time_s,
        "avg_wall_time_s": wall_time_s / divisor,
        "transferred_context_tokens": transferred_context_tokens,
        "avg_transferred_context_tokens": transferred_context_tokens / divisor,
        "required_context_tokens": _sum_stage_trace_field(rows, method_name, "required_context_tokens"),
        "optional_context_tokens": _sum_stage_trace_field(rows, method_name, "optional_context_tokens"),
        "context_read_time_s": _sum_stage_trace_field(rows, method_name, "estimated_context_read_time_s"),
        "met_slo_rate": sum(
            1
            for item in rows
            if getattr(item, method_name, None) is not None and getattr(item, method_name, None).met_slo
        )
        / divisor,
        "completion_proxy": sum(
            getattr(item, method_name, None).completion_proxy
            for item in rows
            if getattr(item, method_name, None) is not None
        )
        / divisor,
        "utility_at_slo_proxy": sum(
            getattr(item, method_name, None).completion_proxy
            if getattr(item, method_name, None) is not None and getattr(item, method_name, None).met_slo
            else 0.0
            for item in rows
        )
        / divisor,
    }


def _flatten_method_metrics(summary: Dict[str, float], method_name: str, metrics: Dict[str, float]) -> None:
    for key, value in metrics.items():
        summary[f"{method_name}_{key}"] = value


def _add_common_deltas(summary: Dict[str, float]) -> None:
    full_prompt = summary["full_history_carry_prompt_tokens"]
    full_context = summary["full_history_carry_transferred_context_tokens"]
    full_wall = summary["full_history_carry_wall_time_s"]
    for method_name in ("structured_fixed_deps", "compressed_history_carry"):
        summary[f"{method_name}_vs_full_history_prompt_reduction_ratio"] = _safe_reduction(
            summary[f"{method_name}_prompt_tokens"],
            full_prompt,
        )
        summary[f"{method_name}_vs_full_history_context_reduction_ratio"] = _safe_reduction(
            summary[f"{method_name}_transferred_context_tokens"],
            full_context,
        )
        summary[f"{method_name}_vs_full_history_wall_time_reduction_ratio"] = _safe_reduction(
            summary[f"{method_name}_wall_time_s"],
            full_wall,
        )


def build_support_ticket_summary(results: List[SupportTicketResult]) -> Dict[str, float]:
    item_count = len(results)
    divisor = item_count or 1
    source_pack_estimates = sorted(item.source_pack_token_estimate for item in results)
    median_estimate = source_pack_estimates[len(source_pack_estimates) // 2] if source_pack_estimates else 0
    downstream_stage_names = ("eligibility_assessment", "resolution_plan", "response_draft")
    summary: Dict[str, float] = {
        "tickets": item_count,
        "source_pack_chars": sum(item.source_pack_chars for item in results),
        "source_pack_token_estimate": sum(item.source_pack_token_estimate for item in results),
        "source_pack_token_estimate_min": source_pack_estimates[0] if source_pack_estimates else 0,
        "source_pack_token_estimate_median": median_estimate,
        "source_pack_token_estimate_max": source_pack_estimates[-1] if source_pack_estimates else 0,
        "labeled_tickets": _count_reference_available(results, "structured_fixed_deps_quality"),
        "reference_slo_seconds": results[0].policy_config.total_slo_seconds if results else 0.0,
        "retrieval_elapsed_seconds": results[0].policy_config.retrieval_elapsed_seconds if results else 0.0,
        "item_label": "ticket",
        "review_ablation_enabled": False,
    }
    for method_name in PRIMARY_METHOD_NAMES:
        _flatten_method_metrics(summary, method_name, _method_summary(results, method_name, divisor))
    _add_common_deltas(summary)

    for method_name in PRIMARY_METHOD_NAMES:
        downstream_context_tokens = _sum_stage_trace_field_for_names(
            results,
            method_name,
            "selected_context_tokens",
            downstream_stage_names,
        )
        summary[f"{method_name}_downstream_transferred_context_tokens"] = downstream_context_tokens
        summary[f"{method_name}_downstream_avg_transferred_context_tokens"] = downstream_context_tokens / divisor
        summary[f"{method_name}_downstream_context_read_time_s"] = _sum_stage_trace_field_for_names(
            results,
            method_name,
            "estimated_context_read_time_s",
            downstream_stage_names,
        )
        summary[f"{method_name}_downstream_prompt_tokens"] = _sum_log_field_for_suffixes(
            results,
            method_name,
            "prompt_tokens",
            downstream_stage_names,
        )
        quality_field = f"{method_name}_quality"
        summary[f"{method_name}_exact_match_rate"] = _average_quality_metric(results, quality_field, "exact_match")
        summary[f"{method_name}_issue_type_match_rate"] = _average_quality_metric(
            results,
            quality_field,
            "issue_type_match",
        )
        summary[f"{method_name}_eligibility_match_rate"] = _average_quality_metric(
            results,
            quality_field,
            "eligibility_match",
        )
        summary[f"{method_name}_resolution_match_rate"] = _average_quality_metric(
            results,
            quality_field,
            "resolution_match",
        )
        summary[f"{method_name}_escalation_match_rate"] = _average_quality_metric(
            results,
            quality_field,
            "escalation_match",
        )
        summary[f"{method_name}_policy_label_coverage"] = _average_quality_metric(
            results,
            quality_field,
            "policy_label_coverage",
        )
        summary[f"{method_name}_response_commitment_coverage"] = _average_quality_metric(
            results,
            quality_field,
            "response_commitment_coverage",
        )

    full_downstream_context = summary["full_history_carry_downstream_transferred_context_tokens"]
    for method_name in ("structured_fixed_deps", "compressed_history_carry"):
        summary[f"{method_name}_vs_full_history_downstream_context_reduction_ratio"] = _safe_reduction(
            summary[f"{method_name}_downstream_transferred_context_tokens"],
            full_downstream_context,
        )

    compression_candidates = _count_stage_traces(
        results,
        "compressed_history_carry",
        stage_name_prefix="compress_before_",
    )
    compression_applied = _count_stage_traces(
        results,
        "compressed_history_carry",
        stage_name_prefix="compress_before_",
        skipped=False,
    )
    summary["compressed_history_carry_compression_stage_count"] = compression_candidates
    summary["compressed_history_carry_compression_applied_count"] = compression_applied
    summary["compressed_history_carry_compression_applied_rate"] = (
        compression_applied / compression_candidates if compression_candidates else 0.0
    )
    return summary


def build_bug_triage_summary(results: List[BugTriageIssueResult]) -> Dict[str, float]:
    item_count = len(results)
    divisor = item_count or 1
    downstream_stage_names = ("hypothesis_shortlist", "triage_decision", "fix_plan", "review")
    summary: Dict[str, float] = {
        "issues": item_count,
        "retrieved_hits": sum(item.retrieved_hits_count for item in results),
        "labeled_primary_files": _count_bug_triage_gold(results, "gold_primary_file"),
        "labeled_root_cause_categories": _count_bug_triage_gold(results, "gold_root_cause_category"),
        "labeled_evidence_labels": sum(1 for item in results if getattr(item, "gold_evidence_labels", None)),
        "reference_slo_seconds": results[0].policy_config.total_slo_seconds if results else 0.0,
        "retrieval_elapsed_seconds": results[0].policy_config.retrieval_elapsed_seconds if results else 0.0,
        "item_label": "issue",
        "review_ablation_enabled": _method_has_review(results, "structured_fixed_deps"),
    }
    for method_name in PRIMARY_METHOD_NAMES:
        _flatten_method_metrics(summary, method_name, _method_summary(results, method_name, divisor))
    _add_common_deltas(summary)

    for method_name in PRIMARY_METHOD_NAMES:
        summary[f"{method_name}_gold_primary_file_raw_recall_rate"] = _average_bug_triage_gold_in_raw_retrieval(
            results,
            method_name,
        )
        downstream_context_tokens = _sum_stage_trace_field_for_names(
            results,
            method_name,
            "selected_context_tokens",
            downstream_stage_names,
        )
        summary[f"{method_name}_downstream_transferred_context_tokens"] = downstream_context_tokens
        summary[f"{method_name}_downstream_avg_transferred_context_tokens"] = downstream_context_tokens / divisor
        summary[f"{method_name}_downstream_context_read_time_s"] = _sum_stage_trace_field_for_names(
            results,
            method_name,
            "estimated_context_read_time_s",
            downstream_stage_names,
        )
        summary[f"{method_name}_downstream_prompt_tokens"] = _sum_log_field_for_suffixes(
            results,
            method_name,
            "prompt_tokens",
            downstream_stage_names,
        )
        summary[f"{method_name}_primary_file_match_rate"] = _average_bug_triage_primary_file_match(
            results,
            method_name,
        )
        summary[f"{method_name}_gold_primary_file_candidate_recall_rate"] = _average_bug_triage_gold_in_candidates(
            results,
            method_name,
        )
        summary[f"{method_name}_primary_file_match_rate_when_in_candidates"] = _average_bug_triage_primary_file_match_when_in_candidates(
            results,
            method_name,
        )
        summary[f"{method_name}_root_cause_category_match_rate"] = _average_bug_triage_category_match(
            results,
            method_name,
        )
        summary[f"{method_name}_retrieved_file_hit_rate"] = _average_bug_triage_retrieved_file_hit(
            results,
            method_name,
        )
        quality_field = f"{method_name}_quality"
        summary[f"{method_name}_evidence_label_coverage"] = _average_quality_metric(
            results,
            quality_field,
            "evidence_label_coverage",
        )
        if summary["review_ablation_enabled"]:
            summary[f"{method_name}_review_accept_rate"] = _average_bug_triage_review_accept(results, method_name)
            summary[f"{method_name}_review_override_rate"] = _average_bug_triage_review_override(results, method_name)

    full_downstream_context = summary["full_history_carry_downstream_transferred_context_tokens"]
    for method_name in ("structured_fixed_deps", "compressed_history_carry"):
        summary[f"{method_name}_vs_full_history_downstream_context_reduction_ratio"] = _safe_reduction(
            summary[f"{method_name}_downstream_transferred_context_tokens"],
            full_downstream_context,
        )

    compression_candidates = _count_stage_traces(
        results,
        "compressed_history_carry",
        stage_name_prefix="compress_before_",
    )
    compression_applied = _count_stage_traces(
        results,
        "compressed_history_carry",
        stage_name_prefix="compress_before_",
        skipped=False,
    )
    summary["compressed_history_carry_compression_stage_count"] = compression_candidates
    summary["compressed_history_carry_compression_applied_count"] = compression_applied
    summary["compressed_history_carry_compression_applied_rate"] = (
        compression_applied / compression_candidates if compression_candidates else 0.0
    )
    return summary


def _print_summary(summary: Dict[str, float], label: str) -> None:
    count_key = "tickets" if "tickets" in summary else "issues"
    print("=" * 60)
    print(f"{label} {count_key}:".ljust(32) + f"{summary[count_key]}")
    print("Main experiment:")
    print(f"  Structured-FixedDeps prompt:".ljust(32) + f"{summary['structured_fixed_deps_prompt_tokens']:.0f}")
    print(f"  Full-History prompt:".ljust(32) + f"{summary['full_history_carry_prompt_tokens']:.0f}")
    print(f"  Compressed-History prompt:".ljust(32) + f"{summary['compressed_history_carry_prompt_tokens']:.0f}")
    print(f"  Structured-FixedDeps context:".ljust(32) + f"{summary['structured_fixed_deps_transferred_context_tokens']:.0f}")
    print(f"  Full-History context:".ljust(32) + f"{summary['full_history_carry_transferred_context_tokens']:.0f}")
    print(f"  Compressed-History context:".ljust(32) + f"{summary['compressed_history_carry_transferred_context_tokens']:.0f}")
    print(f"  Structured-FixedDeps wall (s):".ljust(32) + f"{summary['structured_fixed_deps_wall_time_s']:.3f}")
    print(f"  Full-History wall (s):".ljust(32) + f"{summary['full_history_carry_wall_time_s']:.3f}")
    print(f"  Compressed-History wall (s):".ljust(32) + f"{summary['compressed_history_carry_wall_time_s']:.3f}")
    if "labeled_tickets" in summary:
        print(f"Labeled tickets:".ljust(32) + f"{summary['labeled_tickets']}")
        print(f"  Structured-FixedDeps exact:".ljust(32) + f"{summary['structured_fixed_deps_exact_match_rate']:.2%}")
        print(f"  Full-History exact:".ljust(32) + f"{summary['full_history_carry_exact_match_rate']:.2%}")
        print(f"  Compressed-History exact:".ljust(32) + f"{summary['compressed_history_carry_exact_match_rate']:.2%}")
        print(
            f"  Structured-FixedDeps downstream ctx:".ljust(32)
            + f"{summary['structured_fixed_deps_downstream_transferred_context_tokens']:.0f}"
        )
        print(
            f"  Full-History downstream ctx:".ljust(32)
            + f"{summary['full_history_carry_downstream_transferred_context_tokens']:.0f}"
        )
        print(
            f"  Compressed-History downstream ctx:".ljust(32)
            + f"{summary['compressed_history_carry_downstream_transferred_context_tokens']:.0f}"
        )
        print(
            f"  Structured-FixedDeps issue:".ljust(32)
            + f"{summary['structured_fixed_deps_issue_type_match_rate']:.2%}"
        )
        print(
            f"  Full-History issue:".ljust(32)
            + f"{summary['full_history_carry_issue_type_match_rate']:.2%}"
        )
        print(
            f"  Compressed-History issue:".ljust(32)
            + f"{summary['compressed_history_carry_issue_type_match_rate']:.2%}"
        )
        print(
            f"  Structured-FixedDeps eligibility:".ljust(32)
            + f"{summary['structured_fixed_deps_eligibility_match_rate']:.2%}"
        )
        print(
            f"  Full-History eligibility:".ljust(32)
            + f"{summary['full_history_carry_eligibility_match_rate']:.2%}"
        )
        print(
            f"  Compressed-History eligibility:".ljust(32)
            + f"{summary['compressed_history_carry_eligibility_match_rate']:.2%}"
        )
    else:
        print(f"Labeled primary files:".ljust(32) + f"{summary['labeled_primary_files']}")
        print(
            f"  Structured raw recall:".ljust(32)
            + f"{summary['structured_fixed_deps_gold_primary_file_raw_recall_rate']:.2%}"
        )
        print(
            f"  Full-History raw recall:".ljust(32)
            + f"{summary['full_history_carry_gold_primary_file_raw_recall_rate']:.2%}"
        )
        print(
            f"  Compressed-History raw recall:".ljust(32)
            + f"{summary['compressed_history_carry_gold_primary_file_raw_recall_rate']:.2%}"
        )
        print(
            f"  Structured-FixedDeps cand recall:".ljust(32)
            + f"{summary['structured_fixed_deps_gold_primary_file_candidate_recall_rate']:.2%}"
        )
        print(
            f"  Full-History cand recall:".ljust(32)
            + f"{summary['full_history_carry_gold_primary_file_candidate_recall_rate']:.2%}"
        )
        print(
            f"  Compressed-History cand recall:".ljust(32)
            + f"{summary['compressed_history_carry_gold_primary_file_candidate_recall_rate']:.2%}"
        )
        print(
            f"  Structured-FixedDeps file:".ljust(32)
            + f"{summary['structured_fixed_deps_primary_file_match_rate']:.2%}"
        )
        print(
            f"  Full-History file:".ljust(32)
            + f"{summary['full_history_carry_primary_file_match_rate']:.2%}"
        )
        print(
            f"  Compressed-History file:".ljust(32)
            + f"{summary['compressed_history_carry_primary_file_match_rate']:.2%}"
        )
        print(
            f"  Structured-FixedDeps file|cand:".ljust(32)
            + f"{summary['structured_fixed_deps_primary_file_match_rate_when_in_candidates']:.2%}"
        )
        print(
            f"  Full-History file|cand:".ljust(32)
            + f"{summary['full_history_carry_primary_file_match_rate_when_in_candidates']:.2%}"
        )
        print(
            f"  Compressed-History file|cand:".ljust(32)
            + f"{summary['compressed_history_carry_primary_file_match_rate_when_in_candidates']:.2%}"
        )
        print(f"Labeled categories:".ljust(32) + f"{summary['labeled_root_cause_categories']}")
        print(
            f"  Structured-FixedDeps cat:".ljust(32)
            + f"{summary['structured_fixed_deps_root_cause_category_match_rate']:.2%}"
        )
        print(
            f"  Full-History cat:".ljust(32)
            + f"{summary['full_history_carry_root_cause_category_match_rate']:.2%}"
        )
        print(
            f"  Compressed-History cat:".ljust(32)
            + f"{summary['compressed_history_carry_root_cause_category_match_rate']:.2%}"
        )
        print(f"Labeled evidence labels:".ljust(32) + f"{summary['labeled_evidence_labels']}")
        print(
            f"  Structured-FixedDeps evidence:".ljust(32)
            + f"{summary['structured_fixed_deps_evidence_label_coverage']:.2%}"
        )
        print(
            f"  Full-History evidence:".ljust(32)
            + f"{summary['full_history_carry_evidence_label_coverage']:.2%}"
        )
        print(
            f"  Compressed-History evidence:".ljust(32)
            + f"{summary['compressed_history_carry_evidence_label_coverage']:.2%}"
        )
        print(
            f"  Structured-FixedDeps downstream ctx:".ljust(32)
            + f"{summary['structured_fixed_deps_downstream_transferred_context_tokens']:.0f}"
        )
        print(
            f"  Full-History downstream ctx:".ljust(32)
            + f"{summary['full_history_carry_downstream_transferred_context_tokens']:.0f}"
        )
        print(
            f"  Compressed-History downstream ctx:".ljust(32)
            + f"{summary['compressed_history_carry_downstream_transferred_context_tokens']:.0f}"
        )
        print(
            f"  Compression applied rate:".ljust(32)
            + f"{summary['compressed_history_carry_compression_applied_rate']:.2%}"
        )
        if summary.get("review_ablation_enabled"):
            print(
                f"  Structured-FixedDeps review ok:".ljust(32)
                + f"{summary['structured_fixed_deps_review_accept_rate']:.2%}"
            )
            print(
                f"  Full-History review ok:".ljust(32)
                + f"{summary['full_history_carry_review_accept_rate']:.2%}"
            )
            print(
                f"  Compressed-History review ok:".ljust(32)
                + f"{summary['compressed_history_carry_review_accept_rate']:.2%}"
            )
    print("=" * 60)


def print_support_ticket_summary(results: List[SupportTicketResult]) -> None:
    if not results:
        print("No support tickets were found.")
        return
    _print_summary(build_support_ticket_summary(results), "Support")


def print_bug_triage_summary(results: List[BugTriageIssueResult]) -> None:
    if not results:
        print("No bug-triage issues were found.")
        return
    _print_summary(build_bug_triage_summary(results), "Bug-triage")
