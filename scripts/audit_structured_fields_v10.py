#!/usr/bin/env python3
"""Audit v10 structured facts from source DOCX through the official DOCX table.

This is a deliberately small, read-only audit utility.  It does not score Gold,
call an LLM, change templates, or write predictions back into the source tree.
It records source provenance for the summary fields that can affect the
platform's brief-information correctness and verifies their renderer aliases.

Typical use on the full preliminary set::

    python scripts/audit_structured_fields_v10.py \
      --input-root <92-docx-root> \
      --output-dir reports/v10-structured-field-audit \
      --stage before --expected-count 92

After applying a patch, rerun with ``--stage after`` and the same input root.
The script preserves ``field_audit.before.jsonl`` and writes a compact
``before_after_summary.json`` when both snapshots exist.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata
from pathlib import Path
import tempfile
import sys
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from docx import Document

from src.extraction.summary import extract_summary
from src.extraction.output_normalizer import normalize_prediction_output
from src.contracts import InspectionPrediction
from src.parsing import parse_docx
from src.rendering import build_submission_document, render_template_report
from src.routing import route_sections


AUDIT_FIELDS: tuple[str, ...] = (
    "bridge_name",
    "report_date",
    "overall_score",
    "overall_grade",
    "superstructure_score",
    "superstructure_grade",
    "substructure_score",
    "substructure_grade",
    "deck_score",
    "deck_grade",
    "previous_overall_score",
    "previous_overall_grade",
    "trend",
)

RENDERER_KEYS = {
    "bridge_name": "bridge_name",
    "report_date": "report_date",
    "overall_score": "overall_score",
    "overall_grade": "overall_grade",
    "superstructure_score": "superstructure_score",
    "superstructure_grade": "superstructure_grade",
    "substructure_score": "substructure_score",
    "substructure_grade": "substructure_grade",
    "deck_score": "deck_system_score",
    "deck_grade": "deck_system_grade",
    "previous_overall_score": "previous_overall_score",
    "previous_overall_grade": "previous_overall_grade",
    "trend": "defect_development_trend",
}

PUBLIC_DATE_KINDS = {
    "cover": "report_date",
    "sign": "report_date",
    "cover_range_end": "date_range",
    "detection": "inspection_date",
    "detection_end": "inspection_date",
    "range": "date_range",
}


def _jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _source_section(document, routes, block_index: int | None) -> str:
    if block_index is None:
        return ""
    for route in routes:
        if any(getattr(block, "block_index", None) == block_index for block in route.blocks):
            return str(route.category.value)
    # History/identity sections are intentionally outside the six-route model;
    # retain a nearby paragraph heading instead of inventing a category.
    previous = ""
    for block in document.blocks:
        current = getattr(block, "block_index", None)
        if current is None or current > block_index:
            break
        text = " ".join(str(getattr(block, "raw_text", "") or "").split())
        if 0 < len(text) <= 80:
            previous = text
    return previous


def _table_labels(document, source) -> tuple[str, str]:
    if source is None or source.table_index is None or source.row_index is None:
        return "", ""
    table = next(
        (
            block
            for block in document.blocks
            if getattr(block, "table_index", None) == source.table_index
        ),
        None,
    )
    if table is None:
        return "", ""
    row = next((row for row in table.rows if row.row_index == source.row_index), None)
    if row is None:
        return "", ""

    # Prefer a textual label in the same row over a serial/group number.
    row_label = ""
    for cell in row.cells:
        if source.column_index is not None and cell.column_index == source.column_index:
            continue
        text = " ".join(cell.raw_text.split())
        if text and not text.isdigit() and any("\u4e00" <= ch <= "\u9fff" for ch in text):
            row_label = text
            break
    if not row_label:
        for cell in row.cells:
            text = " ".join(cell.raw_text.split())
            if text:
                row_label = text
                break

    # Only rows preceding the source row can be column headers.  Prefer
    # header-like labels and never let numeric data rows overwrite them.
    header_tokens = ("评分", "得分", "分数", "等级", "级别", "日期", "名称", "结果", "发展", "状态", "内容")
    candidates: list[tuple[int, str]] = []
    if source.column_index is not None:
        for header_row in table.rows:
            if header_row.row_index >= source.row_index:
                break
            for cell in header_row.cells:
                start = cell.column_index
                span = max(1, cell.column_span)
                if start <= source.column_index < start + span:
                    text = " ".join(cell.raw_text.split())
                    if not text:
                        continue
                    score = 10 if any(token in text for token in header_tokens) else 0
                    candidates.append((score, text))
    best = max(candidates, default=(0, ""), key=lambda item: item[0])
    column_label = best[1] if best[0] >= 10 else ""
    return row_label, column_label


def _candidate_for_value(candidates, value: str):
    matching = [candidate for candidate in candidates if candidate.value == value]
    pool = matching or list(candidates)
    if not pool:
        return None
    return sorted(
        pool,
        key=lambda candidate: (
            -int(getattr(candidate, "priority", 0)),
            getattr(getattr(candidate, "source", None), "block_index", 10**9),
        ),
    )[0]


def _canonical_value(field: str, value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if field.endswith("_score"):
        match = re.search(r"(?<![\d.])-?\d+(?:\.\d+)?", text)
        if match is not None:
            try:
                number = Decimal(match.group(0)).normalize()
                return format(number, "f")
            except InvalidOperation:
                pass
    if field.endswith("_grade"):
        return re.sub(r"\s+", "", text).upper()
    return re.sub(r"[\s:：,，;；。．.（）()\[\]【】]", "", text).casefold()


def _anchor_supports_value(field: str, value: str, selected) -> bool:
    if selected is None or getattr(selected, "source", None) is None:
        return False
    raw = str(getattr(selected.source, "raw_text", "") or "")
    canonical = _canonical_value(field, value)
    if not canonical:
        return False
    return canonical in _canonical_value(field, raw)


def _strong_selected_source(field: str, selected) -> bool:
    if selected is None:
        return False
    kind = str(getattr(selected, "source_kind", "") or "")
    label = str(getattr(selected, "label", "") or "")
    if field == "bridge_name":
        return kind in {
            "facility_name", "cover_facility_name", "cover_name", "body_name"
        } and any(token in label for token in ("工程名称", "设施名称", "桥梁名称", "通道名称", "天桥名称", "隧道名称", "涵洞名称", "道路名称", "设施名"))
    if field == "report_date":
        return getattr(selected, "date_kind", None) in {"cover", "sign", "cover_range_end"}
    if field in {"superstructure_score", "substructure_score", "deck_score"}:
        return kind in {"overall_assessment_table", "bci"}
    if field in {"overall_score", "overall_grade"}:
        return kind in {"bci", "overall_assessment_table", "underpass_conclusion"}
    if field.startswith("previous_"):
        return kind in {"previous_detection", "history_comparison"}
    return False


def _audit_state(
    field: str,
    value: str,
    candidates,
    selected,
    *,
    renderer_match: bool,
) -> tuple[str, bool, bool, int]:
    visible = str(value or "").strip()
    canonical_values = {
        _canonical_value(field, candidate.value)
        for candidate in candidates
        if _canonical_value(field, candidate.value)
    }
    distinct_count = len(canonical_values)
    raw_conflict = distinct_count > 1
    if visible in {"无", "暂无", "不适用"}:
        return "explicit_none", False, False, distinct_count
    if not visible:
        return "missing", False, False, distinct_count

    anchor_match = _anchor_supports_value(field, visible, selected)
    # A large candidate pool is normal in long inspection reports.  If the
    # selected value is directly anchored in an authoritative source and the
    # exact value survives SubmissionDocument -> DOCX, the selection is
    # resolved even when lower-priority/incidental candidates differ.
    resolved = bool(
        selected is not None
        and anchor_match
        and renderer_match
        and (_strong_selected_source(field, selected) or not raw_conflict)
    )
    if resolved:
        return "extracted", False, True, distinct_count
    if raw_conflict:
        return "ambiguous", True, False, distinct_count
    return "extracted", False, bool(anchor_match and renderer_match), distinct_count


def _template_scalar_locations(fields_path: Path) -> dict[str, tuple[int, int, int]]:
    payload = json.loads(fields_path.read_text(encoding="utf-8"))
    result: dict[str, tuple[int, int, int]] = {}
    for key, item in payload.get("scalars", {}).items():
        location = item.get("location", {})
        if {"table_index", "row", "column"} <= set(location):
            result[key] = (
                int(location["table_index"]),
                int(location["row"]),
                int(location["column"]),
            )
    return result


def _rendered_scalars(payload: dict[str, object], template: Path, fields: Path) -> dict[str, str]:
    locations = _template_scalar_locations(fields)
    with tempfile.TemporaryDirectory(prefix="v10-field-audit-") as tmp:
        output = Path(tmp) / "audit.docx"
        render_template_report(payload, output, template_path=template, fields_path=fields)
        document = Document(output)
        result: dict[str, str] = {}
        for key, (table_index, row_index, column_index) in locations.items():
            result[key] = document.tables[table_index].rows[row_index].cells[column_index].text.strip()
        return result


def _record_for_field(path: Path, root: Path, document, routes, summary, submission, rendered, field: str) -> dict[str, object]:
    value = str(getattr(summary.summary, field, "") or "")
    candidates = tuple(summary.candidates.get(field, ()))
    selected = _candidate_for_value(candidates, value)
    source = getattr(selected, "source", None)
    row_label, column_label = _table_labels(document, source)
    renderer_key = RENDERER_KEYS[field]
    submission_value = str(submission.scalars.get(renderer_key, ""))
    rendered_value = str(rendered.get(renderer_key, ""))
    expected_visible = value if value and value != "未提取到" else "无"
    if expected_visible == "":
        expected_visible = "无"
    renderer_match = submission_value == expected_visible and rendered_value == expected_visible
    state, conflict, selection_resolved, distinct_candidate_count = _audit_state(
        field, value, candidates, selected, renderer_match=renderer_match
    )
    anchor_value_match = _anchor_supports_value(field, value, selected)
    date_kind = None
    if field == "report_date":
        internal_kind = getattr(selected, "date_kind", None) if selected else None
        date_kind = PUBLIC_DATE_KINDS.get(str(internal_kind or ""), "unknown")
    return {
        "sample_id": path.relative_to(root).with_suffix("").as_posix(),
        "filename": path.name,
        "facility_type": summary.facility_context.facility_type_raw or summary.facility_context.facility_type,
        "field": field,
        "value": value,
        "state": state,
        "source_kind": getattr(selected, "source_kind", "") if selected else "",
        "source_section": _source_section(document, routes, getattr(source, "block_index", None)),
        "anchor": source.to_dict() if source is not None else None,
        "row_label": row_label,
        "column_label": column_label,
        "candidate_count": len(candidates),
        "distinct_candidate_count": distinct_candidate_count,
        "conflict": conflict,
        "selection_resolved": selection_resolved,
        "anchor_value_match": anchor_value_match,
        "renderer_key": renderer_key,
        "submission_value": submission_value,
        "rendered_docx_value": rendered_value,
        "renderer_match": renderer_match,
        "date_kind": date_kind,
    }


def _summarize(rows: list[dict[str, object]], *, stage: str, input_count: int, errors: list[dict[str, str]]) -> dict[str, object]:
    states = Counter(str(row["state"]) for row in rows)
    renderer_mismatches = [row for row in rows if not bool(row.get("renderer_match"))]
    by_field: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_field[str(row["field"])][str(row["state"])] += 1
    report_dates = [str(row["value"]) for row in rows if row["field"] == "report_date" and row.get("value")]
    common_dates = Counter(report_dates).most_common(5)
    return {
        "stage": stage,
        "input_count": input_count,
        "field_record_count": len(rows),
        "states": dict(states),
        "renderer_mismatch_count": len(renderer_mismatches),
        "renderer_mismatch_samples": sorted({str(row["sample_id"]) for row in renderer_mismatches})[:20],
        "by_field": {field: dict(counter) for field, counter in sorted(by_field.items())},
        "common_report_dates": common_dates,
        "errors": errors,
        "platform_score_verified": False,
    }


def _write_markdown(path: Path, summary: dict[str, object], *, expected_count: int) -> None:
    input_count = int(summary.get("input_count", 0))
    lines = [
        "# v10 structured-field audit",
        "",
        f"- stage: `{summary.get('stage')}`",
        f"- inputs audited: **{input_count}** / expected **{expected_count}**",
        f"- field records: **{summary.get('field_record_count', 0)}**",
        f"- renderer mismatches: **{summary.get('renderer_mismatch_count', 0)}**",
        "- platform score: **尚未验证**",
        "",
    ]
    if input_count != expected_count:
        lines += [
            "> BLOCKED: 当前输入数量与官方 92 份目标不一致。本报告不得被表述为完整 before/after 平台集审计。",
            "",
        ]
    lines += ["## 字段状态", "", "| field | extracted | explicit_none | missing | ambiguous |", "|---|---:|---:|---:|---:|"]
    for field, counts in summary.get("by_field", {}).items():
        lines.append(
            f"| {field} | {counts.get('extracted', 0)} | {counts.get('explicit_none', 0)} | {counts.get('missing', 0)} | {counts.get('ambiguous', 0)} |"
        )
    lines += ["", "## 高频报告日期", ""]
    for value, count in summary.get("common_report_dates", []):
        lines.append(f"- `{value}`: {count}")
    lines += [
        "",
        "## 结论边界",
        "",
        "- 代码已修复：仅能由源码 diff 与专项测试确认。",
        "- 字段来源已确认：仅限 field_audit.jsonl 中存在 source/anchor 的记录。",
        "- 字段仍缺失：state=missing。",
        "- 字段存在歧义：state=ambiguous / conflict=true。",
        "- 渲染映射已修复：renderer_match=true 仅说明 Prediction→SubmissionDocument→DOCX 当前值一致。",
        "- 平台分数尚未验证：本审计绝不等同平台提分。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _compare(before: list[dict[str, object]], after: list[dict[str, object]]) -> dict[str, object]:
    def key(row):
        return str(row.get("sample_id")), str(row.get("field"))
    before_map = {key(row): row for row in before}
    after_map = {key(row): row for row in after}
    changed: list[dict[str, object]] = []
    for item in sorted(set(before_map) | set(after_map)):
        left = before_map.get(item, {})
        right = after_map.get(item, {})
        if (left.get("value"), left.get("state"), left.get("renderer_match")) != (
            right.get("value"), right.get("state"), right.get("renderer_match")
        ):
            changed.append({
                "sample_id": item[0],
                "field": item[1],
                "before": {k: left.get(k) for k in ("value", "state", "source_kind", "renderer_match")},
                "after": {k: right.get(k) for k in ("value", "state", "source_kind", "renderer_match")},
            })
    return {
        "status": "compared",
        "before_records": len(before),
        "after_records": len(after),
        "changed_count": len(changed),
        "changed": changed,
        "platform_score_verified": False,
    }




def _identity_variants(value: object) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ()
    path = Path(text)
    raw_items = [text, path.as_posix(), path.name, path.stem]
    variants: list[str] = []
    for item in raw_items:
        candidates = (item, re.sub(r"^\d+__", "", item))
        for candidate in candidates:
            normalized = re.sub(r"\s+", "", candidate).casefold()
            if normalized and normalized not in variants:
                variants.append(normalized)
    return tuple(variants)

def _prediction_summary(record: dict[str, object]) -> dict[str, object]:
    summary = record.get("summary")
    return dict(summary) if isinstance(summary, dict) else {}


def _prediction_keys(record: dict[str, object]) -> tuple[str, ...]:
    keys: list[str] = []
    for raw in (record.get("sample_id"), record.get("source_file")):
        for key in _identity_variants(raw):
            if key not in keys:
                keys.append(key)
    return tuple(keys)


def _audit_row_keys(row: dict[str, object]) -> tuple[str, ...]:
    keys: list[str] = []
    for raw in (row.get("sample_id"), row.get("filename")):
        for key in _identity_variants(raw):
            if key not in keys:
                keys.append(key)
    return tuple(keys)


def _compare_prediction_baseline(
    baseline_path: Path,
    after_rows: list[dict[str, object]],
    *,
    baseline_label: str,
    current_label: str,
) -> dict[str, object]:
    records = _jsonl(baseline_path)
    lookup: dict[str, dict[str, object]] = {}
    for record in records:
        for key in _prediction_keys(record):
            lookup.setdefault(key, record)

    fields = {
        "bridge_name", "report_date", "overall_score", "overall_grade",
        "superstructure_score", "superstructure_grade",
        "substructure_score", "substructure_grade",
        "deck_score", "deck_grade", "previous_overall_score",
        "previous_overall_grade", "trend",
    }
    changes: list[dict[str, object]] = []
    matched_samples: set[str] = set()
    missing_baseline_samples: set[str] = set()
    for row in after_rows:
        field = str(row.get("field") or "")
        if field not in fields:
            continue
        baseline = next((lookup[key] for key in _audit_row_keys(row) if key in lookup), None)
        sample = str(row.get("sample_id") or "")
        if baseline is None:
            missing_baseline_samples.add(sample)
            continue
        matched_samples.add(sample)
        before_summary = _prediction_summary(baseline)
        before_value = str(before_summary.get(field, "") or "")
        after_value = str(row.get("value", "") or "")
        if _canonical_value(field, before_value) == _canonical_value(field, after_value):
            continue
        changes.append({
            "sample_id": sample,
            "filename": row.get("filename"),
            "field": field,
            baseline_label: before_value,
            current_label: after_value,
            "current_state": row.get("state"),
            "current_source_kind": row.get("source_kind"),
            "current_anchor": row.get("anchor"),
        })

    by_field = Counter(str(item["field"]) for item in changes)
    return {
        "status": "compared",
        "baseline_label": baseline_label,
        "current_label": current_label,
        "baseline_record_count": len(records),
        "matched_sample_count": len(matched_samples),
        "missing_baseline_samples": sorted(missing_baseline_samples),
        "changed_count": len(changes),
        "changed_by_field": dict(sorted(by_field.items())),
        "changed": changes,
        "platform_score_verified": False,
    }


def _write_baseline_diff_markdown(path: Path, payload: dict[str, object]) -> None:
    lines = [
        f"# {payload.get('baseline_label')} vs {payload.get('current_label')} structured-field diff",
        "",
        f"- baseline records: **{payload.get('baseline_record_count', 0)}**",
        f"- matched samples: **{payload.get('matched_sample_count', 0)}**",
        f"- changed fields: **{payload.get('changed_count', 0)}**",
        "- platform score: **尚未验证**",
        "",
        "## Changes by field",
        "",
    ]
    for field, count in payload.get("changed_by_field", {}).items():
        lines.append(f"- `{field}`: {count}")
    lines += ["", "## Field-level changes", ""]
    before_key = str(payload.get("baseline_label"))
    after_key = str(payload.get("current_label"))
    for item in payload.get("changed", []):
        lines.append(
            f"- `{item.get('sample_id')}` · `{item.get('field')}`: "
            f"`{item.get(before_key, '')}` → `{item.get(after_key, '')}` "
            f"({item.get('current_source_kind', '')})"
        )
    if payload.get("missing_baseline_samples"):
        lines += ["", "## Missing baseline matches", ""]
        lines.extend(f"- `{value}`" for value in payload["missing_baseline_samples"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/v10-structured-field-audit"))
    parser.add_argument("--stage", choices=("before", "after"), default="after")
    parser.add_argument("--expected-count", type=int, default=92)
    parser.add_argument("--template", type=Path, default=Path("assets/templates/information_extraction_v1.docx"))
    parser.add_argument("--fields", type=Path, default=Path("assets/templates/template_fields.json"))
    parser.add_argument("--baseline-prediction-jsonl", type=Path)
    parser.add_argument("--baseline-label", default="v8")
    parser.add_argument("--current-label", default="v11")
    args = parser.parse_args()

    root = args.input_root
    paths = sorted(root.rglob("*.docx"), key=lambda path: path.relative_to(root).as_posix()) if root.is_dir() else []
    if not paths:
        raise SystemExit(f"no DOCX inputs found under: {root}")

    rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for path in paths:
        source_file = path.relative_to(root).as_posix()
        try:
            document = parse_docx(path, source_file=source_file)
            routes = route_sections(document)
            summary = extract_summary(document, routes)
            prediction = normalize_prediction_output(
                InspectionPrediction(
                    sample_id=path.relative_to(root).with_suffix("").as_posix(),
                    source_file=source_file,
                    summary=summary.summary,
                ),
                facility_context=summary.facility_context,
                source_recommendations_summary=summary.summary.recommendations_summary,
            )
            payload = prediction.to_dict()
            payload["field_states"] = dict(summary.field_states)
            payload["facility_context"] = summary.facility_context.to_dict()
            submission = build_submission_document(payload)
            rendered = _rendered_scalars(payload, args.template, args.fields)
            for field in AUDIT_FIELDS:
                rows.append(_record_for_field(path, root, document, routes, summary, submission, rendered, field))
        except Exception as error:  # audit every remaining input; report failures explicitly
            errors.append({"source_file": source_file, "error": f"{type(error).__name__}: {error}"[:500]})

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    current = output / "field_audit.jsonl"
    _write_jsonl(current, rows)
    snapshot = output / f"field_audit.{args.stage}.jsonl"
    _write_jsonl(snapshot, rows)
    summary = _summarize(rows, stage=args.stage, input_count=len(paths), errors=errors)
    (output / f"summary.{args.stage}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown(output / "field_audit.md", summary, expected_count=args.expected_count)

    before = _jsonl(output / "field_audit.before.jsonl")
    after = _jsonl(output / "field_audit.after.jsonl")
    if before and after:
        comparison = _compare(before, after)
    else:
        comparison = {
            "status": "awaiting_both_snapshots",
            "before_records": len(before),
            "after_records": len(after),
            "platform_score_verified": False,
        }
    comparison["expected_input_count"] = args.expected_count
    comparison["current_input_count"] = len(paths)
    (output / "before_after_summary.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    baseline_diff = None
    if args.baseline_prediction_jsonl is not None:
        baseline_diff = _compare_prediction_baseline(
            args.baseline_prediction_jsonl,
            rows,
            baseline_label=args.baseline_label,
            current_label=args.current_label,
        )
        diff_stem = f"{args.baseline_label}-vs-{args.current_label}-field-diff"
        (output / f"{diff_stem}.json").write_text(
            json.dumps(baseline_diff, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_baseline_diff_markdown(output / f"{diff_stem}.md", baseline_diff)

    print(json.dumps({
        "stage": args.stage,
        "input_count": len(paths),
        "field_records": len(rows),
        "errors": len(errors),
        "renderer_mismatches": summary["renderer_mismatch_count"],
        "output_dir": str(output),
        "complete_92": len(paths) == args.expected_count and not errors,
        "baseline_diff_changes": baseline_diff.get("changed_count") if baseline_diff else None,
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
