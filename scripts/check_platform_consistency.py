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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    "可能与构件受力、材料收缩或温度变化有关",
    "报告未明确该类病害对安全性、承载能力或耐久性的具体影响",
    "已有证据为",
    "综上，报告建议",
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
    if previous_grade and actual_previous and actual_previous not in {"无", previous_grade}:
        warnings.append({
            "code": "filename_previous_grade_conflict",
            "detail": f"filename={previous_grade!r}; prediction={actual_previous!r}",
        })
    if current_grade and actual_current and actual_current != current_grade:
        warnings.append({
            "code": "filename_current_grade_conflict",
            "detail": f"filename={current_grade!r}; prediction={actual_current!r}",
        })
    # Filename metadata is only a conflict signal.  It must not force a trend
    # sentence or override an explicit report value.

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
        issues.append({
            "code": "generic_composer_marker",
            "detail": " | ".join(marker_hits),
        })

    overall = _text(summary.get("overall_conclusion"))
    risk = _text(summary.get("risk_points"))
    if len(overall) > 250:
        issues.append({
            "code": "overall_conclusion_too_long",
            "detail": f"length={len(overall)}",
        })
    if any(marker in overall for marker in ("建议", "维修", "修复", "处治", "处置")):
        issues.append({
            "code": "overall_conclusion_contains_action",
            "detail": overall[:160],
        })
    if len(risk) > 200:
        issues.append({
            "code": "risk_points_too_long",
            "detail": f"length={len(risk)}",
        })
    if any(marker in risk for marker in (
        "建议", "维修", "修复", "处治", "处置", "可直接用", "环氧砂浆",
    )):
        issues.append({
            "code": "risk_points_contains_action",
            "detail": risk[:160],
        })

    detailed = record.get("detailed_conclusion") if isinstance(record.get("detailed_conclusion"), list) else []
    if any("综上，报告建议" in _text(value) for value in detailed):
        issues.append({
            "code": "detailed_conclusion_contains_recommendation",
            "detail": "detailed conclusion repeats recommendation content",
        })
    if any("无往年检测评分" in _text(value) for value in detailed):
        issues.append({
            "code": "unsupported_no_history_statement",
            "detail": "missing extraction was converted into a factual no-history claim",
        })

    safety_values = record.get("safety_impact") if isinstance(record.get("safety_impact"), list) else []
    safety_text = "\n".join(_text(value) for value in safety_values)
    if "已有证据为" in safety_text or "报告未明确" in safety_text:
        issues.append({
            "code": "safety_meta_text",
            "detail": "safety field contains extractor commentary instead of report conclusions",
        })
    reassuring = any(marker in safety_text for marker in ("不影响", "未影响", "满足要求", "符合要求"))
    adverse_text = re.sub(
        r"(?:暂不|不|未)\s*影响(?:结构)?(?:安全|承载能力|承载)",
        "",
        safety_text,
    )
    adverse = any(marker in adverse_text for marker in ("影响安全", "影响承载", "承载能力不足", "不满足要求"))
    if reassuring and adverse:
        issues.append({
            "code": "safety_conclusion_conflict",
            "detail": "contains both reassuring and adverse final conclusions",
        })

    return {
        "sample_id": sample_id,
        "source_file": source_file,
        "issues": issues,
        "warnings": warnings,
    }


def build_report(path: Path) -> dict[str, Any]:
    source_records = list(_iter_records(path))
    records = [inspect_record(record) for record in source_records]
    issue_records = [item for item in records if item["issues"]]
    warning_records = [item for item in records if item["warnings"]]
    counts: dict[str, int] = {}
    warning_counts: dict[str, int] = {}
    for item in records:
        for issue in item["issues"]:
            counts[issue["code"]] = counts.get(issue["code"], 0) + 1
        for warning in item["warnings"]:
            warning_counts[warning["code"]] = warning_counts.get(warning["code"], 0) + 1
    date_counts: dict[str, int] = {}
    for record in source_records:
        summary = record.get("summary") if isinstance(record.get("summary"), Mapping) else {}
        date = _text(summary.get("report_date"))
        if date:
            date_counts[date] = date_counts.get(date, 0) + 1
    dataset_warnings: list[dict[str, str]] = []
    if records and date_counts:
        dominant_date, dominant_count = max(date_counts.items(), key=lambda item: item[1])
        if dominant_count / len(records) >= 0.8:
            dataset_warnings.append({
                "code": "dominant_report_date_requires_source_check",
                "detail": f"{dominant_date!r} appears in {dominant_count}/{len(records)} records",
            })

    return {
        "input": str(path),
        "record_count": len(records),
        "valid": not issue_records,
        "issue_record_count": len(issue_records),
        "warning_record_count": len(warning_records),
        "issue_counts": counts,
        "warning_counts": warning_counts,
        "dataset_warnings": dataset_warnings,
        "report_date_counts": dict(sorted(date_counts.items(), key=lambda item: (-item[1], item[0]))),
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
