"""Adapt prediction records to the stable template rendering contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
import json
from pathlib import Path
from typing import Any

from ..contracts.prediction import InspectionPrediction


_MISSING = "未提取到"
_NONE_VALUES = {"", "无", "未提取到", "none", "null", "n/a", "-"}


def _text(value: object) -> str:
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
        return source.to_dict()
    if isinstance(source, Mapping):
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
    if not isinstance(payload, Mapping):
        raise TypeError("unsupported prediction source")
    return payload


def _comparison_type(summary: Mapping[str, Any]) -> str:
    previous = (
        _text(summary.get("previous_overall_score")),
        _text(summary.get("previous_overall_grade")),
    )
    trend = _text(summary.get("trend"))
    if any(value.casefold() not in _NONE_VALUES for value in previous):
        return "有对比年度"
    if trend.casefold() not in _NONE_VALUES:
        return "有对比年度"
    return "无对比年度"


def _detailed_slots(record: Mapping[str, Any], summary: Mapping[str, Any]) -> tuple[str, str, str, str]:
    values = [_text(value) for value in _items(record.get("detailed_conclusion")) if _text(value)]
    while len(values) < 4:
        values.append("")

    if not values[0]:
        score = _text(summary.get("overall_score"))
        grade = _text(summary.get("overall_grade"))
        if score and score.casefold() not in _NONE_VALUES and grade and grade.casefold() not in _NONE_VALUES:
            values[0] = f"经综合评定，该桥总体技术状况评分为{score}分，总体技术状况等级为{grade}。"
        else:
            values[0] = "该文档无总体技术状况评分和总体技术状况等级。"
    if not values[1]:
        trend = _text(summary.get("trend"))
        values[1] = trend if trend and trend.casefold() not in _NONE_VALUES else _MISSING
    if not values[2]:
        values[2] = _text(summary.get("overall_conclusion")) or _MISSING
    if not values[3]:
        values[3] = _text(summary.get("risk_points")) or _MISSING
    return tuple(values[:4])  # type: ignore[return-value]


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


def build_submission_document(source: object) -> SubmissionDocument:
    """Map one prediction/Gold record to the fixed template field contract."""

    record = _load_record(source)
    summary = _mapping(record.get("summary"))
    bridge_name = _text(summary.get("bridge_name")) or _MISSING
    comparison_type = _comparison_type(summary)
    report_title = f"{bridge_name}·{comparison_type}的信息提取报告"

    scalars = {
        "report_title": report_title,
        "bridge_name": bridge_name,
        "report_date": _text(summary.get("report_date")) or _MISSING,
        "overall_score": _text(summary.get("overall_score")) or _MISSING,
        "overall_grade": _text(summary.get("overall_grade")) or _MISSING,
        "superstructure_score": _text(summary.get("superstructure_score")) or _MISSING,
        "superstructure_grade": _text(summary.get("superstructure_grade")) or _MISSING,
        "substructure_score": _text(summary.get("substructure_score")) or _MISSING,
        "substructure_grade": _text(summary.get("substructure_grade")) or _MISSING,
        "deck_system_score": _text(summary.get("deck_score")) or _MISSING,
        "deck_system_grade": _text(summary.get("deck_grade")) or _MISSING,
        "previous_overall_score": _text(summary.get("previous_overall_score")) or _MISSING,
        "previous_overall_grade": _text(summary.get("previous_overall_grade")) or _MISSING,
        "defect_development_trend": _text(summary.get("trend")) or _MISSING,
        "overall_conclusion": _text(summary.get("overall_conclusion")) or _MISSING,
        "major_risks": _text(summary.get("risk_points")) or _MISSING,
        "recommendations_summary": _text(summary.get("recommendations_summary")) or _MISSING,
    }

    detailed = _detailed_slots(record, summary)
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
