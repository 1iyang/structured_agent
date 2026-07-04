"""Schemas for the support and bug-triage state-transfer experiments."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


SupportIssueType = Literal[
    "damaged_item",
    "billing_refund",
    "delivery_delay",
    "account_access",
    "warranty_claim",
    "policy_exception",
    "unknown",
]

SupportEligibilityDecision = Literal[
    "approved",
    "denied",
    "needs_info",
    "partial",
    "escalate",
]

SupportResolutionCategory = Literal[
    "refund",
    "replacement",
    "troubleshoot",
    "account_recovery",
    "goodwill_credit",
    "policy_denial",
    "clarification_request",
    "escalation",
]

SupportPolicyLabel = Literal[
    "standard_remedy_available",
    "customer_proof_required",
    "timing_or_posting_uncertain",
    "identity_verification_required",
    "frontline_permission_limited",
    "limited_concession_available",
    "core_order_or_service_still_active",
    "coverage_not_available",
    "paid_alternative_available",
    "specialist_review_required",
    "urgent_escalation_required",
    "approval_not_guaranteed",
]

SupportEvidenceFactor = Literal[
    "missing_customer_evidence",
    "frontline_action_available",
    "limited_concession_available",
    "requested_remedy_not_supported",
    "specialist_queue_required",
    "high_time_sensitivity",
]


class SupportTicketReferenceAnswer(BaseModel):
    """Human reference answer for offline support ticket evaluation."""

    issue_type: SupportIssueType
    eligibility_decision: SupportEligibilityDecision
    resolution_category: SupportResolutionCategory
    escalation_needed: bool = Field(default=False)
    key_policy_labels: List[SupportPolicyLabel] = Field(default_factory=list)
    key_policy_points: List[str] = Field(default_factory=list)


class SupportTicket(BaseModel):
    """One mixed-corpus support ticket."""

    ticket_id: str
    customer_message: str
    account_context: str = Field(default="")
    source_pack: str
    reference_answer: Optional[SupportTicketReferenceAnswer] = None


class SupportIntakeState(BaseModel):
    """Structured intake summary extracted from the raw ticket."""

    issue_type: SupportIssueType
    customer_goal: str
    customer_emotion: str
    hard_constraints: List[str] = Field(default_factory=list)
    account_facts: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    tone_guidance: List[str] = Field(default_factory=list)


class SupportPolicyEvidenceState(BaseModel):
    """Structured policy and account evidence extracted from the source pack."""

    evidence_summary: str
    applied_policy_labels: List[SupportPolicyLabel] = Field(default_factory=list)
    evidence_factors: List[SupportEvidenceFactor] = Field(default_factory=list)
    relevant_policy_points: List[str] = Field(default_factory=list)
    account_findings: List[str] = Field(default_factory=list)
    candidate_actions: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)


class SupportEligibilityState(BaseModel):
    """Structured eligibility judgment over the current ticket."""

    eligibility_decision: SupportEligibilityDecision
    decision_rationale: str
    blocking_factors: List[str] = Field(default_factory=list)
    required_checks: List[str] = Field(default_factory=list)
    approved_actions: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0)


class SupportResolutionPlanState(BaseModel):
    """Structured internal resolution plan for the ticket."""

    resolution_category: SupportResolutionCategory
    internal_actions: List[str] = Field(default_factory=list)
    customer_steps: List[str] = Field(default_factory=list)
    escalation_needed: bool = Field(default=False)
    escalation_reason: str = Field(default="")
    commitments: List[str] = Field(default_factory=list)


class SupportResponseDraftState(BaseModel):
    """Customer-facing draft assembled from prior structured states."""

    response_subject: str
    response_message: str
    follow_up_window: str = Field(default="")
    commitments: List[str] = Field(default_factory=list)


class SupportCompressedMemory(BaseModel):
    """Structured working memory carried by compressed support history."""

    issue_type: SupportIssueType = Field(default="unknown")
    customer_goal: str = Field(default="")
    hard_constraints: List[str] = Field(default_factory=list)
    applied_policy_labels: List[SupportPolicyLabel] = Field(default_factory=list)
    evidence_factors: List[SupportEvidenceFactor] = Field(default_factory=list)
    relevant_policy_points: List[str] = Field(default_factory=list)
    candidate_actions: List[str] = Field(default_factory=list)
    eligibility_decision: SupportEligibilityDecision = Field(default="needs_info")
    resolution_category: SupportResolutionCategory = Field(default="clarification_request")
    escalation_needed: bool = Field(default=False)
    notes_for_next_stage: List[str] = Field(default_factory=list)


BugRootCauseCategory = Literal[
    "configuration",
    "state_management",
    "error_recovery",
    "artifact_lifecycle",
    "api_contract_mismatch",
    "invalid_input_handling",
    "test_gap",
    "unknown",
]

BugEvidenceLabel = Literal[
    "recovery_logic_gap",
    "lifecycle_scope_error",
    "preflight_guard_missing",
    "heuristic_underestimate",
    "stale_contract_assumption",
    "workflow_branch_mismatch",
    "stage_boundary_misalignment",
]


class BugTriageIssue(BaseModel):
    """One local bug-triage issue specification."""

    issue_id: str
    title: str
    description: str
    expected_behavior: str = Field(default="")
    log_excerpt: str = Field(default="")
    stacktrace_excerpt: str = Field(default="")
    repo_subpaths: List[str] = Field(default_factory=list)
    gold_primary_file: str = Field(default="")
    gold_root_cause_category: BugRootCauseCategory = Field(default="unknown")
    gold_evidence_labels: List[BugEvidenceLabel] = Field(default_factory=list)


class BugSignalExtractionState(BaseModel):
    """Structured bug signals extracted from the raw issue text."""

    issue_title: str
    bug_summary: str
    observed_behavior: str = Field(default="")
    expected_behavior: str = Field(default="")
    error_messages: List[str] = Field(default_factory=list)
    exception_types: List[str] = Field(default_factory=list)
    file_hints: List[str] = Field(default_factory=list)
    path_hints: List[str] = Field(default_factory=list)
    symbol_hints: List[str] = Field(default_factory=list)
    error_codes: List[str] = Field(default_factory=list)
    trigger_conditions: List[str] = Field(default_factory=list)
    suspected_modules: List[str] = Field(default_factory=list)
    related_concepts: List[str] = Field(default_factory=list)
    synonym_terms: List[str] = Field(default_factory=list)
    acceptance_checks: List[str] = Field(default_factory=list)


class BugQueryExpansionState(BaseModel):
    """Structured retrieval query plan built from extracted bug signals."""

    retrieval_objective: str = Field(default="")
    exact_queries: List[str] = Field(default_factory=list)
    semantic_queries: List[str] = Field(default_factory=list)
    symbol_queries: List[str] = Field(default_factory=list)
    graph_queries: List[str] = Field(default_factory=list)
    config_queries: List[str] = Field(default_factory=list)


class BugEvidenceCandidate(BaseModel):
    """One evidence-backed file candidate for downstream bug reasoning."""

    evidence_id: str
    file_path: str
    relevance_reason: str
    suspicious_signals: List[str] = Field(default_factory=list)


class BugEvidenceState(BaseModel):
    """Structured evidence screen over retrieved repo context."""

    evidence_summary: str
    applied_evidence_labels: List[BugEvidenceLabel] = Field(default_factory=list)
    candidate_files: List[BugEvidenceCandidate] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    reretrieval_needed: bool = Field(default=False)
    additional_queries: List[str] = Field(default_factory=list)


class BugHypothesisCard(BaseModel):
    """One root-cause hypothesis built from structured evidence."""

    hypothesis_label: str
    primary_evidence_id: str = Field(default="")
    primary_file: str
    root_cause_category: BugRootCauseCategory
    explanation: str
    supporting_evidence: List[str] = Field(default_factory=list)


class BugHypothesisState(BaseModel):
    """Structured shortlist of possible root causes."""

    shortlist_rationale: str
    hypotheses: List[BugHypothesisCard] = Field(default_factory=list)


class TriageDecisionState(BaseModel):
    """Final bug-triage decision."""

    primary_evidence_id: str = Field(default="")
    primary_file: str
    root_cause_category: BugRootCauseCategory
    root_cause: str
    proposed_fix: str
    rationale: str
    supporting_evidence: List[str] = Field(default_factory=list)
    alternatives: List[str] = Field(default_factory=list)
    validation_steps: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0)


class BugFixPlanState(BaseModel):
    """Structured repair plan after the main triage decision."""

    patch_scope: List[str] = Field(default_factory=list)
    implementation_steps: List[str] = Field(default_factory=list)
    validation_steps: List[str] = Field(default_factory=list)
    regression_risks: List[str] = Field(default_factory=list)
    rollout_notes: List[str] = Field(default_factory=list)


class TriageReviewState(BaseModel):
    """Review over the current triage decision."""

    decision_ok: bool = Field(default=True)
    review_notes: List[str] = Field(default_factory=list)
    revised_primary_evidence_id: str = Field(default="")
    revised_primary_file: str = Field(default="")
    revised_fix: str = Field(default="")
    missing_evidence: List[str] = Field(default_factory=list)
    residual_risks: List[str] = Field(default_factory=list)


class BugTriageCompressedHypothesis(BaseModel):
    """Compact memory slot for one bug-triage hypothesis."""

    primary_evidence_id: str = Field(default="")
    primary_file: str = Field(default="")
    root_cause_category: BugRootCauseCategory = Field(default="unknown")
    explanation: str = Field(default="")
    supporting_evidence: List[str] = Field(default_factory=list)


class BugTriageCompressedMemory(BaseModel):
    """Structured working memory carried by compressed bug-triage history."""

    bug_summary: str = Field(default="")
    observed_behavior: str = Field(default="")
    expected_behavior: str = Field(default="")
    retrieval_objective: str = Field(default="")
    applied_evidence_labels: List[BugEvidenceLabel] = Field(default_factory=list)
    candidate_files: List[str] = Field(default_factory=list)
    hypotheses: List[BugTriageCompressedHypothesis] = Field(default_factory=list)
    chosen_direction: str = Field(default="")
    validation_focus: List[str] = Field(default_factory=list)
    open_risks: List[str] = Field(default_factory=list)


@dataclass
class StageLog:
    """Log information for one model call."""

    stage_name: str
    prompt_chars: int
    prompt_tokens: int
    output_chars: int
    output_tokens: int
    latency_s: float
    parsed_ok: bool
    raw_text: str


@dataclass
class MethodStats:
    """Aggregated resource usage for one transfer strategy."""

    prompt_chars: int = 0
    prompt_tokens: int = 0
    output_chars: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0

    def add_log(self, log: StageLog) -> None:
        self.prompt_chars += log.prompt_chars
        self.prompt_tokens += log.prompt_tokens
        self.output_chars += log.output_chars
        self.output_tokens += log.output_tokens
        self.latency_s += log.latency_s


@dataclass
class StoredStateArtifact:
    """Externalized artifact stored for later stage reads."""

    artifact_id: str
    method_name: str
    stage_name: str
    artifact_type: str
    label: str
    payload_format: str
    payload: Any
    views: Dict[str, str] = field(default_factory=dict)


@dataclass
class StateDependencySpec:
    """A declared dependency on prior state for one workflow stage."""

    artifact_type: str
    view_name: str
    required: bool = False
    max_items: Optional[int] = None
    relevance_weight: float = 1.0
    description: str = ""


@dataclass
class StageDependencyContract:
    """Skill-like contract describing what prior state a stage can read."""

    stage_name: str
    required_dependencies: List[StateDependencySpec] = field(default_factory=list)
    optional_dependencies: List[StateDependencySpec] = field(default_factory=list)
    estimated_base_seconds: float = 0.0
    future_reserve_seconds: float = 0.0
    skippable: bool = False


@dataclass
class StateReadRecord:
    """One candidate or selected state read during context assembly."""

    artifact_id: str
    artifact_type: str
    artifact_label: str
    view_name: str
    required: bool
    selected: bool
    estimated_tokens: int
    estimated_latency_s: float
    relevance_score: float = 0.0
    content_preview: str = ""


@dataclass
class StageRoutingTrace:
    """Trace information for one routed stage execution."""

    stage_name: str
    skipped: bool = False
    remaining_slo_before_s: float = 0.0
    remaining_slo_after_s: float = 0.0
    required_context_tokens: int = 0
    optional_context_tokens: int = 0
    selected_context_tokens: int = 0
    estimated_context_read_time_s: float = 0.0
    actual_stage_wall_time_s: float = 0.0
    read_records: List[StateReadRecord] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class SLOPolicyConfig:
    """SLO and routing-policy configuration for one run."""

    total_slo_seconds: float
    retrieval_elapsed_seconds: float = 0.0
    seconds_per_token: float = 0.0005
    view_read_overhead_seconds: float = 0.01
    stage_safety_margin_seconds: float = 0.2
    optional_selection_score_threshold: float = 0.0


@dataclass
class StateTransferMethodArtifacts:
    """Artifacts, traces, and metrics for one transfer strategy."""

    method_name: str = ""
    intake_state: Optional[Dict[str, Any]] = None
    signal_state: Optional[Dict[str, Any]] = None
    query_state: Optional[Dict[str, Any]] = None
    evidence_state: Optional[Dict[str, Any]] = None
    candidate_state: Optional[Dict[str, Any]] = None
    eligibility_state: Optional[Dict[str, Any]] = None
    hypothesis_state: Optional[Dict[str, Any]] = None
    decision_state: Optional[Dict[str, Any]] = None
    plan_state: Optional[Dict[str, Any]] = None
    response_state: Optional[Dict[str, Any]] = None
    review_state: Optional[Dict[str, Any]] = None
    stats: MethodStats = field(default_factory=MethodStats)
    logs: List[Dict[str, Any]] = field(default_factory=list)
    state_artifacts: List[StoredStateArtifact] = field(default_factory=list)
    stage_traces: List[StageRoutingTrace] = field(default_factory=list)
    total_wall_time_s: float = 0.0
    met_slo: bool = False
    completion_proxy: float = 0.0


@dataclass
class SupportTicketQualityMetrics:
    """Automatic quality metrics for one support method on one ticket."""

    reference_available: bool = False
    final_issue_type: str = ""
    final_eligibility_decision: str = ""
    final_resolution_category: str = ""
    final_escalation_needed: Optional[bool] = None
    exact_match: Optional[bool] = None
    issue_type_match: Optional[bool] = None
    eligibility_match: Optional[bool] = None
    resolution_match: Optional[bool] = None
    escalation_match: Optional[bool] = None
    policy_label_coverage: Optional[float] = None
    response_commitment_coverage: Optional[float] = None


@dataclass
class SupportTicketResult:
    """State-transfer experiment result for one support ticket."""

    ticket_id: str
    customer_message: str
    source_pack_chars: int
    source_pack_token_estimate: int
    policy_config: SLOPolicyConfig
    structured_fixed_deps: StateTransferMethodArtifacts = field(default_factory=StateTransferMethodArtifacts)
    full_history_carry: StateTransferMethodArtifacts = field(default_factory=StateTransferMethodArtifacts)
    compressed_history_carry: StateTransferMethodArtifacts = field(default_factory=StateTransferMethodArtifacts)
    structured_fixed_deps_quality: SupportTicketQualityMetrics = field(default_factory=SupportTicketQualityMetrics)
    full_history_carry_quality: SupportTicketQualityMetrics = field(default_factory=SupportTicketQualityMetrics)
    compressed_history_carry_quality: SupportTicketQualityMetrics = field(default_factory=SupportTicketQualityMetrics)
    structured_fixed_deps_vs_full_history_metrics_delta: Dict[str, Optional[float]] = field(default_factory=dict)
    compressed_history_carry_vs_full_history_metrics_delta: Dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class BugTriageQualityMetrics:
    """Automatic quality metrics for one bug-triage method on one issue."""

    reference_available: bool = False
    final_primary_file: str = ""
    final_primary_evidence_id: str = ""
    final_root_cause_category: str = ""
    gold_primary_file_in_candidates: Optional[bool] = None
    exact_match: Optional[bool] = None
    primary_file_match: Optional[bool] = None
    primary_file_match_when_in_candidates: Optional[bool] = None
    root_cause_category_match: Optional[bool] = None
    evidence_label_coverage: Optional[float] = None


@dataclass
class BugTriageIssueResult:
    """State-transfer experiment result for one local bug-triage issue."""

    issue_id: str
    title: str
    repo_root: str
    retrieved_hits_count: int
    retrieved_files: List[str]
    policy_config: SLOPolicyConfig
    gold_primary_file: str = ""
    gold_root_cause_category: str = ""
    gold_evidence_labels: List[str] = field(default_factory=list)
    structured_fixed_deps: StateTransferMethodArtifacts = field(default_factory=StateTransferMethodArtifacts)
    full_history_carry: StateTransferMethodArtifacts = field(default_factory=StateTransferMethodArtifacts)
    compressed_history_carry: StateTransferMethodArtifacts = field(default_factory=StateTransferMethodArtifacts)
    structured_fixed_deps_quality: BugTriageQualityMetrics = field(default_factory=BugTriageQualityMetrics)
    full_history_carry_quality: BugTriageQualityMetrics = field(default_factory=BugTriageQualityMetrics)
    compressed_history_carry_quality: BugTriageQualityMetrics = field(default_factory=BugTriageQualityMetrics)
    structured_fixed_deps_vs_full_history_metrics_delta: Dict[str, Optional[float]] = field(default_factory=dict)
    compressed_history_carry_vs_full_history_metrics_delta: Dict[str, Optional[float]] = field(default_factory=dict)
