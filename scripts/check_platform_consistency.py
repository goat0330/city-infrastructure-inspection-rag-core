#!/usr/bin/env python3
"""Run the minimum pre-submission consistency checks on prediction JSONL.

This is intentionally a small release gate. It checks contradictions that the
platform can see directly; it does not attempt to reproduce the platform score.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.extraction.output_normalizer import (
    normalize_recommendations_summary,
    resolve_recommendation_category,
)
from src.extraction.summary.extractor import (
    _FILENAME_GRADE_RE,
    _filename_grade,
)

_COMPOSER_MARKERS = (
    "车辆荷载长期作用、温度变化及材料老化共同影响",
    "混凝土保护层破损、施工密实性不足及长期环境侵蚀",
    "防排水不畅、接缝密封老化或雨水长期下渗",
    "可能削弱结构整体性",
    "影响传力状态",
    "若不及时处理，会影响使用功能并降低构件耐久性",
)


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _iter_records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"line {line_number} is not a JSON object")
            yield payload


def _filename_grades(source_file: str) -> tuple[str, str]:
    stem = re.sub(r"\.(?:docx?|DOCX?)$", "", Path(source_file).name)
    previous = ""
    current = ""
    for match in _FILENAME_GRADE_RE.finditer(stem):
        value = _filename_grade(match.group("grade"), match.group("suffix") or "")
        if match.group("label") == "原":
            previous = value
        else:
            current = value
    return previous, current


def inspect_record(record: Mapping[str, Any]) -> dict[str, Any]:
    sample_id = _text(record.get("sample_id") or record.get("source_file"))
    source_file = _text(record.get("source_file"))
    summary = record.get("summary") if isinstance(record.get("summary"), Mapping) else {}
    recommendations = record.get("recommendations") if isinstance(record.get("recommendations"), list) else []

    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    resolved_rows: list[dict[str, Any]] = []
    blank_categories = 0
    for item in recommendations:
        if not isinstance(item, Mapping):
            continue
        category = _text(item.get("category"))
        content = _text(item.get("content"))
        if not category:
            blank_categories += 1
        resolved_rows.append({**item, "category": resolve_recommendation_category(category, content)})
    if blank_categories:
        issues.append({
            "code": "blank_recommendation_category",
            "detail": f"{blank_categories} recommendation rows have no category",
        })

    if resolved_rows:
        expected_summary = normalize_recommendations_summary(resolved_rows)
        actual_summary = _text(summary.get("recommendations_summary"))
        if actual_summary != expected_summary:
            issues.append({
                "code": "recommendation_summary_mismatch",
                "detail": f"summary={actual_summary!r}; details={expected_summary!r}",
            })

    previous_grade, current_grade = _filename_grades(source_file)
    actual_previous = _text(summary.get("previous_overall_grade"))
    actual_current = _text(summary.get("overall_grade"))
    actual_trend = _text(summary.get("trend"))
    if previous_grade and actual_previous != previous_grade:
        issues.append({
            "code": "filename_previous_grade_missing_or_conflicting",
            "detail": f"filename={previous_grade!r}; prediction={actual_previous!r}",
        })
    if current_grade and actual_current != current_grade:
        issues.append({
            "code": "filename_current_grade_conflict",
            "detail": f"filename={current_grade!r}; prediction={actual_current!r}",
        })
    if previous_grade and current_grade and not actual_trend:
        issues.append({
            "code": "trend_missing_with_filename_history",
            "detail": f"filename carries {previous_grade}->{current_grade}",
        })

    joined_text = "\n".join(
        _text(value)
        for field in ("overall_conclusion", "risk_points")
        for value in (summary.get(field),)
    )
    for field in ("detailed_conclusion", "causes", "safety_impact"):
        values = record.get(field)
        if isinstance(values, list):
            joined_text += "\n" + "\n".join(_text(value) for value in values)
    if "无。本次检测" in joined_text:
        issues.append({
            "code": "missing_value_sentence_splice",
            "detail": "contains the submitted composer splice '无。本次检测'",
        })
    marker_hits = sorted({marker for marker in _COMPOSER_MARKERS if marker in joined_text})
    if marker_hits:
        warnings.append({
            "code": "generic_composer_marker",
            "detail": " | ".join(marker_hits),
        })

    return {
        "sample_id": sample_id,
        "source_file": source_file,
        "issues": issues,
        "warnings": warnings,
    }


def build_report(path: Path) -> dict[str, Any]:
    records = [inspect_record(record) for record in _iter_records(path)]
    issue_records = [item for item in records if item["issues"]]
    warning_records = [item for item in records if item["warnings"]]
    counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    for item in records:
        for issue in item["issues"]:
            counts[issue["code"]] = counts.get(issue["code"], 0) + 1
        for warning in item["warnings"]:
            warning_counts[warning["code"]] = warning_counts.get(warning["code"], 0) + 1
    return {
        "input": str(path),
        "record_count": len(records),
        "valid": not issue_records,
        "issue_record_count": len(issue_records),
        "warning_record_count": len(warning_records),
        "issue_counts": counts,
        "warning_counts": warning_counts,
        "records_with_issues": issue_records,
        "records_with_warnings": warning_records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.input)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
