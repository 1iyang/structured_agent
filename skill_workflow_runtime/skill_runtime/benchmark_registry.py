from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ValidationLayer:
    key: str
    name: str
    purpose: str


@dataclass(frozen=True)
class BenchmarkTarget:
    key: str
    name: str
    domain: str
    source_type: str
    validation_layer: str
    role: str
    status: str
    datasets: List[str] = field(default_factory=list)
    primary_questions: List[str] = field(default_factory=list)
    required_adaptations: List[str] = field(default_factory=list)
    recommended_metrics: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkValidationPlan:
    thesis: str
    layers: List[ValidationLayer]
    targets: List[BenchmarkTarget]
    design_adjustment: str
    execution_order: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thesis": self.thesis,
            "layers": [asdict(layer) for layer in self.layers],
            "targets": [asdict(target) for target in self.targets],
            "design_adjustment": self.design_adjustment,
            "execution_order": list(self.execution_order),
        }


def build_benchmark_validation_plan() -> BenchmarkValidationPlan:
    layers = [
        ValidationLayer(
            key="mechanism_validation",
            name="Main Mechanism Validation",
            purpose=(
                "Use the current support and bug workflows to test state-sufficient boundaries, "
                "minimal-transfer execution, suffix budget policy, and tool-aware suffix loops."
            ),
        ),
        ValidationLayer(
            key="localization_validation",
            name="Localization Validation",
            purpose=(
                "Separate bug-side retrieval and primary-file localization from the rest of the "
                "end-to-end repository workflow."
            ),
        ),
        ValidationLayer(
            key="external_transfer_validation",
            name="External Transfer Validation",
            purpose=(
                "Check whether the current skill runtime and minimal-transfer claims transfer to "
                "public benchmarks outside the self-built support and bug tasks."
            ),
        ),
    ]

    targets = [
        BenchmarkTarget(
            key="internal_support_workflow",
            name="Support Workflow (internal realistic set)",
            domain="support",
            source_type="internal",
            validation_layer="mechanism_validation",
            role=(
                "Primary mechanism benchmark for support-side state-sufficient boundaries and "
                "budget-aware suffix execution."
            ),
            status="active",
            datasets=["examples/support_tickets_realistic.json", "examples/support_tickets_realistic_labeled.json"],
            primary_questions=[
                "Does structured state reduce repeated suffix evidence transfer?",
                "Can a budget-aware strategy keep optional suffix quality where full/raw baselines cannot?",
            ],
            required_adaptations=[
                "Keep the current three-way minimal/full/raw comparison.",
                "Preserve support-specific evidence and commitment metrics.",
            ],
            recommended_metrics=[
                "suffix_prompt_tokens",
                "input_view_tokens_total",
                "eligibility_match_rate",
                "resolution_match_rate",
                "policy_label_coverage",
                "response_commitment_coverage",
            ],
        ),
        BenchmarkTarget(
            key="internal_bug_workflow",
            name="Bug Triage Workflow (internal realistic set)",
            domain="bug",
            source_type="internal",
            validation_layer="mechanism_validation",
            role=(
                "Primary mechanism benchmark for repository-aware retrieval, evidence compression, "
                "and tool-aware suffix execution."
            ),
            status="active",
            datasets=["examples/bug_triage_issues_realistic.json"],
            primary_questions=[
                "Do retrieval and evidence compression expose a reusable bug-state boundary?",
                "Can suffix reasoning stay mostly on structured state once retrieval has been compressed?",
            ],
            required_adaptations=[
                "Keep retrieval recall metrics separate from downstream decision metrics.",
                "Preserve the explicit suffix re-retrieval demo as a mechanism-level check.",
            ],
            recommended_metrics=[
                "gold_primary_file_candidate_recall_rate",
                "primary_file_match_rate",
                "primary_file_match_rate_when_in_candidates",
                "root_cause_category_match_rate",
                "evidence_label_coverage",
            ],
        ),
        BenchmarkTarget(
            key="tau_knowledge",
            name="tau-Knowledge",
            domain="support",
            source_type="external",
            validation_layer="external_transfer_validation",
            role=(
                "Support-side external validation for document-heavy workflows where the prefix "
                "must absorb large knowledge bundles before the suffix can proceed on compact state."
            ),
            status="planned",
            datasets=["tau-Knowledge"],
            primary_questions=[
                "Does a document-heavy public workflow still expose anchor-to-suffix state boundaries?",
                "Does the skill contract materially change later-stage information flow on a public task?",
            ],
            required_adaptations=[
                "Map public task fields into the canonical support-style task contract.",
                "Preserve the distinction between anchor evidence consumption and suffix state reuse.",
            ],
            recommended_metrics=[
                "suffix_prompt_tokens",
                "input_view_tokens_total",
                "task_success",
                "tool_call_count",
            ],
        ),
        BenchmarkTarget(
            key="mulocbench",
            name="MULocBench",
            domain="bug",
            source_type="external",
            validation_layer="localization_validation",
            role=(
                "Bug-side localization benchmark for retrieval recall, candidate generation quality, "
                "and primary-file localization."
            ),
            status="adapter_ready",
            datasets=["MULocBench"],
            primary_questions=[
                "Can the retrieval prefix recover the gold file into the candidate pool reliably?",
                "Once the gold file is present, does structured suffix reasoning localize it correctly?",
            ],
            required_adaptations=[
                "Treat localization as a standalone validation layer rather than as a full end-to-end fix benchmark.",
                "Expose public benchmark file labels through the current primary-file evaluation path.",
            ],
            recommended_metrics=[
                "candidate_recall_rate",
                "primary_file_match_rate",
                "primary_file_match_rate_when_in_candidates",
                "retrieved_file_hit_rate",
            ],
        ),
        BenchmarkTarget(
            key="swe_bench",
            name="SWE-bench",
            domain="bug",
            source_type="external",
            validation_layer="external_transfer_validation",
            role=(
                "Repository-level external validation for end-to-end issue understanding, retrieval, "
                "decision, and handoff on public software engineering tasks."
            ),
            status="planned",
            datasets=["SWE-bench"],
            primary_questions=[
                "Do minimal-transfer claims survive on public end-to-end repo workflows?",
                "Is structured state useful beyond isolated localization when the workflow includes full issue resolution?",
            ],
            required_adaptations=[
                "Keep localization metrics separate from end-to-end workflow success metrics.",
                "Use the current bug workflow as the core execution skeleton before adding benchmark-specific branches.",
            ],
            recommended_metrics=[
                "prompt_tokens_total",
                "input_view_tokens_total",
                "task_success",
                "retrieval_hit_rate",
            ],
        ),
        BenchmarkTarget(
            key="multi_swe_bench",
            name="Multi-SWE-bench",
            domain="bug",
            source_type="external",
            validation_layer="external_transfer_validation",
            role=(
                "Cross-language external validation for the same end-to-end repository workflow claim "
                "made on SWE-bench."
            ),
            status="planned",
            datasets=["Multi-SWE-bench"],
            primary_questions=[
                "Do the current skill-defined workflow runtime and minimal-transfer claims survive across repository languages?",
                "Does the runtime stay useful when the repo workflow is no longer Python-only?",
            ],
            required_adaptations=[
                "Generalize bug retrieval adapters beyond Python-centric file assumptions.",
                "Keep the skill contract focused on workflow semantics rather than language-specific heuristics.",
            ],
            recommended_metrics=[
                "prompt_tokens_total",
                "input_view_tokens_total",
                "task_success",
                "cross_repo_transfer_stability",
            ],
        ),
    ]

    return BenchmarkValidationPlan(
        thesis=(
            "The benchmark expansion should strengthen, not replace, the current claim: skill-defined "
            "workflow contracts are valuable when they expose state-sufficient boundaries that reduce "
            "repeated evidence transfer and enable explicit budget-aware execution choices."
        ),
        layers=layers,
        targets=targets,
        design_adjustment=(
            "Do not replace the current support and bug workflows. Keep them as the main mechanism "
            "validation layer, then add public benchmarks as localization-only or external-transfer "
            "validation layers."
        ),
        execution_order=[
            "internal_support_workflow",
            "internal_bug_workflow",
            "mulocbench",
            "tau_knowledge",
            "swe_bench",
            "multi_swe_bench",
        ],
    )


def render_benchmark_validation_markdown(plan: BenchmarkValidationPlan) -> str:
    lines: List[str] = []
    lines.append("# Benchmark Validation Plan")
    lines.append("")
    lines.append(plan.thesis)
    lines.append("")
    lines.append(f"Design adjustment: {plan.design_adjustment}")
    lines.append("")
    lines.append("## Validation Layers")
    lines.append("")
    for layer in plan.layers:
        lines.append(f"### {layer.name}")
        lines.append("")
        lines.append(f"- key: `{layer.key}`")
        lines.append(f"- purpose: {layer.purpose}")
        lines.append("")
    lines.append("## Targets")
    lines.append("")
    targets_by_layer = {layer.key: [] for layer in plan.layers}
    for target in plan.targets:
        targets_by_layer.setdefault(target.validation_layer, []).append(target)
    for layer in plan.layers:
        lines.append(f"### {layer.name}")
        lines.append("")
        for target in targets_by_layer.get(layer.key, []):
            lines.append(f"#### {target.name}")
            lines.append("")
            lines.append(f"- key: `{target.key}`")
            lines.append(f"- domain: `{target.domain}`")
            lines.append(f"- source: `{target.source_type}`")
            lines.append(f"- status: `{target.status}`")
            lines.append(f"- role: {target.role}")
            lines.append(f"- datasets: {', '.join(target.datasets)}")
            lines.append("- primary questions:")
            for question in target.primary_questions:
                lines.append(f"  - {question}")
            lines.append("- required adaptations:")
            for adaptation in target.required_adaptations:
                lines.append(f"  - {adaptation}")
            lines.append("- recommended metrics:")
            for metric in target.recommended_metrics:
                lines.append(f"  - `{metric}`")
            lines.append("")
    lines.append("## Recommended Execution Order")
    lines.append("")
    for index, key in enumerate(plan.execution_order, start=1):
        lines.append(f"{index}. `{key}`")
    lines.append("")
    return "\n".join(lines)
