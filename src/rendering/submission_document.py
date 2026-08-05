"""Adapt prediction records to the stable template rendering contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any

from ..contracts.prediction import InspectionPrediction


_MISSING = "未提取到"
_NONE_VALUES = {"", "无", "未提取到", "none", "null", "n/a", "-"}
_EXPLICIT_NONE_STATES = {"explicit_none", "not_applicable"}
_NOT_EXTRACTED_STATES = {"not_extracted"}
_RECOMMENDATION_SUMMARY_CATEGORIES = ("立即处置", "尽快维修", "预防性养护")


def _text(value: object) -> str:
    if isinstance(value, Enum):
        value = value.value
    return "" if value is None else str(value).strip()


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value):
        return asdict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, Mapping):
            return converted
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        return attributes
    return {}


def _items(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _load_record(source: object) -> Mapping[str, Any]:
    if isinstance(source, InspectionPrediction):
        payload = source.to_dict()
        for name in ("facility_context", "field_states"):
            value = getattr(source, name, None)
            if value is not None:
                payload[name] = value
    elif isinstance(source, Mapping):
        payload: object = source
    elif isinstance(source, Path):
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    elif isinstance(source, str):
        stripped = source.lstrip()
        payload = json.loads(source) if stripped.startswith(("{", "[")) else json.loads(
            Path(source).read_text(encoding="utf-8-sig")
        )
    else:
        payload = _mapping(source)

    if isinstance(payload, list):
        if len(payload) != 1:
            raise ValueError("template rendering requires exactly one prediction record")
        payload = payload[0]
    if isinstance(payload, Mapping) and "records" in payload and "summary" not in payload:
        records = payload.get("records")
        if not isinstance(records, list) or len(records) != 1:
            raise ValueError("wrapped JSON must contain exactly one record")
        payload = records[0]
    if isinstance(payload, Mapping) and "prediction" in payload and "summary" not in payload:
        prediction = _mapping(payload.get("prediction"))
        if prediction:
            merged = dict(prediction)
            for name in ("facility_context", "field_states"):
                if name in payload:
                    merged[name] = payload[name]
            payload = merged
    if not isinstance(payload, Mapping):
        raise TypeError("unsupported prediction source")
    return payload


def _normalize_state(value: object) -> str:
    return _text(value).casefold().replace("-", "_").replace(" ", "_")


def _state_from_entry(value: object) -> str:
    if isinstance(value, Enum):
        return _normalize_state(value.value)
    if isinstance(value, str):
        return _normalize_state(value)
    mapped = _mapping(value)
    for key in ("state", "status", "value_state", "availability"):
        if key in mapped:
            return _normalize_state(mapped[key])
    return ""


def _field_state(field_states: object, aliases: Sequence[str]) -> str:
    states = _mapping(field_states)
    if not states:
        return ""
    for alias in aliases:
        for key in (alias, f"summary.{alias}", f"scalars.{alias}"):
            if key in states:
                return _state_from_entry(states[key])
    for section in ("summary", "scalars"):
        nested = _mapping(states.get(section))
        for alias in aliases:
            if alias in nested:
                return _state_from_entry(nested[alias])
    return ""


def _lookup(mapping: Mapping[str, Any], aliases: Sequence[str]) -> tuple[object, bool]:
    for alias in aliases:
        if alias in mapping:
            return mapping[alias], True
    return None, False


def _value_and_embedded_state(value: object) -> tuple[object, str]:
    mapped = _mapping(value)
    if not mapped:
        return value, ""
    embedded_state = _state_from_entry(mapped)
    for key in ("value", "text", "raw_value"):
        if key in mapped:
            return mapped[key], embedded_state
    return value, embedded_state


def _display_value(value: object, state: object = "") -> str:
    normalized = _normalize_state(state)
    if normalized in _EXPLICIT_NONE_STATES:
        return "无"
    if normalized in _NOT_EXTRACTED_STATES:
        return _MISSING
    text = _text(value)
    if normalized == "present":
        return text
    return text or _MISSING


def _summary_value(
    summary: Mapping[str, Any],
    aliases: Sequence[str],
    field_states: object,
) -> str:
    raw, _ = _lookup(summary, aliases)
    raw, embedded_state = _value_and_embedded_state(raw)
    state = _field_state(field_states, aliases) or embedded_state
    return _display_value(raw, state)


def _metadata(record: Mapping[str, Any], name: str, override: object = None) -> object:
    if override is not None:
        return override
    if name in record:
        return record[name]
    metadata = _mapping(record.get("metadata"))
    return metadata.get(name)


def _subject_text(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    if text.startswith(("该", "此", "本")):
        return text
    return f"该{text}"


def _facility_subject(record: Mapping[str, Any], facility_context: object = None) -> str:
    candidates: list[object] = []
    if facility_context is not None:
        candidates.append(facility_context)
    for key in ("facility_context", "facility", "context"):
        if key in record:
            candidates.append(record[key])
    summary = _mapping(record.get("summary"))
    if "facility_context" in summary:
        candidates.append(summary["facility_context"])
    metadata = _mapping(record.get("metadata"))
    if "facility_context" in metadata:
        candidates.append(metadata["facility_context"])
    for candidate in candidates:
        context = _mapping(candidate)
        for key in ("facility_noun", "subject"):
            if key in context:
                subject = _subject_text(context[key])
                if subject:
                    return subject
        if isinstance(candidate, str):
            subject = _subject_text(candidate)
            if subject:
                return subject
    for key in ("facility_noun", "subject"):
        if key in record:
            subject = _subject_text(record[key])
            if subject:
                return subject
    return "该设施"


def _comparison_type(summary: Mapping[str, Any], field_states: object = None) -> str:
    previous = (
        _summary_value(summary, ("previous_overall_score",), field_states),
        _summary_value(summary, ("previous_overall_grade",), field_states),
    )
    trend = _summary_value(summary, ("trend", "defect_development_trend"), field_states)
    if any(value.casefold() not in _NONE_VALUES for value in previous):
        return "有对比年度"
    if trend.casefold() not in _NONE_VALUES:
        return "有对比年度"
    return "无对比年度"


def _detailed_slots(
    record: Mapping[str, Any],
    summary: Mapping[str, Any],
    field_states: object,
    subject: str,
) -> tuple[str, str, str, str]:
    narrative = _mapping(record.get("narrative"))
    detailed_source = (
        narrative["detailed_conclusion"]
        if "detailed_conclusion" in narrative
        else record.get("detailed_conclusion")
    )
    values = [_text(value) for value in _items(detailed_source) if _text(value)]
    while len(values) < 4:
        values.append("")

    if not values[0]:
        score = _summary_value(summary, ("overall_score",), field_states)
        grade = _summary_value(summary, ("overall_grade",), field_states)
        if score and score.casefold() not in _NONE_VALUES and grade and grade.casefold() not in _NONE_VALUES:
            values[0] = f"经综合评定，{subject}总体技术状况评分为{score}分，总体技术状况等级为{grade}。"
        else:
            values[0] = "该文档无总体技术状况评分和总体技术状况等级。"
    if not values[1]:
        trend = _summary_value(summary, ("trend", "defect_development_trend"), field_states)
        values[1] = trend or _MISSING
    if not values[2]:
        values[2] = _summary_value(summary, ("overall_conclusion",), field_states)
    if not values[3]:
        values[3] = _summary_value(summary, ("risk_points", "major_risks"), field_states)
    return tuple(values[:4])  # type: ignore[return-value]


def _recommendation_summary(
    record: Mapping[str, Any],
    summary: Mapping[str, Any],
    field_states: object,
) -> str:
    raw, found = _lookup(summary, ("recommendations_summary",))
    if not found and "recommendations_summary" in record:
        raw = record["recommendations_summary"]
        found = True
    raw, embedded_state = _value_and_embedded_state(raw)
    state = _field_state(field_states, ("recommendations_summary",)) or embedded_state
    normalized_state = _normalize_state(state)
    if normalized_state in _EXPLICIT_NONE_STATES | _NOT_EXTRACTED_STATES:
        return _display_value(raw, state)
    text = _text(raw)
    if normalized_state == "present" or text:
        return text if normalized_state == "present" else text
    if "recommendations" not in record:
        return _MISSING
    counts = dict.fromkeys(_RECOMMENDATION_SUMMARY_CATEGORIES, 0)
    for item in _items(record.get("recommendations")):
        category = _text(_mapping(item).get("category"))
        if "立即" in category:
            counts["立即处置"] += 1
        elif "尽快" in category:
            counts["尽快维修"] += 1
        elif "预防" in category:
            counts["预防性养护"] += 1
    return "、".join(
        f"{counts[category]}条{category}" for category in _RECOMMENDATION_SUMMARY_CATEGORIES
    )


@dataclass(frozen=True)
class SubmissionDocument:
    """Content-only object consumed by the DOCX template renderer."""

    scalars: Mapping[str, str]
    score_and_grade: str
    history_and_defects: str
    current_structure_state: str
    comprehensive_judgement: str
    recommendations: tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    defects: tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    causes: tuple[str, ...] = field(default_factory=tuple)
    treatments: tuple[str, ...] = field(default_factory=tuple)
    safety_impacts: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "scalars": dict(self.scalars),
            "score_and_grade": self.score_and_grade,
            "history_and_defects": self.history_and_defects,
            "current_structure_state": self.current_structure_state,
            "comprehensive_judgement": self.comprehensive_judgement,
            "recommendations": [dict(item) for item in self.recommendations],
            "defects": [dict(item) for item in self.defects],
            "causes": list(self.causes),
            "treatments": list(self.treatments),
            "safety_impacts": list(self.safety_impacts),
        }


def build_submission_document(
    source: object,
    *,
    facility_context: object = None,
    field_states: object = None,
) -> SubmissionDocument:
    """Map one prediction/Gold record to the fixed template field contract."""

    record = _load_record(source)
    summary = _mapping(record.get("summary"))
    states = _metadata(record, "field_states", field_states)
    if states is None:
        states = summary.get("field_states")
    subject = _facility_subject(record, _metadata(record, "facility_context", facility_context))
    bridge_name = _summary_value(summary, ("bridge_name",), states)
    comparison_type = _comparison_type(summary, states)
    report_title = f"{bridge_name}·{comparison_type}的信息提取报告"

    scalars = {
        "report_title": report_title,
        "bridge_name": bridge_name,
        "report_date": _summary_value(summary, ("report_date",), states),
        "overall_score": _summary_value(summary, ("overall_score",), states),
        "overall_grade": _summary_value(summary, ("overall_grade",), states),
        "superstructure_score": _summary_value(summary, ("superstructure_score",), states),
        "superstructure_grade": _summary_value(summary, ("superstructure_grade",), states),
        "substructure_score": _summary_value(summary, ("substructure_score",), states),
        "substructure_grade": _summary_value(summary, ("substructure_grade",), states),
        "deck_system_score": _summary_value(summary, ("deck_system_score", "deck_score"), states),
        "deck_system_grade": _summary_value(summary, ("deck_system_grade", "deck_grade"), states),
        "previous_overall_score": _summary_value(summary, ("previous_overall_score",), states),
        "previous_overall_grade": _summary_value(summary, ("previous_overall_grade",), states),
        "defect_development_trend": _summary_value(summary, ("trend", "defect_development_trend"), states),
        "overall_conclusion": _summary_value(summary, ("overall_conclusion",), states),
        "major_risks": _summary_value(summary, ("risk_points", "major_risks"), states),
        "recommendations_summary": _recommendation_summary(record, summary, states),
    }

    detailed = _detailed_slots(record, summary, states, subject)
    recommendations: list[Mapping[str, str]] = []
    for item in _items(record.get("recommendations")):
        value = _mapping(item)
        recommendations.append({
            "index": _text(value.get("index")),
            "category": _text(value.get("category")) or _MISSING,
            "content": _text(value.get("content")) or _MISSING,
            "location": _text(value.get("location")) or _MISSING,
        })

    defects: list[Mapping[str, str]] = []
    for item in _items(record.get("defects")):
        value = _mapping(item)
        defects.append({
            "index": _text(value.get("index")),
            "location": _text(value.get("location")) or _MISSING,
            "type": _text(value.get("defect_type", value.get("type"))) or _MISSING,
            "description": _text(value.get("description")) or _MISSING,
            "is_new": _text(value.get("is_new")) or _MISSING,
            "previous_status": _text(value.get("previous_status")) or _MISSING,
            "development_degree": _text(value.get("development", value.get("development_degree"))) or _MISSING,
        })

    def text_items(name: str) -> tuple[str, ...]:
        return tuple(_text(item) for item in _items(record.get(name)) if _text(item))

    return SubmissionDocument(
        scalars=scalars,
        score_and_grade=detailed[0],
        history_and_defects=detailed[1],
        current_structure_state=detailed[2],
        comprehensive_judgement=detailed[3],
        recommendations=tuple(recommendations),
        defects=tuple(defects),
        causes=text_items("causes"),
        treatments=text_items("treatments"),
        safety_impacts=text_items("safety_impact"),
    )
