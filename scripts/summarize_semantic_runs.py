#!/usr/bin/env python3
"""Summarize already-generated semantic-live run artifacts without API calls."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_ROOT = ROOT / "runs/round2-semantic"
DEFAULT_OUTPUT = DEFAULT_RUNS_ROOT / "semantic-live-smoke-summary.json"
LOCKED_FIELDS = (
    "sample_id",
    "source_file",
    "defects",
    "detailed_conclusion",
    "causes",
    "treatments",
    "safety_impact",
)
ARTIFACTS = (
    "baseline.json",
    "enhanced_prediction.json",
    "candidates.json",
    "enhanced_prediction.trace.json",
    "enhanced_prediction.decisions.jsonl",
    "enhanced_prediction.retrieval.json",
)
_TIMEOUT_RE = re.compile(r"tim(?:e|ed)[ -]?out|timeout", re.IGNORECASE)


def _read_json(path: Path, errors: list[str]) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: {exc}")
        return None


def _read_decisions(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"{path.name}: {exc}")
        return records
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number}: {exc}")
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            errors.append(f"{path.name}:{line_number}: entry is not an object")
    return records


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if str(item).strip()})


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _model_and_tokens(calls: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    models = Counter(str(call.get("model") or "unknown") for call in calls)
    input_tokens = sum(_number(call.get("input_tokens", call.get("prompt_tokens"))) for call in calls)
    output_tokens = sum(_number(call.get("output_tokens", call.get("completion_tokens"))) for call in calls)
    total_tokens = sum(
        _number(call.get("total_tokens"))
        if call.get("total_tokens") is not None
        else _number(call.get("input_tokens", call.get("prompt_tokens")))
        + _number(call.get("output_tokens", call.get("completion_tokens")))
        for call in calls
    )
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "token_usage_known": bool(calls) and all(call.get("total_tokens") is not None for call in calls),
    }
    return usage, sorted(models), dict(sorted(models.items()))


def _category_changes(baseline: Any, enhanced: Any) -> tuple[bool, list[dict[str, Any]]]:
    if not isinstance(baseline, dict) or not isinstance(enhanced, dict):
        return False, []
    baseline_recommendations = baseline.get("recommendations")
    enhanced_recommendations = enhanced.get("recommendations")
    if not isinstance(baseline_recommendations, list) or not isinstance(enhanced_recommendations, list):
        return False, []
    changes: list[dict[str, Any]] = []
    for index in range(max(len(baseline_recommendations), len(enhanced_recommendations))):
        before = (
            baseline_recommendations[index].get("category")
            if index < len(baseline_recommendations) and isinstance(baseline_recommendations[index], dict)
            else None
        )
        after = (
            enhanced_recommendations[index].get("category")
            if index < len(enhanced_recommendations) and isinstance(enhanced_recommendations[index], dict)
            else None
        )
        if before != after:
            changes.append({"index": index, "baseline": before, "enhanced": after})
    return True, changes


def summarize_run_directory(run_dir: Path) -> dict[str, Any]:
    """Summarize one run directory; missing optional artifacts are reported."""

    artifact_errors: list[str] = []
    baseline = _read_json(run_dir / "baseline.json", artifact_errors)
    enhanced = _read_json(run_dir / "enhanced_prediction.json", artifact_errors)
    candidates = _read_json(run_dir / "candidates.json", artifact_errors)
    trace = _read_json(run_dir / "enhanced_prediction.trace.json", artifact_errors)
    retrieval = _read_json(run_dir / "enhanced_prediction.retrieval.json", artifact_errors)
    decisions = _read_decisions(run_dir / "enhanced_prediction.decisions.jsonl", artifact_errors)

    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    if candidates is not None and not isinstance(candidates, list):
        artifact_errors.append("candidates.json: expected an array")
    trace = trace if isinstance(trace, dict) else {}
    if retrieval is None:
        retrieval = {}
    elif not isinstance(retrieval, dict):
        artifact_errors.append("enhanced_prediction.retrieval.json: expected an object")
        retrieval = {}

    resolved = sum(1 for item in decisions if item.get("decision") == "resolved")
    unresolved = sum(1 for item in decisions if item.get("decision") == "unresolved")
    decision_count = resolved + unresolved
    trace_fallback = "fallback_fields" in trace
    fallback_fields = _strings(trace.get("fallback_fields"))
    if not trace_fallback:
        fallback_fields = sorted(
            {
                str(field)
                for item in decisions
                if item.get("decision") == "unresolved"
                for field in item.get("fallback_fields", []) or []
            }
        )
    validation_errors = trace.get("validation_errors", [])
    if not isinstance(validation_errors, list):
        validation_errors = [str(validation_errors)] if validation_errors else []

    calls = trace.get("model_calls", [])
    calls = [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
    error_details = [
        {
            key: call[key]
            for key in ("candidate_id", "phase", "error_type", "error")
            if key in call
        }
        for call in calls
        if call.get("error")
    ]
    timeout_count = sum(
        1
        for detail in error_details
        if _TIMEOUT_RE.search(" ".join(str(detail.get(key, "")) for key in ("error_type", "error")))
    )
    token_usage, model_names, model_counts = _model_and_tokens(calls)
    category_comparison_available, category_changes = _category_changes(baseline, enhanced)

    if isinstance(baseline, dict) and isinstance(enhanced, dict):
        locked_field_invariance = {
            field: baseline.get(field) == enhanced.get(field) for field in LOCKED_FIELDS
        }
        all_locked_fields_unchanged: bool | None = all(locked_field_invariance.values())
    else:
        locked_field_invariance = {field: None for field in LOCKED_FIELDS}
        all_locked_fields_unchanged = None

    source_kind_counts: Counter[str] = Counter()
    retrieval_entry_count = 0
    for hits in retrieval.values():
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if isinstance(hit, dict):
                source_kind_counts[str(hit.get("kind") or "unknown")] += 1
                retrieval_entry_count += 1

    artifact_status = {name: (run_dir / name).exists() for name in ARTIFACTS}
    if not artifact_status["baseline.json"]:
        status = "missing-baseline"
    elif not artifact_status["enhanced_prediction.json"]:
        status = "incomplete"
    elif unresolved or fallback_fields or error_details:
        status = "partial"
    else:
        status = "resolved"

    return {
        "run": run_dir.name,
        "path": run_dir.as_posix(),
        "status": status,
        "sample_id": baseline.get("sample_id") if isinstance(baseline, dict) else None,
        "source_file": baseline.get("source_file") if isinstance(baseline, dict) else None,
        "artifacts": artifact_status,
        "candidate_count": candidate_count,
        "decision_count": decision_count,
        "resolved": resolved,
        "unresolved": unresolved,
        "missing_decisions": max(candidate_count - decision_count, 0),
        "fallback_fields": fallback_fields,
        "validation_errors": validation_errors,
        "validation_error_count": len(validation_errors),
        "locked_field_invariance": locked_field_invariance,
        "all_locked_fields_unchanged": all_locked_fields_unchanged,
        "category_comparison_available": category_comparison_available,
        "category_changes": category_changes,
        "category_change_count": len(category_changes),
        "source_kind_counts": dict(sorted(source_kind_counts.items())),
        "retrieval_entry_count": retrieval_entry_count,
        "error_count": len(error_details),
        "timeout_count": timeout_count,
        "timeout_or_error_count": len(error_details),
        "errors": error_details,
        "model_names": model_names,
        "model_counts": model_counts,
        "tokens": token_usage,
        "artifact_errors": artifact_errors,
    }


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    source_kind_counts: Counter[str] = Counter()
    fallback_field_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    category_changes: list[dict[str, Any]] = []
    locked_summary: dict[str, dict[str, Any]] = {}
    for field in LOCKED_FIELDS:
        values = [run["locked_field_invariance"][field] for run in runs]
        compared = [value for value in values if isinstance(value, bool)]
        locked_summary[field] = {
            "compared_runs": len(compared),
            "unchanged_runs": sum(compared),
            "changed_runs": sum(value is False for value in compared),
            "all_unchanged": bool(compared) and all(compared),
        }
    for run in runs:
        source_kind_counts.update(run["source_kind_counts"])
        fallback_field_counts.update(run["fallback_fields"])
        model_counts.update(run["model_counts"])
        category_changes.extend({"run": run["run"], **change} for change in run["category_changes"])

    token_keys = ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")
    tokens = {key: sum(run["tokens"][key] for run in runs) for key in token_keys}
    token_usage_known = bool(runs) and all(
        run["tokens"]["token_usage_known"] for run in runs if run["decision_count"] or run["error_count"]
    )
    return {
        "run_count": len(runs),
        "complete_run_count": sum(run["artifacts"]["enhanced_prediction.json"] for run in runs),
        "candidate_count": sum(run["candidate_count"] for run in runs),
        "decision_count": sum(run["decision_count"] for run in runs),
        "resolved": sum(run["resolved"] for run in runs),
        "unresolved": sum(run["unresolved"] for run in runs),
        "missing_decisions": sum(run["missing_decisions"] for run in runs),
        "fallback_fields": sorted(fallback_field_counts),
        "fallback_field_counts": dict(sorted(fallback_field_counts.items())),
        "validation_error_count": sum(run["validation_error_count"] for run in runs),
        "locked_field_invariance": locked_summary,
        "category_changes": category_changes,
        "category_change_count": len(category_changes),
        "source_kind_counts": dict(sorted(source_kind_counts.items())),
        "error_count": sum(run["error_count"] for run in runs),
        "timeout_count": sum(run["timeout_count"] for run in runs),
        "timeout_or_error_count": sum(run["timeout_or_error_count"] for run in runs),
        "model_names": sorted(model_counts),
        "model_counts": dict(sorted(model_counts.items())),
        "tokens": {**tokens, "token_usage_known": token_usage_known},
    }


def build_summary(runs_root: Path = DEFAULT_RUNS_ROOT) -> dict[str, Any]:
    run_dirs = sorted(path for path in runs_root.glob("semantic-live-*") if path.is_dir())
    runs = [summarize_run_directory(path) for path in run_dirs]
    return {
        "schema_version": "semantic-live-smoke-summary-v1",
        "runs_root": runs_root.as_posix(),
        "score_improvement_confirmed": False,
        "runs": runs,
        "aggregate": _aggregate(runs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    summary = build_summary(args.runs_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
