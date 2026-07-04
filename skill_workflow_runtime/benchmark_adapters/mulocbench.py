from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


MULOCBENCH_PRIMARY_TYPE_ORDER = ["code", "test", "config", "doc", "asset"]


def _dedupe_preserve(items: Iterable[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def load_mulocbench_records(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text()
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    parsed = json.loads(text)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        if isinstance(parsed.get("data"), list):
            return parsed["data"]
        if isinstance(parsed.get("records"), list):
            return parsed["records"]
        for split_name in ["train", "validation", "test"]:
            if isinstance(parsed.get(split_name), list):
                return parsed[split_name]
    raise ValueError(
        f"Could not find a record list in {path}. Expected a JSON list, JSONL, or a dict with data/records/train/validation/test."
    )


def _extract_issue_text(record: Dict[str, Any]) -> str:
    return str(
        record.get("body")
        or record.get("description")
        or record.get("issue_body")
        or record.get("problem_description")
        or ""
    )


def _extract_issue_title(record: Dict[str, Any]) -> str:
    return str(record.get("title") or record.get("issue_title") or record.get("summary") or "").strip()


def _extract_expected_behavior(record: Dict[str, Any]) -> str:
    explicit = str(
        record.get("expected_behavior")
        or record.get("expected")
        or record.get("desired_behavior")
        or ""
    ).strip()
    if explicit:
        return explicit
    title = _extract_issue_title(record)
    if title:
        return f"Localize the repository files most relevant to resolving: {title}"
    return "Localize the repository files relevant to the issue."


def _extract_log_and_stacktrace(record: Dict[str, Any]) -> tuple[str, str]:
    text = _extract_issue_text(record)
    if not text.strip():
        return "No log excerpt provided.", "No stacktrace provided."

    log_lines: List[str] = []
    stack_lines: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if not line:
            continue
        if any(token in lower for token in ["error", "exception", "failed", "failure", "warning"]):
            log_lines.append(line)
        if (
            line.startswith("File ")
            or "traceback" in lower
            or "stack trace" in lower
            or "stacktrace" in lower
            or ("/" in line and ":" in line and any(suffix in lower for suffix in [".py", ".ts", ".js", ".java", ".go", ".rb"]))
        ):
            stack_lines.append(line)

    log_excerpt = "\n".join(log_lines[:8]).strip() or "No log excerpt provided."
    stacktrace_excerpt = "\n".join(stack_lines[:12]).strip() or "No stacktrace provided."
    return log_excerpt, stacktrace_excerpt


def extract_mulocbench_gold_files(record: Dict[str, Any]) -> Dict[str, List[str]]:
    loctype = record.get("loctype")
    buckets: Dict[str, List[str]] = {bucket: [] for bucket in MULOCBENCH_PRIMARY_TYPE_ORDER}
    if isinstance(loctype, dict):
        for bucket in MULOCBENCH_PRIMARY_TYPE_ORDER:
            raw_values = loctype.get(bucket, [])
            if isinstance(raw_values, list):
                buckets[bucket] = _dedupe_preserve(str(value) for value in raw_values)

    file_loc = record.get("file_loc")
    fallback_files: List[str] = []
    if isinstance(file_loc, dict):
        raw_files = file_loc.get("files", [])
        if isinstance(raw_files, list):
            for item in raw_files:
                if isinstance(item, dict):
                    fallback_files.append(str(item.get("file") or item.get("path") or "").strip())
                else:
                    fallback_files.append(str(item).strip())

    deduped_fallback = _dedupe_preserve(fallback_files)
    if not any(buckets.values()) and deduped_fallback:
        buckets["code"] = deduped_fallback
    return buckets


def _choose_primary_gold_file(gold_files_by_type: Dict[str, Sequence[str]]) -> tuple[str, str]:
    for bucket in MULOCBENCH_PRIMARY_TYPE_ORDER:
        candidates = [str(item).strip() for item in gold_files_by_type.get(bucket, []) if str(item).strip()]
        if candidates:
            return candidates[0], bucket
    return "", "unknown"


def _repo_subpath_for_record(
    record: Dict[str, Any],
    *,
    repos_root: str | None = None,
    repo_layout: str = "nested",
) -> List[str]:
    organization = str(record.get("organization") or "").strip()
    repo_name = str(record.get("repo_name") or record.get("repository") or "").strip()
    if not repo_name:
        return []

    if repos_root:
        root = Path(repos_root)
    else:
        root = Path("external_repos")

    if repo_layout == "nested":
        target = root / organization / repo_name if organization else root / repo_name
    elif repo_layout == "flat":
        target = root / (f"{organization}__{repo_name}" if organization else repo_name)
    elif repo_layout == "repo_name":
        target = root / repo_name
    else:
        raise ValueError(f"Unsupported repo_layout: {repo_layout}")
    return [str(target)]


def _build_task_id(record: Dict[str, Any]) -> str:
    explicit = str(record.get("task_id") or record.get("id") or "").strip()
    if explicit:
        return explicit
    organization = str(record.get("organization") or "").strip()
    repo_name = str(record.get("repo_name") or "").strip()
    issue_number = str(record.get("issue_number") or record.get("number") or record.get("issue_id") or "").strip()
    parts = [part for part in [organization, repo_name, issue_number] if part]
    if parts:
        return "mulocbench-" + "-".join(parts)
    url = str(record.get("iss_html_url") or "").strip()
    if url:
        return "mulocbench-" + url.rstrip("/").rsplit("/", 1)[-1]
    return "mulocbench-task"


def adapt_mulocbench_record(
    record: Dict[str, Any],
    *,
    repos_root: str | None = None,
    repo_layout: str = "nested",
) -> Dict[str, Any]:
    gold_files_by_type = extract_mulocbench_gold_files(record)
    gold_primary_file, gold_primary_file_type = _choose_primary_gold_file(gold_files_by_type)
    gold_localized_files = _dedupe_preserve(
        path
        for bucket in MULOCBENCH_PRIMARY_TYPE_ORDER
        for path in gold_files_by_type.get(bucket, [])
    )
    log_excerpt, stacktrace_excerpt = _extract_log_and_stacktrace(record)
    issue_text = _extract_issue_text(record)

    return {
        "task_id": _build_task_id(record),
        "source_dataset": "MULocBench",
        "organization": str(record.get("organization") or "").strip(),
        "repo_name": str(record.get("repo_name") or record.get("repository") or "").strip(),
        "base_commit": str(record.get("base_commit") or "").strip(),
        "issue_url": str(record.get("iss_html_url") or "").strip(),
        "issue_label": str(record.get("iss_label") or "").strip(),
        "issue_has_pr": bool(record.get("iss_has_pr", False)),
        "title": _extract_issue_title(record),
        "description": issue_text,
        "expected_behavior": _extract_expected_behavior(record),
        "log_excerpt": log_excerpt,
        "stacktrace_excerpt": stacktrace_excerpt,
        "repo_subpaths": _repo_subpath_for_record(record, repos_root=repos_root, repo_layout=repo_layout),
        "repository_artifacts": [],
        "gold_primary_file": gold_primary_file,
        "gold_primary_file_type": gold_primary_file_type,
        "gold_localized_files": gold_localized_files,
        "gold_localized_files_by_type": {
            bucket: list(gold_files_by_type.get(bucket, []))
            for bucket in MULOCBENCH_PRIMARY_TYPE_ORDER
            if gold_files_by_type.get(bucket, [])
        },
    }


def adapt_mulocbench_records(
    records: Sequence[Dict[str, Any]],
    *,
    repos_root: str | None = None,
    repo_layout: str = "nested",
) -> List[Dict[str, Any]]:
    return [
        adapt_mulocbench_record(record, repos_root=repos_root, repo_layout=repo_layout)
        for record in records
    ]
