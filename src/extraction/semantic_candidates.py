"""Build a small semantic hand-off from deterministic extraction diagnostics.

The semantic path is an experiment sidecar.  It receives only ambiguous or
low-confidence candidates; the deterministic prediction remains the source of
truth for all report facts and locked fields.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.contracts.semantic_extraction import ExtractionCandidate


_DEFECT_FLAGS = {"defaulted_defect_fields", "conflicting_candidates"}
_RECOMMENDATION_FLAGS = {
    "recommendation_category_unresolved",
    "recommendation_category_inferred",
}
_SUMMARY_FLAGS = {"conflicting_candidates", "missing_value"}


def _as_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _flag_items(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("quality_flags", "quality_flag_codes", "flags", "diagnostics"):
            if key in value:
                return _flag_items(value[key])
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            item if isinstance(item, Mapping) else {"code": str(item)}
            for item in value
            if isinstance(item, Mapping) or isinstance(item, str)
        ]
    if isinstance(value, str) and value.strip():
        return [{"code": value.strip()}]
    return []


def _flag_code(flag: Mapping[str, Any]) -> str:
    return _as_text(flag.get("code") or flag.get("quality_flag"))


def _flag_index(
    flag: Mapping[str, Any],
    *names: str,
    records: Sequence[object] | None = None,
) -> int | None:
    for name in names:
        value = flag.get(name)
        if isinstance(value, bool):
            continue
        try:
            index = int(value)
        except (TypeError, ValueError):
            text = _as_text(value)
            if records is not None and text:
                for position, record in enumerate(records):
                    if isinstance(record, Mapping) and _as_text(record.get("index")) == text:
                        return position
            continue
        if (
            records is not None
            and isinstance(value, str)
            and name in {"defect_index", "recommendation_index"}
        ):
            text = _as_text(value)
            for position, record in enumerate(records):
                if isinstance(record, Mapping) and _as_text(record.get("index")) == text:
                    return position
            # Extractor quality flags use displayed one-based numeric indices;
            # the prediction sequence is zero-based.
            index -= 1
        if index >= 0:
            return index
    return None


def _report_evidence(
    report_facts: Sequence[Mapping[str, Any]],
    text: str,
    fallback: str,
) -> tuple[str, ...]:
    values: list[str] = []
    compact = _as_text(text)
    for fact in report_facts:
        fact_text = _as_text(fact.get("text"))
        if compact and fact_text and (compact in fact_text or fact_text in compact):
            identifier = fact.get("evidence_id") or fact.get("id")
            if identifier:
                values.append(str(identifier))
    if values:
        return tuple(dict.fromkeys(values))
    return (fallback,)


def _candidate(
    *,
    sample_id: str,
    task_type: str,
    candidate_id: str,
    source_text: str,
    evidence_ids: tuple[str, ...],
    rule_output: Mapping[str, Any],
    context: Mapping[str, Any],
    facility_context: Mapping[str, Any],
) -> ExtractionCandidate:
    return ExtractionCandidate(
        candidate_id=candidate_id,
        sample_id=sample_id,
        task_type=task_type,  # type: ignore[arg-type]
        source_text=source_text,
        evidence_ids=evidence_ids,
        rule_output=rule_output,
        context=context,
        facility_context=facility_context,
    )


def _explicit_candidates(
    values: object,
    *,
    sample_id: str,
    facility_context: Mapping[str, Any],
) -> list[ExtractionCandidate]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    result: list[ExtractionCandidate] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        item.setdefault("sample_id", sample_id)
        item.setdefault("facility_context", dict(facility_context))
        try:
            result.append(ExtractionCandidate.from_dict(item))
        except (TypeError, ValueError):
            continue
    return result


def build_semantic_candidates(
    baseline: Mapping[str, Any],
    diagnostics: object = None,
    report_facts: Sequence[Mapping[str, Any]] = (),
    *,
    max_candidates: int = 24,
) -> list[ExtractionCandidate]:
    """Return bounded candidates selected by diagnostics, not full extraction output."""

    sample_id = _as_text(baseline.get("sample_id")) or "sample"
    facility_context = baseline.get("facility_context")
    facility_context = dict(facility_context) if isinstance(facility_context, Mapping) else {}
    explicit = diagnostics.get("candidates") if isinstance(diagnostics, Mapping) else None
    candidates = _explicit_candidates(
        explicit, sample_id=sample_id, facility_context=facility_context
    )
    if candidates:
        return candidates[:max_candidates]

    flags = _flag_items(diagnostics)
    codes = {_flag_code(flag) for flag in flags}
    defects = baseline.get("defects")
    recommendations = baseline.get("recommendations")
    result: list[ExtractionCandidate] = []
    seen: set[str] = set()

    def add(candidate: ExtractionCandidate) -> None:
        if candidate.candidate_id not in seen and len(result) < max_candidates:
            seen.add(candidate.candidate_id)
            result.append(candidate)

    defect_flags = [
        flag
        for flag in flags
        if _flag_code(flag) == "defaulted_defect_fields"
        or (
            _flag_code(flag) == "conflicting_candidates"
            and (
                _as_text(flag.get("field")) == "defects"
                or (
                    isinstance(flag.get("details"), Mapping)
                    and _as_text(flag["details"].get("field")) == "defects"
                )
            )
        )
    ]
    if defect_flags and isinstance(defects, Sequence):
        indexes = [
            _flag_index(flag, "defect_index", "index", "row_index")
            for flag in defect_flags
        ]
        selected = [index for index in indexes if index is not None]
        for index in dict.fromkeys(selected):
            if index >= len(defects) or not isinstance(defects[index], Mapping):
                continue
            item = dict(defects[index])
            text = _as_text(item.get("description")) or _as_text(item.get("defect_type"))
            add(
                _candidate(
                    sample_id=sample_id,
                    task_type="defect_row_validation",
                    candidate_id=f"{sample_id}:defect:{index}",
                    source_text=text,
                    evidence_ids=_report_evidence(
                        report_facts, text, f"baseline:{sample_id}:defect:{index}"
                    ),
                    rule_output=item,
                    context={"defect_index": index, "reason": "diagnostic ambiguity"},
                    facility_context=facility_context,
                )
            )

    if codes & _RECOMMENDATION_FLAGS and isinstance(recommendations, Sequence):
        indexes = [
            _flag_index(
                flag,
                "recommendation_index",
                "index",
                "row_index",
                records=recommendations,
            )
            for flag in flags
            if _flag_code(flag) in _RECOMMENDATION_FLAGS
        ]
        selected = [index for index in indexes if index is not None]
        if not selected:
            selected = list(range(min(len(recommendations), 3)))
        for index in dict.fromkeys(selected):
            if index >= len(recommendations) or not isinstance(recommendations[index], Mapping):
                continue
            item = dict(recommendations[index])
            text = _as_text(item.get("content"))
            add(
                _candidate(
                    sample_id=sample_id,
                    task_type="recommendation_category",
                    candidate_id=f"{sample_id}:recommendation:{index}",
                    source_text=text,
                    evidence_ids=_report_evidence(
                        report_facts, text, f"baseline:{sample_id}:recommendation:{index}"
                    ),
                    rule_output=item,
                    context={"recommendation_index": index, "reason": "category ambiguity"},
                    facility_context=facility_context,
                )
            )

    summary = baseline.get("summary")
    if isinstance(summary, Mapping) and codes & _SUMMARY_FLAGS:
        summary_flags = [flag for flag in flags if _flag_code(flag) in _SUMMARY_FLAGS]
        explicit_fields = {
            _as_text(flag.get("field"))
            or _as_text((flag.get("details") or {}).get("field"))
            for flag in summary_flags
            if isinstance(flag.get("details"), Mapping) or flag.get("field")
        }
        fields = (
            ("overall_conclusion", "conclusion_evidence_selection"),
            ("risk_points", "risk_evidence_selection"),
        )
        if explicit_fields:
            fields = tuple(item for item in fields if item[0] in explicit_fields)
        for field, task_type in fields:
            text = _as_text(summary.get(field))
            if not text:
                continue
            add(
                _candidate(
                    sample_id=sample_id,
                    task_type=task_type,
                    candidate_id=f"{sample_id}:summary:{field}",
                    source_text=text,
                    evidence_ids=_report_evidence(
                        report_facts, text, f"baseline:{sample_id}:summary:{field}"
                    ),
                    rule_output={field: text},
                    context={"summary_field": field, "reason": "summary ambiguity"},
                    facility_context=facility_context,
                )
            )
    return result


__all__ = ["build_semantic_candidates"]
