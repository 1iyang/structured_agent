from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable, Dict, List

from .models import StageDefinition

ExecutorFn = Callable[
    [StageDefinition, Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]],
    Dict[str, Any],
]
ToolFn = Callable[..., Any]


def _text(*parts: object) -> str:
    return "\n".join(str(part) for part in parts if part).lower()


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _customer_supplied_visual_proof(text: str) -> bool:
    negative_phrases = [
        "have not sent photos",
        "haven't sent photos",
        "have not sent photo",
        "haven't sent photo",
        "did not send photos",
        "didn't send photos",
        "did not send photo",
        "didn't send photo",
        "not attached photos",
        "not attached photo",
        "no photos yet",
        "no photo yet",
        "without photos",
        "without photo",
    ]
    if any(phrase in text for phrase in negative_phrases):
        return False
    proof_phrases = [
        "attached photo",
        "attached photos",
        "attached picture",
        "attached pictures",
        "attached image",
        "attached images",
        "uploaded photo",
        "uploaded photos",
        "shared photo",
        "shared photos",
        "sent photo",
        "sent photos",
        "see photo",
        "see photos",
        "here are photos",
    ]
    return any(phrase in text for phrase in proof_phrases)


def _safe_read_text(path: Path) -> str:
    try:
        if path.stat().st_size > 250_000:
            return ""
        return path.read_text(errors="ignore")
    except (OSError, UnicodeDecodeError):
        return ""


def _iter_candidate_files(repo_root: Path, repo_subpaths: List[str]) -> List[tuple[Path, Path]]:
    candidate_files: List[tuple[Path, Path]] = []
    seen_paths = set()
    allowed_suffixes = {".py", ".ts", ".js", ".json", ".md", ".yaml", ".yml"}
    excluded_parts = {"runs", "__pycache__", ".git", "examples", "docs", "skill_workflow_runtime"}
    for subpath in repo_subpaths:
        root = (repo_root / subpath).resolve()
        if not root.exists():
            continue
        if root.is_file() and root.suffix in allowed_suffixes:
            resolved = str(root)
            if resolved not in seen_paths:
                seen_paths.add(resolved)
                candidate_files.append((root.parent, root))
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in allowed_suffixes:
                continue
            if any(part in excluded_parts for part in path.parts):
                continue
            resolved = str(path.resolve())
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            candidate_files.append((root, path))
    return candidate_files


def _match_snippet(text: str, queries: List[str]) -> str:
    lower = text.lower()
    for query in queries:
        q = query.lower().strip()
        if not q:
            continue
        index = lower.find(q)
        if index >= 0:
            start = max(0, index - 80)
            end = min(len(text), index + max(len(q), 80))
            return " ".join(text[start:end].split())
    return " ".join(text[:180].split())


def bug_local_repo_search(*, repo_root: str, repo_subpaths: List[str], queries: List[str], limit: int = 8) -> List[Dict[str, Any]]:
    root = Path(repo_root).resolve()
    scored: List[Dict[str, Any]] = []
    for search_root, file_path in _iter_candidate_files(root, repo_subpaths):
        text = _safe_read_text(file_path)
        if not text:
            continue
        lower_text = text.lower()
        try:
            relative_path = str(file_path.relative_to(search_root))
        except ValueError:
            relative_path = str(file_path.name)
        lower_path = relative_path.lower()
        score = 0
        for query in queries:
            q = query.lower().strip()
            if not q:
                continue
            if q in lower_path:
                score += 4
            if q in lower_text:
                score += 2
        if score <= 0:
            continue
        scored.append(
            {
                "path": relative_path,
                "snippet": _match_snippet(text, queries),
                "tags": ["local_repo_search"],
                "score": score,
            }
        )
    scored.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return scored[:limit]


def support_ticket_intake(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    text = _text(task.get("customer_message"), task.get("context_pack"))
    if any(token in text for token in ["refund", "chargeback", "money back"]):
        issue_type = "billing"
    elif any(token in text for token in ["login", "password", "account", "sso"]):
        issue_type = "account_access"
    elif any(token in text for token in ["broken", "damaged", "replacement"]):
        issue_type = "damaged_item"
    elif any(token in text for token in ["late", "delay", "shipping", "delivery"]):
        issue_type = "delivery_issue"
    else:
        issue_type = "general_support"

    urgency = "high" if any(token in text for token in ["urgent", "outage", "blocked"]) else "normal"
    goal = task.get("customer_goal_override") or task.get("customer_message", "").split(".")[0].strip()

    missing_information: List[str] = []
    if "order id" not in text and issue_type in {"billing", "delivery_issue", "damaged_item"}:
        missing_information.append("order_id")
    customer_message = str(task.get("customer_message", "")).lower()
    if issue_type == "damaged_item" and not _customer_supplied_visual_proof(customer_message):
        missing_information.append("damage_proof")

    return {
        "state": {
            "issue_type": issue_type,
            "customer_goal": goal,
            "urgency": urgency,
            "missing_information": missing_information,
        },
        "artifacts": {},
    }


def support_policy_evidence(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    text = _text(task.get("context_pack"), task.get("customer_message"))
    policy_signals: List[str] = []
    if "proof required" in text or "documentation required" in text:
        policy_signals.append("customer_proof_required")
    if "manager review" in text or "specialist review" in text:
        policy_signals.append("specialist_review_required")
    if "frontline can approve" in text or "frontline may issue" in text:
        policy_signals.append("standard_remedy_available")
    if "non-refundable" in text or "not refundable" in text:
        policy_signals.append("approval_not_guaranteed")
    if "shipping fee credit" in text or "small credit" in text:
        policy_signals.append("limited_concession_available")
    if "identity verification" in text:
        policy_signals.append("identity_verification_required")

    evidence_factors: List[str] = []
    if state.get("missing_information"):
        evidence_factors.append("missing_customer_evidence")
    if "frontline" in text and "cannot" not in text:
        evidence_factors.append("frontline_action_available")
    if "specialist" in text:
        evidence_factors.append("specialist_queue_required")
    if "urgent" in text or "outage" in text:
        evidence_factors.append("high_time_sensitivity")

    issue_type = state.get("issue_type")
    if issue_type == "billing":
        candidate_actions = ["refund_review", "clarify_charge_state"]
    elif issue_type == "account_access":
        candidate_actions = ["identity_verification", "specialist_escalation"]
    elif issue_type == "damaged_item":
        candidate_actions = ["replacement_if_stocked", "collect_damage_proof"]
    elif issue_type == "delivery_issue":
        candidate_actions = ["shipping_credit", "set_delivery_expectation"]
    else:
        candidate_actions = ["clarify_request", "general_escalation"]

    if issue_type == "billing":
        preferred_resolution_path = "refund"
    elif issue_type == "account_access":
        preferred_resolution_path = "account_recovery"
    elif issue_type == "damaged_item":
        preferred_resolution_path = "replacement" if "in stock" in text or "replacement" in text else "refund"
    elif issue_type == "delivery_issue":
        preferred_resolution_path = "goodwill_credit"
    else:
        preferred_resolution_path = "escalation"

    blocking_requirements: List[str] = []
    if issue_type == "damaged_item" and state.get("missing_information"):
        blocking_requirements.append("customer_evidence")
    if "identity_verification_required" in policy_signals:
        blocking_requirements.append("customer_verification")
    if "specialist_review_required" in policy_signals or "specialist_queue_required" in evidence_factors:
        blocking_requirements.append("internal_review")

    risk_flags: List[str] = []
    if "medical exception" in text:
        risk_flags.append("exception_policy")
    if "enterprise" in text and "sso" in text:
        risk_flags.append("tenant_wide_impact")

    return {
        "state": {
            "policy_signals": _dedupe(policy_signals),
            "evidence_factors": _dedupe(evidence_factors),
            "preferred_resolution_path": preferred_resolution_path,
            "blocking_requirements": _dedupe(blocking_requirements),
            "candidate_actions": _dedupe(candidate_actions),
            "risk_flags": _dedupe(risk_flags),
        },
        "artifacts": {},
    }


def support_eligibility(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    signals = set(state.get("policy_signals", []))
    factors = set(state.get("evidence_factors", []))
    blockers = set(state.get("blocking_requirements", []))
    preferred_path = str(state.get("preferred_resolution_path", ""))

    if "internal_review" in blockers or "specialist_review_required" in signals or "specialist_queue_required" in factors:
        decision = "escalate"
    elif blockers or "missing_customer_evidence" in factors:
        decision = "needs_info"
    elif preferred_path == "goodwill_credit" and "approval_not_guaranteed" in signals and "limited_concession_available" in signals:
        decision = "partial"
    elif "approval_not_guaranteed" in signals and "standard_remedy_available" not in signals:
        decision = "denied"
    else:
        decision = "approved"

    rationale = (
        "Decision based on "
        f"signals={sorted(signals)}, factors={sorted(factors)}, "
        f"blockers={sorted(blockers)}, preferred_path={preferred_path or 'unknown'}."
    )
    return {
        "state": {
            "eligibility_decision": decision,
            "eligibility_rationale": rationale,
        },
        "artifacts": {},
    }


def support_resolution(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    decision = state.get("eligibility_decision")
    issue_type = state.get("issue_type")
    preferred_path = str(state.get("preferred_resolution_path", ""))
    blockers = list(state.get("blocking_requirements", []))
    actions = set(state.get("candidate_actions", []))

    if decision == "needs_info":
        if preferred_path in {"replacement", "refund", "goodwill_credit", "account_recovery"}:
            category = preferred_path
            missing_information = list(state.get("missing_information", []))
            if missing_information:
                blocker_summary = ", ".join(missing_information)
            elif blockers:
                blocker_summary = ", ".join(blockers)
            else:
                blocker_summary = "the remaining requirement"
            steps = [
                f"Collect {blocker_summary}.",
                f"Continue with the {preferred_path} path once the blocker is cleared.",
            ]
        else:
            category = "clarification_request"
            steps = ["Collect the missing evidence.", "Hold off on irreversible promises."]
    elif decision == "escalate":
        category = "escalation"
        steps = ["Route the case to the specialist queue.", "Explain the next checkpoint."]
    elif decision == "partial":
        category = "goodwill_credit"
        steps = ["Offer the limited concession allowed by policy.", "Explain why the full request is not supported."]
    elif decision == "denied":
        category = "policy_denial"
        steps = ["Decline the requested remedy.", "Point to the relevant policy constraint."]
    elif issue_type == "account_access":
        category = "account_recovery"
        steps = ["Verify identity.", "Complete the recovery workflow."]
    elif "replacement_if_stocked" in actions:
        category = "replacement"
        steps = ["Check current stock.", "Issue the replacement if available."]
    else:
        category = "refund"
        steps = ["Approve the standard remedy.", "Close the request after confirmation."]

    return {
        "state": {
            "resolution_category": category,
            "resolution_steps": steps,
        },
        "artifacts": {},
    }


def support_response(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    handoff_status = state.get("handoff_status", "ready")
    guardrails = list(state.get("response_guardrails", []))
    goal = state.get("customer_goal", "your request")
    decision = state.get("eligibility_decision", "pending")
    category = state.get("resolution_category", "clarification_request")
    steps = state.get("resolution_steps", [])
    response = (
        f"We reviewed {goal}. Current status: {decision}. "
        f"Next path: {category}. "
        f"Planned steps: {' '.join(steps)}"
    )
    if handoff_status != "ready":
        response += f" Handoff status: {handoff_status}."
    if guardrails:
        response += f" Guardrails: {' '.join(guardrails)}"
    return {
        "state": {
            "response_message": response,
        },
        "artifacts": {},
    }


def support_resolution_verification(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    decision = str(state.get("eligibility_decision", ""))
    category = str(state.get("resolution_category", ""))
    blockers = list(state.get("blocking_requirements", []))
    missing_information = list(state.get("missing_information", []))
    risk_flags = list(state.get("risk_flags", []))
    text = _text(task.get("customer_message"), task.get("context_pack"))

    response_guardrails: List[str] = []
    if decision == "needs_info":
        handoff_status = "blocked"
        if missing_information:
            response_guardrails.append(
                f"State the missing requirement before confirming {category}: {', '.join(missing_information)}."
            )
        elif blockers:
            response_guardrails.append(
                f"Do not confirm {category} until blockers are cleared: {', '.join(blockers)}."
            )
        else:
            response_guardrails.append("Do not promise irreversible action until the blocker is cleared.")
    elif decision == "escalate":
        handoff_status = "escalate"
        response_guardrails.append("Explain that specialist handling is required before final resolution.")
    else:
        handoff_status = "ready"

    if "approval_not_guaranteed" in state.get("policy_signals", []):
        response_guardrails.append("Avoid absolute promises until the policy exception is confirmed.")
    if "urgent" in text or "trip" in text or "outage" in text:
        response_guardrails.append("Acknowledge urgency without committing to unsupported expedite steps.")
    for risk_flag in risk_flags:
        response_guardrails.append(f"Surface the operational risk flag: {risk_flag}.")

    return {
        "state": {
            "handoff_status": handoff_status,
            "response_guardrails": _dedupe(response_guardrails),
        },
        "artifacts": {},
    }


def support_eligibility_with_context(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    base = support_eligibility(stage, task, state, artifacts, toolbox)
    text = _text(task.get("customer_message"), task.get("context_pack"))
    if "documentation required" in text or "proof required" in text:
        base["state"]["eligibility_decision"] = "needs_info"
    elif "specialist review" in text or "manager review" in text:
        base["state"]["eligibility_decision"] = "escalate"
    base["state"]["eligibility_rationale"] = (
        base["state"]["eligibility_rationale"]
        + " Context was also consulted from the full evidence pack."
    )
    return base


def support_resolution_with_context(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    base = support_resolution(stage, task, state, artifacts, toolbox)
    text = _text(task.get("customer_message"), task.get("context_pack"))
    if base["state"]["resolution_category"] == "refund" and "non-refundable" in text:
        base["state"]["resolution_category"] = "policy_denial"
        base["state"]["resolution_steps"] = [
            "Explain the policy constraint from the source material.",
            "Offer the nearest policy-safe alternative if available.",
        ]
    return base


def support_response_with_context(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    base = support_response(stage, task, state, artifacts, toolbox)
    text = _text(task.get("customer_message"), task.get("context_pack"))
    if "urgent" in text or "outage" in text:
        base["state"]["response_message"] += " We also considered the urgency signaled in the original evidence."
    return base


def bug_signal_extractor(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    text = _text(
        task.get("title"),
        task.get("description"),
        task.get("log_excerpt"),
        task.get("stacktrace_excerpt"),
    )
    error_messages = _dedupe(re.findall(r"[A-Za-z_]+(?:Error|Exception)", text))
    file_hints = _dedupe(re.findall(r"[A-Za-z0-9_/]+\.(?:py|ts|js|json)", text))
    symbol_hints = _dedupe(re.findall(r"(?:in|function|method)\s+([A-Za-z_][A-Za-z0-9_]*)", text))

    suspected_modules: List[str] = []
    for token in ["json", "state", "summary", "report", "prompt", "token", "artifact", "config"]:
        if token in text:
            suspected_modules.append(token)

    return {
        "state": {
            "bug_summary": task.get("title", "").strip(),
            "error_messages": error_messages,
            "file_hints": file_hints,
            "symbol_hints": symbol_hints,
            "suspected_modules": _dedupe(suspected_modules),
        },
        "artifacts": {},
    }


def bug_query_expansion(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    exact_queries = _dedupe(list(state.get("error_messages", [])) + list(state.get("file_hints", [])))
    symbol_queries = _dedupe(list(state.get("symbol_hints", [])))
    semantic_queries = _dedupe(
        [state.get("bug_summary", "")]
        + [f"{module} handling" for module in state.get("suspected_modules", [])]
    )
    graph_queries = _dedupe([hint.rsplit("/", 1)[0] for hint in state.get("file_hints", []) if "/" in hint])

    return {
        "state": {
            "exact_queries": [query for query in exact_queries if query],
            "symbol_queries": [query for query in symbol_queries if query],
            "semantic_queries": [query for query in semantic_queries if query],
            "graph_queries": [query for query in graph_queries if query],
        },
        "artifacts": {},
    }


def _artifact_score(artifact: Dict[str, Any], queries: List[str]) -> int:
    path = str(artifact.get("path", "")).lower()
    snippet = str(artifact.get("snippet", "")).lower()
    tags = " ".join(str(tag) for tag in artifact.get("tags", []))
    haystack = " ".join([path, snippet, tags])
    score = 0
    for query in queries:
        q = query.lower().strip()
        if not q:
            continue
        if q in path:
            score += 4
        if q in snippet:
            score += 3
        if q in tags:
            score += 2
    return score


def _stage_retrieval_queries(stage: StageDefinition, state: Dict[str, Any]) -> List[str]:
    queries: List[str] = []
    for field_name in stage.reads.state:
        normalized = field_name.lower()
        if "query" not in normalized and "queries" not in normalized:
            continue
        value = state.get(field_name)
        if isinstance(value, list):
            queries.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            queries.append(value.strip())
    if queries:
        return _dedupe(queries)
    fallback_queries = (
        list(state.get("exact_queries", []))
        + list(state.get("symbol_queries", []))
        + list(state.get("semantic_queries", []))
        + list(state.get("graph_queries", []))
    )
    return _dedupe([str(query).strip() for query in fallback_queries if str(query).strip()])


def llm_json_stage(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    backend = toolbox.get("__llm_backend")
    if backend is None:
        raise ValueError("LLM backend is not available for llm.json_stage executor.")
    output_schema = toolbox.get("__output_schema")
    if output_schema is None:
        raise ValueError(f"Stage {stage.name} is missing output schema for llm.json_stage executor.")
    state_output = backend.generate_json(
        stage=stage,
        instruction=stage.instruction,
        task_view=task,
        state_view=state,
        artifact_view=artifacts,
        output_schema=output_schema,
    )
    return {"state": state_output, "artifacts": {}}


def artifact_seed_from_task(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    source_items: List[Any] = []
    source_name = ""
    for field_name in stage.reads.task:
        value = task.get(field_name)
        if isinstance(value, list):
            source_items = list(value)
            source_name = field_name
            break
    artifact_field = stage.writes_artifacts[0] if stage.writes_artifacts else "seeded_artifacts"
    summary_field = stage.writes[0] if stage.writes else "artifact_seed_summary"
    return {
        "state": {
            summary_field: f"Seeded {len(source_items)} artifacts from task field {source_name or 'unknown'}.",
        },
        "artifacts": {
            artifact_field: source_items,
        },
    }


def tool_retrieve_artifacts(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    artifact_inputs = [
        value
        for artifact_name, value in artifacts.items()
        if artifact_name in stage.reads.artifacts and isinstance(value, list)
    ]
    if artifact_inputs:
        task_artifacts = list(artifact_inputs[0])
    elif stage.metadata.tool_intent == "re_retrieval":
        task_artifacts = list(task.get("followup_repository_artifacts", [])) or list(
            task.get("repository_artifacts", [])
        )
    else:
        task_artifacts = list(task.get("repository_artifacts", [])) or list(
            task.get("followup_repository_artifacts", [])
        )
    queries = _stage_retrieval_queries(stage, state)
    if not task_artifacts:
        repo_search = toolbox.get("bug.local_repo_search")
        if repo_search is None:
            raise ValueError("bug.local_repo_search tool is not available for retrieval stage.")
        task_artifacts = repo_search(
            repo_root=str(Path.cwd()),
            repo_subpaths=list(task.get("repo_subpaths", [])),
            queries=queries,
            limit=8,
        )
    scored = []
    for artifact in task_artifacts:
        score = _artifact_score(artifact, queries)
        if score > 0:
            with_score = dict(artifact)
            with_score["score"] = score
            scored.append(with_score)

    scored.sort(key=lambda item: (-int(item["score"]), str(item.get("path", ""))))
    top = scored[:4]
    summary_field = stage.writes[0] if stage.writes else "retrieval_summary"
    artifact_field = stage.writes_artifacts[0] if stage.writes_artifacts else "retrieved_artifacts"
    summary_prefix = "Re-retrieved" if stage.metadata.tool_intent == "re_retrieval" else "Retrieved"
    return {
        "state": {
            summary_field: (
                f"{summary_prefix} {len(top)} candidate artifacts from "
                f"{len(task_artifacts)} repository entries."
            ),
        },
        "artifacts": {
            artifact_field: top,
        },
    }


def bug_high_recall_retrieval(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    return tool_retrieve_artifacts(stage, task, state, artifacts, toolbox)


def bug_evidence_screen(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    retrieved = list(artifacts.get("retrieved_artifacts", []))
    candidate_files = [artifact.get("path", "") for artifact in retrieved[:3]]
    text = _text(state.get("bug_summary"), state.get("retrieval_summary"))
    labels: List[str] = []
    if "config" in text or any("config" in file for file in candidate_files[:1]):
        labels.append("config_path")
    if any("state" in file for file in candidate_files):
        labels.append("state_update_path")
    if any("summary" in file or "report" in file for file in candidate_files):
        labels.append("reporting_path")
    if any("utils" in file for file in candidate_files):
        labels.append("shared_helper_path")
    if any("engine" in file for file in candidate_files):
        labels.append("runtime_callsite_path")

    missing_evidence: List[str] = []
    if not candidate_files:
        missing_evidence.append("No candidate files matched the available evidence.")

    return {
        "state": {
            "candidate_files": _dedupe([item for item in candidate_files if item]),
            "applied_evidence_labels": _dedupe(labels) or ["generic_code_path"],
            "missing_evidence": missing_evidence,
        },
        "artifacts": {},
    }


def bug_hypothesis_shortlist(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_files = list(state.get("candidate_files", []))
    labels = list(state.get("applied_evidence_labels", []))
    hypotheses: List[str] = []
    for file_path in candidate_files[:2]:
        hypotheses.append(f"{file_path} likely owns the failure because of labels={labels}.")
    if not hypotheses:
        hypotheses.append("Need more repository evidence before ranking a fix location.")
    return {
        "state": {
            "hypotheses": hypotheses,
        },
        "artifacts": {},
    }


def bug_triage_decision(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_files = list(state.get("candidate_files", []))
    labels = set(state.get("applied_evidence_labels", []))
    primary_file = candidate_files[0] if candidate_files else ""
    if "shared_helper_path" in labels:
        helper_candidate = next((path for path in candidate_files if "utils" in path or "helper" in path), "")
        if helper_candidate:
            primary_file = helper_candidate

    if "config_path" in labels:
        category = "configuration"
    elif "shared_helper_path" in labels:
        category = "error_recovery"
    elif "state_update_path" in labels:
        category = "state_management"
    elif "runtime_callsite_path" in labels:
        category = "error_recovery"
    elif "reporting_path" in labels:
        category = "artifact_lifecycle"
    else:
        category = "unknown"

    rationale = f"Selected {primary_file or 'no file'} from candidates={candidate_files} with labels={sorted(labels)}."
    return {
        "state": {
            "primary_file": primary_file,
            "root_cause_category": category,
            "decision_rationale": rationale,
        },
        "artifacts": {},
    }


def bug_fix_plan(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    category = state.get("root_cause_category", "unknown")
    primary_file = state.get("primary_file", "")
    decision_status = state.get("decision_status", "supported")
    review_notes = list(state.get("review_notes", []))
    if category == "configuration":
        steps = [f"Review defaults in {primary_file}.", "Add a regression test for the configuration boundary."]
    elif category == "state_management":
        steps = [f"Audit state transitions in {primary_file}.", "Add a test that exercises the failing state path."]
    elif category == "artifact_lifecycle":
        steps = [f"Inspect output shaping in {primary_file}.", "Verify downstream report fields stay specialized."]
    else:
        steps = [f"Inspect {primary_file or 'the candidate files'} closely.", "Add a focused regression test."]
    if decision_status != "supported":
        steps.insert(0, f"Treat the current triage result as {decision_status} and confirm evidence before landing a fix.")
    for note in review_notes:
        steps.append(f"Verification note: {note}")
    return {
        "state": {
            "fix_plan": steps,
        },
        "artifacts": {},
    }


def bug_decision_verification(
    stage: StageDefinition,
    task: Dict[str, Any],
    state: Dict[str, Any],
    artifacts: Dict[str, Any],
    toolbox: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_files = list(state.get("candidate_files", []))
    primary_file = str(state.get("primary_file", ""))
    missing_evidence = list(state.get("missing_evidence", []))
    labels = list(state.get("applied_evidence_labels", []))

    review_notes: List[str] = []
    if missing_evidence:
        decision_status = "needs_more_retrieval"
        review_notes.extend(missing_evidence)
    elif not primary_file or primary_file not in candidate_files:
        decision_status = "weak_evidence"
        review_notes.append("Selected primary file is not strongly grounded in the screened candidate pool.")
    else:
        decision_status = "supported"

    if labels == ["generic_code_path"]:
        review_notes.append("Evidence labels are still generic; inspect neighboring modules before finalizing the patch.")

    return {
        "state": {
            "decision_status": decision_status,
            "review_notes": _dedupe(review_notes),
        },
        "artifacts": {},
    }


BUILTIN_EXECUTORS: Dict[str, ExecutorFn] = {
    "llm.json_stage": llm_json_stage,
    "artifact.seed_from_task": artifact_seed_from_task,
    "tool.retrieve_artifacts": tool_retrieve_artifacts,
    "support.ticket_intake": support_ticket_intake,
    "support.policy_evidence": support_policy_evidence,
    "support.eligibility_assessment": support_eligibility,
    "support.eligibility_assessment_with_context": support_eligibility_with_context,
    "support.resolution_plan": support_resolution,
    "support.resolution_plan_with_context": support_resolution_with_context,
    "support.resolution_verification": support_resolution_verification,
    "support.response_draft": support_response,
    "support.response_draft_with_context": support_response_with_context,
    "bug.signal_extractor": bug_signal_extractor,
    "bug.query_expansion": bug_query_expansion,
    "bug.high_recall_retrieval": bug_high_recall_retrieval,
    "bug.evidence_screen": bug_evidence_screen,
    "bug.hypothesis_shortlist": bug_hypothesis_shortlist,
    "bug.triage_decision": bug_triage_decision,
    "bug.decision_verification": bug_decision_verification,
    "bug.fix_plan": bug_fix_plan,
}

BUILTIN_TOOLS: Dict[str, ToolFn] = {
    "bug.local_repo_search": bug_local_repo_search,
}
