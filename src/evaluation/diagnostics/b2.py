"""Deterministic, privacy-safe diagnostics for the B2 evaluation.

The official scorer remains the source of truth for scores.  This module adds
explainable, summary-only diagnostics around its three B2 sections: summary,
defects, and recommendations.  It never serializes field values or Word
``raw_text``; source anchors are retained as structural coordinates only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from ..scorer import (
    DEFECT_FIELDS,
    RECOMMENDATION_FIELDS,
    load_records,
    normalize_text,
    score_dataset,
)


SUMMARY_FIELDS = (
    "bridge_name",
    "bridge_id",
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
    "overall_conclusion",
    "risk_points",
    "recommendations_summary",
)

CATEGORY_ORDER = ("missing", "extra", "wrong_column", "description_difference")
_ALL_CATEGORIES = CATEGORY_ORDER + ("value_difference",)
_SECTION_WEIGHTS = {"summary": 20.0, "defects": 30.0, "recommendations": 20.0}
DEFECT_COUNT_BUCKETS = ("0", "1-10", "11-50", "51+")
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|\\\\\?\\)")
_ANCHOR_FIELDS = (
    "source_file",
    "block_index",
    "table_index",
    "row_index",
    "column_index",
    "paragraph_index",
)


def _round(value: float) -> float:
    return round(float(value), 6)


def _metrics(true_positive: int, false_positive: int, false_negative: int) -> dict[str, float]:
    predicted = true_positive + false_positive
    expected = true_positive + false_negative
    precision = true_positive / predicted if predicted else 1.0
    recall = true_positive / expected if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": _round(precision), "recall": _round(recall), "f1": _round(f1)}


def _count_template() -> dict[str, int]:
    return {"true_positive": 0, "false_positive": 0, "false_negative": 0}


def _category_template() -> dict[str, int]:
    return {category: 0 for category in _ALL_CATEGORIES}


def _field_count_template() -> dict[str, int]:
    return {"matched": 0, **_category_template()}


def _safe_text(value: Any, *, path: bool = False) -> str:
    text = " ".join(str(value).split())
    if _ABSOLUTE_PATH.match(text):
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
        return f"sha256:{digest}"
    if path:
        return text.replace("\\", "/")
    return text


def _safe_id(value: Any) -> str:
    text = _safe_text(value)
    return text if text else "unknown"


def _safe_anchor(value: Any) -> tuple[dict[str, Any] | None, int]:
    if not isinstance(value, Mapping):
        return None, 0
    result: dict[str, Any] = {}
    for key in _ANCHOR_FIELDS:
        if key not in value or value[key] is None:
            continue
        if key == "source_file":
            result[key] = _safe_text(value[key], path=True)
        else:
            try:
                result[key] = int(value[key])
            except (TypeError, ValueError):
                continue
    return (result or None), int("raw_text" in value)


def _anchors(value: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(value, Mapping):
        return [], 0
    candidates = value.get("evidence")
    if candidates is None:
        candidates = value.get("source_anchor") or value.get("source")
    if candidates is None and any(key in value for key in _ANCHOR_FIELDS):
        candidates = [value]
    if isinstance(candidates, Mapping):
        candidates = [candidates]
    if not isinstance(candidates, (list, tuple)):
        return [], 0
    safe: list[dict[str, Any]] = []
    redacted = 0
    for candidate in candidates:
        anchor, count = _safe_anchor(candidate)
        redacted += count
        if anchor is not None:
            safe.append(anchor)
    return safe, redacted


def load_json_records(source: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Load JSON/JSONL records or normalize an already loaded JSON payload."""

    if isinstance(source, (str, Path)):
        return load_records(source)
    if isinstance(source, Mapping):
        if isinstance(source.get("records"), list):
            source = source["records"]  # type: ignore[assignment]
        elif isinstance(source.get("samples"), list):
            source = source["samples"]  # type: ignore[assignment]
        else:
            source = [source]
    if not isinstance(source, Sequence) or isinstance(source, (str, bytes, bytearray)):
        raise TypeError("records must be a JSON object, JSON list, JSONL path, or sequence")
    if not all(isinstance(record, Mapping) for record in source):
        raise ValueError("every record must be a JSON object")
    return [dict(record) for record in source]


def _mapping(value: Any) -> tuple[Mapping[str, Any], bool]:
    if value is None:
        return {}, False
    if isinstance(value, Mapping):
        return value, False
    return {}, True


def _rows(value: Any) -> tuple[list[tuple[int, Mapping[str, Any]]], list[int]]:
    if value is None:
        return [], []
    values = list(value) if isinstance(value, (list, tuple)) else [value]
    valid: list[tuple[int, Mapping[str, Any]]] = []
    invalid: list[int] = []
    for index, row in enumerate(values):
        if isinstance(row, Mapping):
            valid.append((index, row))
        else:
            invalid.append(index)
    return valid, invalid


def _norm(value: Any) -> str:
    return normalize_text(value)


def _issue(
    category: str,
    *,
    field: str | None = None,
    gold_row: int | None = None,
    prediction_row: int | None = None,
    gold_value: Any = None,
    prediction_value: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"category": category}
    if field is not None:
        result["field"] = field
    if gold_row is not None:
        result["gold_row"] = gold_row
        anchors, redacted = _anchors(gold_value)
        result["gold_anchors"] = anchors
        if redacted:
            result["gold_raw_text_redacted"] = redacted
    if prediction_row is not None:
        result["prediction_row"] = prediction_row
        anchors, redacted = _anchors(prediction_value)
        result["prediction_anchors"] = anchors
        if redacted:
            result["prediction_raw_text_redacted"] = redacted
    return result


def _section_result(
    counts: dict[str, int],
    category_counts: dict[str, int],
    field_counts: dict[str, dict[str, int]],
    *,
    fields: Mapping[str, str] | None = None,
    issues: list[dict[str, Any]] | None = None,
    evidence: dict[str, int] | None = None,
    row_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metrics = _metrics(counts["true_positive"], counts["false_positive"], counts["false_negative"])
    result: dict[str, Any] = {
        "counts": counts,
        **metrics,
        "category_counts": category_counts,
        "field_counts": field_counts,
        "issues": issues or [],
        "evidence": evidence or {"gold_anchor_count": 0, "prediction_anchor_count": 0, "raw_text_redacted_count": 0},
    }
    if fields is not None:
        result["fields"] = dict(fields)
    if row_matches is not None:
        result["row_matches"] = row_matches
    return result


def _summary_diagnostic(gold: Any, prediction: Any) -> tuple[dict[str, Any], Counter[str]]:
    gold_summary, gold_invalid = _mapping(gold)
    prediction_summary, prediction_invalid = _mapping(prediction)
    flags: Counter[str] = Counter()
    if gold_invalid:
        flags["invalid_gold_summary"] += 1
    if prediction_invalid:
        flags["invalid_prediction_summary"] += 1

    fields = sorted(set(gold_summary) | set(prediction_summary), key=str)
    statuses: dict[str, str] = {}
    field_counts: dict[str, dict[str, int]] = {}
    categories = _category_template()
    counts = _count_template()
    issues: list[dict[str, Any]] = []
    gold_anchor_count = prediction_anchor_count = redacted = 0
    for field in fields:
        in_gold = field in gold_summary
        in_prediction = field in prediction_summary
        if in_gold and in_prediction:
            gold_anchors, gold_redacted = _anchors(gold_summary[field])
            prediction_anchors, prediction_redacted = _anchors(prediction_summary[field])
            gold_anchor_count += len(gold_anchors)
            prediction_anchor_count += len(prediction_anchors)
            redacted += gold_redacted + prediction_redacted
            if _norm(gold_summary[field]) == _norm(prediction_summary[field]):
                status = "matched"
                counts["true_positive"] += 1
            else:
                status = "value_difference"
                counts["false_positive"] += 1
                counts["false_negative"] += 1
                categories["value_difference"] += 1
                issues.append(_issue("value_difference", field=field, gold_value=gold_summary[field], prediction_value=prediction_summary[field]))
        elif in_gold:
            status = "missing"
            counts["false_negative"] += 1
            categories["missing"] += 1
            issues.append(_issue("missing", field=field, gold_value=gold_summary[field]))
        else:
            status = "extra"
            counts["false_positive"] += 1
            categories["extra"] += 1
            issues.append(_issue("extra", field=field, prediction_value=prediction_summary[field]))
        statuses[str(field)] = status
        field_counts[str(field)] = _field_count_template()
        field_counts[str(field)][status] += 1

    if redacted:
        flags["raw_text_redacted"] += redacted
    return (
        _section_result(
            counts,
            categories,
            field_counts,
            fields=statuses,
            issues=issues,
            evidence={
                "gold_anchor_count": gold_anchor_count,
                "prediction_anchor_count": prediction_anchor_count,
                "raw_text_redacted_count": redacted,
            },
        ),
        flags,
    )


def _value_overlap(gold: Mapping[str, Any], prediction: Mapping[str, Any], fields: Sequence[str]) -> int:
    gold_values = Counter(_norm(gold.get(field)) for field in fields if _norm(gold.get(field)))
    prediction_values = Counter(_norm(prediction.get(field)) for field in fields if _norm(prediction.get(field)))
    return sum((gold_values & prediction_values).values())


def _row_score(
    gold: Mapping[str, Any], prediction: Mapping[str, Any], fields: Sequence[str]
) -> tuple[tuple[int, int, int, int, int], bool]:
    identity_fields = [field for field in fields if field not in {"description", "content"}]
    same_slots = sum(
        bool(_norm(gold.get(field))) and _norm(gold.get(field)) == _norm(prediction.get(field))
        for field in fields
    )
    identity_same = sum(
        bool(_norm(gold.get(field))) and _norm(gold.get(field)) == _norm(prediction.get(field))
        for field in identity_fields
    )
    cross_matches = 0
    for field in fields:
        value = _norm(prediction.get(field))
        if value and any(value == _norm(gold.get(other)) for other in fields if other != field):
            cross_matches += 1
    overlap = _value_overlap(gold, prediction, fields)
    index_same = int(bool(_norm(gold.get("index"))) and _norm(gold.get("index")) == _norm(prediction.get("index")))
    eligible = bool(identity_same or same_slots or cross_matches or (index_same and overlap))
    return (identity_same, same_slots, cross_matches, overlap, index_same), eligible


def _row_exact(gold: Mapping[str, Any], prediction: Mapping[str, Any], fields: Sequence[str]) -> bool:
    return all(_norm(gold.get(field)) == _norm(prediction.get(field)) for field in fields)


def _field_status(
    field: str,
    gold: Mapping[str, Any],
    prediction: Mapping[str, Any],
    fields: Sequence[str],
) -> str:
    in_gold = field in gold
    in_prediction = field in prediction
    if not in_gold and not in_prediction:
        return "matched"
    if in_gold and not in_prediction:
        return "missing"
    if not in_gold and in_prediction:
        return "extra"
    gold_value = _norm(gold.get(field))
    prediction_value = _norm(prediction.get(field))
    if gold_value == prediction_value:
        return "matched"
    if field in fields and (
        (prediction_value and any(prediction_value == _norm(gold.get(other)) for other in fields if other != field))
        or (gold_value and any(gold_value == _norm(prediction.get(other)) for other in fields if other != field))
    ):
        return "wrong_column"
    if field in {"description", "content"}:
        return "description_difference"
    return "value_difference"


def _rows_diagnostic(
    section: str,
    gold: Any,
    prediction: Any,
    fields: Sequence[str],
) -> tuple[dict[str, Any], Counter[str]]:
    gold_rows, invalid_gold = _rows(gold)
    prediction_rows, invalid_prediction = _rows(prediction)
    flags: Counter[str] = Counter()
    if invalid_gold:
        flags["invalid_gold_row"] += len(invalid_gold)
    if invalid_prediction:
        flags["invalid_prediction_row"] += len(invalid_prediction)

    pairs: dict[int, tuple[int, Mapping[str, Any]]] = {}
    used_predictions: set[int] = set()
    row_matches: list[dict[str, Any]] = []
    for gold_index, (gold_row_index, gold_row) in enumerate(gold_rows):
        candidates: list[tuple[tuple[int, int, int, int, int], int, Mapping[str, Any]]] = []
        for prediction_index, (prediction_row_index, prediction_row) in enumerate(prediction_rows):
            if prediction_index in used_predictions:
                continue
            score, eligible = _row_score(gold_row, prediction_row, fields)
            if eligible:
                candidates.append((score, prediction_index, prediction_row))
        candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        if not candidates:
            continue
        top_score = candidates[0][0]
        tied = [candidate for candidate in candidates if candidate[0] == top_score]
        if len(tied) > 1:
            flags["ambiguous_row_match"] += 1
        _, prediction_index, prediction_row = candidates[0]
        used_predictions.add(prediction_index)
        pairs[gold_index] = (prediction_index, prediction_row)

    counts = _count_template()
    counts.update(
        {
            "gold_rows": len(gold_rows) + len(invalid_gold),
            "prediction_rows": len(prediction_rows) + len(invalid_prediction),
            "matched_rows": 0,
            "paired_rows": 0,
            "missing_rows": 0,
            "extra_rows": 0,
        }
    )
    categories = _category_template()
    field_counts: dict[str, dict[str, int]] = {field: _field_count_template() for field in fields}
    issues: list[dict[str, Any]] = []
    gold_anchor_count = prediction_anchor_count = redacted = 0

    for gold_index, (gold_row_index, gold_row) in enumerate(gold_rows):
        if gold_index not in pairs:
            counts["false_negative"] += 1
            counts["missing_rows"] += 1
            categories["missing"] += 1
            anchors, row_redacted = _anchors(gold_row)
            gold_anchor_count += len(anchors)
            redacted += row_redacted
            for field in fields:
                field_counts[field]["missing"] += 1
            issues.append(_issue("missing", gold_row=gold_row_index, gold_value=gold_row))
            continue

        prediction_index, prediction_row = pairs[gold_index]
        prediction_row_index = prediction_rows[prediction_index][0]
        exact = _row_exact(gold_row, prediction_row, fields)
        row_status = "matched" if exact else "partial"
        if exact:
            counts["true_positive"] += 1
            counts["matched_rows"] += 1
        else:
            counts["false_positive"] += 1
            counts["false_negative"] += 1
            counts["paired_rows"] += 1
        gold_anchors, gold_redacted = _anchors(gold_row)
        prediction_anchors, prediction_redacted = _anchors(prediction_row)
        gold_anchor_count += len(gold_anchors)
        prediction_anchor_count += len(prediction_anchors)
        redacted += gold_redacted + prediction_redacted
        row_matches.append(
            {
                "gold_row": gold_row_index,
                "prediction_row": prediction_row_index,
                "status": row_status,
                "gold_anchors": gold_anchors,
                "prediction_anchors": prediction_anchors,
            }
        )
        diagnostic_fields = (set(fields) | set(gold_row) | set(prediction_row)) - {"index"}
        for field in sorted(diagnostic_fields, key=str):
            status = _field_status(str(field), gold_row, prediction_row, fields)
            field_counts.setdefault(str(field), _field_count_template())
            field_counts[str(field)][status] += 1
            if status in _ALL_CATEGORIES:
                categories[status] += 1
                issues.append(
                    _issue(
                        status,
                        field=str(field),
                        gold_row=gold_row_index,
                        prediction_row=prediction_row_index,
                        gold_value=gold_row,
                        prediction_value=prediction_row,
                    )
                )

    for prediction_index, (prediction_row_index, prediction_row) in enumerate(prediction_rows):
        if prediction_index in used_predictions:
            continue
        counts["false_positive"] += 1
        counts["extra_rows"] += 1
        categories["extra"] += 1
        for field in fields:
            field_counts[field]["extra"] += 1
        anchors, row_redacted = _anchors(prediction_row)
        prediction_anchor_count += len(anchors)
        redacted += row_redacted
        issues.append(_issue("extra", prediction_row=prediction_row_index, prediction_value=prediction_row))
    for row_index in invalid_gold:
        counts["false_negative"] += 1
        counts["missing_rows"] += 1
        categories["missing"] += 1
        issues.append(_issue("missing", gold_row=row_index))
    for row_index in invalid_prediction:
        counts["false_positive"] += 1
        counts["extra_rows"] += 1
        categories["extra"] += 1
        issues.append(_issue("extra", prediction_row=row_index))

    if redacted:
        flags["raw_text_redacted"] += redacted
    result = _section_result(
        counts,
        categories,
        field_counts,
        issues=issues,
        evidence={
            "gold_anchor_count": gold_anchor_count,
            "prediction_anchor_count": prediction_anchor_count,
            "raw_text_redacted_count": redacted,
        },
        row_matches=row_matches,
    )
    result["section"] = section
    return result, flags


def _flags(counter: Counter[str]) -> list[dict[str, int | str]]:
    return [{"code": code, "count": int(counter[code])} for code in sorted(counter) if counter[code]]


def _metadata_payload(metadata: Any) -> Any:
    if metadata is None:
        return None
    if isinstance(metadata, (str, Path)):
        return json.loads(Path(metadata).read_text(encoding="utf-8-sig"))
    return metadata


def _metadata_for(record: Mapping[str, Any], raw_id: str, metadata: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    embedded = record.get("metadata")
    if isinstance(embedded, Mapping):
        result.update(embedded)
    for key in (
        "facility",
        "facility_id",
        "facility_name",
        "bridge_id",
        "bridge_name",
        "template",
        "template_id",
        "template_name",
        "template_cluster",
        "status",
    ):
        if key in record and key not in result:
            result[key] = record[key]
    payload = _metadata_payload(metadata)
    candidates: Any = None
    if isinstance(payload, Mapping):
        if raw_id in payload and isinstance(payload[raw_id], Mapping):
            candidates = payload[raw_id]
        elif isinstance(payload.get("records"), list):
            candidates = next(
                (item for item in payload["records"] if isinstance(item, Mapping) and str(item.get("sample_id", "")) == raw_id),
                None,
            )
        elif isinstance(payload.get("samples"), list):
            candidates = next(
                (item for item in payload["samples"] if isinstance(item, Mapping) and str(item.get("sample_id", "")) == raw_id),
                None,
            )
        elif any(key in payload for key in ("facility", "facility_id", "template", "template_cluster")):
            candidates = payload
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        candidates = next(
            (item for item in payload if isinstance(item, Mapping) and str(item.get("sample_id", "")) == raw_id),
            None,
        )
    if isinstance(candidates, Mapping):
        result.update(candidates)
    summary = record.get("summary") if isinstance(record.get("summary"), Mapping) else {}
    for key in ("bridge_id",):
        if key not in result and key in summary:
            result[key] = summary[key]
    return result


def _metadata_label(metadata: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is not None and not isinstance(value, (Mapping, list, tuple)) and _norm(value):
            return _safe_text(value)
    return None


def _record_id(record: Mapping[str, Any], index: int) -> str:
    value = record.get("sample_id")
    return str(value) if value is not None and str(value).strip() else str(index)


def _align_records(
    gold_records: Sequence[Mapping[str, Any]], prediction_records: Sequence[Mapping[str, Any]]
) -> tuple[list[tuple[str, Mapping[str, Any], Mapping[str, Any], bool, bool]], list[str], list[str], Counter[str]]:
    flags: Counter[str] = Counter()
    gold_ids = [_record_id(record, index) for index, record in enumerate(gold_records)]
    prediction_ids = [_record_id(record, index) for index, record in enumerate(prediction_records)]
    complete = all(record.get("sample_id") is not None and str(record.get("sample_id")).strip() for record in gold_records) and all(
        record.get("sample_id") is not None and str(record.get("sample_id")).strip() for record in prediction_records
    )
    if complete:
        if len(set(gold_ids)) != len(gold_ids):
            raise ValueError("duplicate sample_id in gold records")
        if len(set(prediction_ids)) != len(prediction_ids):
            raise ValueError("duplicate sample_id in prediction records")
        gold_by_id = dict(zip(gold_ids, gold_records))
        prediction_by_id = dict(zip(prediction_ids, prediction_records))
        ids = gold_ids + sorted(set(prediction_ids) - set(gold_ids))
        aligned = [
            (sample_id, gold_by_id.get(sample_id, {}), prediction_by_id.get(sample_id, {}), sample_id in gold_by_id, sample_id in prediction_by_id)
            for sample_id in ids
        ]
        return aligned, sorted(set(gold_ids) - set(prediction_ids)), sorted(set(prediction_ids) - set(gold_ids)), flags

    flags["positional_record_alignment"] += 1
    count = max(len(gold_records), len(prediction_records))
    aligned = []
    for index in range(count):
        gold = gold_records[index] if index < len(gold_records) else {}
        prediction = prediction_records[index] if index < len(prediction_records) else {}
        aligned.append((_record_id(gold or prediction, index), gold, prediction, bool(gold), bool(prediction)))
    return aligned, [], [], flags


def diagnose_record(
    gold: Mapping[str, Any],
    prediction: Mapping[str, Any],
    *,
    sample_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return summary-only B2 diagnostics for one Gold/prediction pair."""

    raw_id = sample_id or _record_id(gold or prediction, 0)
    record_flags: Counter[str] = Counter()
    summary, summary_flags = _summary_diagnostic(gold.get("summary"), prediction.get("summary"))
    defects, defect_flags = _rows_diagnostic("defects", gold.get("defects"), prediction.get("defects"), DEFECT_FIELDS)
    recommendations, recommendation_flags = _rows_diagnostic(
        "recommendations", gold.get("recommendations"), prediction.get("recommendations"), RECOMMENDATION_FIELDS
    )
    record_flags.update(summary_flags)
    record_flags.update(defect_flags)
    record_flags.update(recommendation_flags)
    meta = _metadata_for(gold or prediction, raw_id, metadata)
    facility = _metadata_label(meta, ("facility", "facility_id", "facility_name", "bridge_id", "bridge_name"))
    template = _metadata_label(meta, ("template", "template_id", "template_name", "template_cluster"))
    return {
        "sample_id": _safe_id(raw_id),
        "metadata": {"facility": facility, "template": template},
        "sections": {"summary": summary, "defects": defects, "recommendations": recommendations},
        "quality_flags": _flags(record_flags),
    }


def _merge_field_counts(records: Sequence[Mapping[str, Any]], section: str) -> dict[str, dict[str, int]]:
    merged: dict[str, dict[str, int]] = {}
    for record in records:
        for field, counts in record["sections"][section].get("field_counts", {}).items():
            target = merged.setdefault(field, _field_count_template())
            for key, value in counts.items():
                target[key] = target.get(key, 0) + int(value)
    return dict(sorted(merged.items()))


def _merge_categories(records: Sequence[Mapping[str, Any]], section: str) -> dict[str, int]:
    result = _category_template()
    for record in records:
        for key, value in record["sections"][section].get("category_counts", {}).items():
            result[key] = result.get(key, 0) + int(value)
    return result


def _aggregate_section(records: Sequence[Mapping[str, Any]], section: str) -> dict[str, Any]:
    counts = _count_template()
    macro_values = {key: [] for key in ("precision", "recall", "f1")}
    for record in records:
        section_result = record["sections"][section]
        for key in counts:
            counts[key] += int(section_result["counts"].get(key, 0))
        for key in macro_values:
            macro_values[key].append(float(section_result[key]))
    micro = _metrics(counts["true_positive"], counts["false_positive"], counts["false_negative"])
    macro = {
        key: _round(sum(values) / len(values)) if values else 1.0 for key, values in macro_values.items()
    }
    result: dict[str, Any] = {
        "counts": counts,
        "micro": micro,
        "macro": macro,
        "field_counts": _merge_field_counts(records, section),
        "category_counts": _merge_categories(records, section),
        "document_count": len(records),
    }
    for key in ("gold_rows", "prediction_rows", "matched_rows", "paired_rows", "missing_rows", "extra_rows"):
        result[key] = sum(int(record["sections"][section]["counts"].get(key, 0)) for record in records)
    return result


def _view(aggregate: Mapping[str, Any], mode: str) -> dict[str, Any]:
    metrics = aggregate[mode]
    weight = _SECTION_WEIGHTS[str(aggregate.get("section", ""))] if aggregate.get("section") in _SECTION_WEIGHTS else 0.0
    return {
        "counts": dict(aggregate["counts"]),
        **metrics,
        "score": _round(weight * metrics["f1"]),
        "field_counts": aggregate["field_counts"],
        "category_counts": aggregate["category_counts"],
        "document_count": aggregate["document_count"],
    }


def _section_views(records: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    micro: dict[str, Any] = {}
    macro: dict[str, Any] = {}
    for section in ("summary", "defects", "recommendations"):
        aggregate = _aggregate_section(records, section)
        aggregate["section"] = section
        micro[section] = _view(aggregate, "micro")
        macro[section] = _view(aggregate, "macro")
    return micro, macro


def _b2_weighted_total(view: Mapping[str, Any]) -> float:
    points = sum(float(view[section]["score"]) for section in ("summary", "defects", "recommendations"))
    return _round(points / 70.0 * 100.0)


def _bucket_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    micro, macro = _section_views(records)
    return {
        "document_count": len(records),
        "gold_defect_rows": sum(int(record["sections"]["defects"]["counts"].get("gold_rows", 0)) for record in records),
        "prediction_defect_rows": sum(int(record["sections"]["defects"]["counts"].get("prediction_rows", 0)) for record in records),
        "micro": {
            section: {key: micro[section][key] for key in ("precision", "recall", "f1", "score")}
            for section in ("summary", "defects", "recommendations")
        },
        "macro": {
            section: {key: macro[section][key] for key in ("precision", "recall", "f1", "score")}
            for section in ("summary", "defects", "recommendations")
        },
        "b2_weighted_total_micro": _b2_weighted_total(micro),
        "b2_weighted_total_macro": _b2_weighted_total(macro),
    }


def _defect_bucket(count: int) -> str:
    if count == 0:
        return "0"
    if count <= 10:
        return "1-10"
    if count <= 50:
        return "11-50"
    return "51+"


def _group_buckets(records: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    has_label = any(record.get("metadata", {}).get(key) for record in records)
    if not has_label:
        return {}
    for record in records:
        label = record.get("metadata", {}).get(key)
        grouped.setdefault(str(label) if label else "unknown", []).append(record)
    return {label: _bucket_summary(grouped[label]) for label in sorted(grouped)}


def diagnose_records(
    gold_records: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]],
    prediction_records: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    metadata: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Diagnose Gold and prediction JSON records with micro/macro B2 views."""

    gold = load_json_records(gold_records)
    prediction = load_json_records(prediction_records)
    aligned, missing_ids, extra_ids, alignment_flags = _align_records(gold, prediction)
    metadata_payload = _metadata_payload(metadata)
    records: list[dict[str, Any]] = []
    for raw_id, gold_record, prediction_record, gold_present, prediction_present in aligned:
        record_metadata = _metadata_for(gold_record or prediction_record, raw_id, metadata_payload)
        record = diagnose_record(
            gold_record,
            prediction_record,
            sample_id=raw_id,
            metadata=record_metadata,
        )
        flags = Counter(item["code"] for item in record["quality_flags"])
        if not gold_present:
            flags["missing_gold_record"] += 1
        if not prediction_present:
            flags["extra_prediction_record"] += 1
        record["quality_flags"] = _flags(flags)
        record["failed"] = bool(
            not gold_present
            or not prediction_present
            or any(code.startswith("invalid_") for code in flags)
            or record_metadata.get("status") in {"failed", "parse_failed"}
        )
        records.append(record)

    micro, macro = _section_views(records)
    official_flags: Counter[str] = Counter()
    try:
        official = score_dataset(gold, prediction)
        weighted_total = float(official["micro_total_score"])
        macro_weighted_total = float(official["macro_total_score"])
        summary_score = float(official["sections"]["summary"]["score"])
    except (TypeError, ValueError, KeyError):
        official_flags["official_score_unavailable"] += 1
        weighted_total = None
        macro_weighted_total = None
        summary_score = float(micro["summary"]["score"])

    all_flags = Counter(alignment_flags)
    for record in records:
        all_flags.update(item["code"] for item in record["quality_flags"])
    all_flags.update(official_flags)
    facility_available = any(record.get("metadata", {}).get("facility") for record in records)
    template_available = any(record.get("metadata", {}).get("template") for record in records)
    defect_groups = {bucket: [] for bucket in DEFECT_COUNT_BUCKETS}
    for record in records:
        count = int(record["sections"]["defects"]["counts"].get("gold_rows", 0))
        defect_groups[_defect_bucket(count)].append(record)
    defect_count_buckets = {bucket: _bucket_summary(defect_groups[bucket]) for bucket in DEFECT_COUNT_BUCKETS}

    for section in ("summary", "defects", "recommendations"):
        micro[section]["section"] = section
        macro[section]["section"] = section
    result: dict[str, Any] = {
        "version": "b2-diagnostics-v1",
        "record_count": len(records),
        "gold_record_count": len(gold),
        "prediction_record_count": len(prediction),
        "missing_sample_ids": [_safe_id(value) for value in missing_ids],
        "extra_sample_ids": [_safe_id(value) for value in extra_ids],
        "failed_documents": sum(bool(record["failed"]) for record in records),
        "micro": {**micro, "weighted_total": weighted_total, "b2_weighted_total": _b2_weighted_total(micro)},
        "macro": {**macro, "weighted_total": macro_weighted_total, "b2_weighted_total": _b2_weighted_total(macro)},
        "summary_score": _round(summary_score),
        "defect_precision": micro["defects"]["precision"],
        "defect_recall": micro["defects"]["recall"],
        "defect_f1": micro["defects"]["f1"],
        "recommendation_f1": micro["recommendations"]["f1"],
        "weighted_total": weighted_total,
        "quality_flags": _flags(all_flags),
        "defect_count_buckets": defect_count_buckets,
        "facility_buckets": _group_buckets(records, "facility"),
        "template_buckets": _group_buckets(records, "template"),
        "bucket_metadata": {
            "facility_available": facility_available,
            "template_available": template_available,
        },
        "records": records,
    }
    result["views"] = {"micro": result["micro"], "macro": result["macro"]}
    if not facility_available:
        result["quality_flags"].append({"code": "facility_metadata_unavailable", "count": 1})
    if not template_available:
        result["quality_flags"].append({"code": "template_metadata_unavailable", "count": 1})
    result["quality_flags"] = sorted(result["quality_flags"], key=lambda item: str(item["code"]))
    return result


def diagnose(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Short alias for :func:`diagnose_records`."""

    return diagnose_records(*args, **kwargs)


diagnose_dataset = diagnose_records


def diagnose_files(
    gold_path: str | Path,
    prediction_path: str | Path,
    *,
    metadata_path: str | Path | None = None,
) -> dict[str, Any]:
    """Diagnose two JSON/JSONL files without putting input paths in the result."""

    return diagnose_records(gold_path, prediction_path, metadata=metadata_path)


def write_diagnostics(
    gold_records: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]],
    prediction_records: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]],
    output_path: str | Path,
    *,
    metadata: str | Path | Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write stable JSON diagnostics; only the caller-supplied output path is used."""

    result = diagnose_records(gold_records, prediction_records, metadata=metadata)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
