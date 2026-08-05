"""Integrate the deterministic B2 extractors into prediction records."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path, PurePosixPath
import re
from time import perf_counter
from typing import Any, Mapping

from ..contracts import InspectionPrediction
from ..parsing import parse_docx
from ..routing import route_sections
from .defects import DefectExtractionResult, extract_defects
from .recommendations import RecommendationExtractionResult, extract_recommendations
from .recommendations.extractor import summarize_recommendations
from .summary import SummaryExtraction, extract_summary
from .summary.facility_context import FacilityContext
from .text_sections import TextSectionExtraction, extract_text_sections


UNIMPLEMENTED_SECTIONS: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportExtraction:
    """One prediction and the non-contract run metadata for one DOCX."""

    prediction: InspectionPrediction
    route_count: int
    quality_flags: tuple[dict[str, object], ...]
    duration_seconds: float
    facility_context: FacilityContext = field(default_factory=FacilityContext)
    field_states: Mapping[str, str] = field(default_factory=dict)

    @property
    def quality_flag_codes(self) -> tuple[str, ...]:
        codes: list[str] = []
        for flag in self.quality_flags:
            code = str(flag.get("quality_flag") or flag.get("code") or "")
            if code and code not in codes:
                codes.append(code)
        return tuple(codes)

    def status_record(self) -> dict[str, object]:
        return {
            "sample_id": self.prediction.sample_id,
            "source_file": self.prediction.source_file,
            "status": "succeeded",
            "route_count": self.route_count,
            "quality_flag_codes": list(self.quality_flag_codes),
            "duration_ms": round(self.duration_seconds * 1000, 3),
        }


def _relative_source(input_path: Path, source_file: str | None) -> str:
    value = source_file or input_path.name
    return PurePosixPath(str(value).replace("\\", "/")).as_posix()


def _sample_id(source_file: str) -> str:
    path = PurePosixPath(source_file)
    return str(path.with_suffix(""))


def _flags(stage: str, values: object) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for value in values if isinstance(values, (list, tuple)) else ():
        if not isinstance(value, dict):
            continue
        flag = dict(value)
        flag["stage"] = stage
        result.append(flag)
    return tuple(result)


def _normalise_risk_location(value: str) -> str:
    """Use the Gold-facing component name for a grouped risk statement."""

    value = " ".join((value or "").split()).strip("，,；;。．")
    if value.endswith("侧墙") and value[: -len("侧墙")] in {"左", "右"}:
        return "侧墙"
    return value


def _enrich_recommendation_locations(
    recommendations: tuple[object, ...],
    defects: tuple[object, ...],
    facility_noun: str,
) -> tuple[object, ...]:
    """Resolve pronoun-only recommendation locations from matching defects."""

    generic_locations = {
        "",
        "其",
        "该",
        "此",
        "本",
        "该设施",
        "桥梁",
        "通道",
        "通道内",
        facility_noun,
    }
    enriched: list[object] = []
    for recommendation in recommendations:
        location = str(getattr(recommendation, "location", "") or "").strip()
        content = str(getattr(recommendation, "content", "") or "")
        if location not in generic_locations:
            enriched.append(recommendation)
            continue
        matching = [
            defect
            for defect in defects
            if str(getattr(defect, "defect_type", "") or "")
            and str(getattr(defect, "defect_type", "")) in content
        ]
        if matching:
            location = _normalise_risk_location(
                str(getattr(matching[0], "location", "") or "")
            )
        if not location:
            location = facility_noun or "该设施"
        enriched.append(replace(recommendation, location=location))
    return tuple(enriched)


def _derive_risk_points(
    summary: SummaryExtraction,
    defects: tuple[object, ...],
    recommendations: tuple[object, ...],
) -> str:
    """Create a short deterministic risk statement when no explicit one exists."""

    explicit = any(
        candidate.source_kind in {"major_risk", "risk_label"}
        for candidate in summary.candidates.get("risk_points", ())
    )
    if explicit and summary.summary.risk_points:
        return summary.summary.risk_points
    if not defects:
        return summary.summary.risk_points

    by_location: dict[str, list[object]] = {}
    for defect in defects:
        location = _normalise_risk_location(str(getattr(defect, "location", "") or ""))
        if location:
            by_location.setdefault(location, []).append(defect)
    for location, grouped in by_location.items():
        types = {str(getattr(item, "defect_type", "") or "") for item in grouped}
        if "破损" in types and "裂缝" in types:
            return f"{location}局部破损及竖向裂缝，需及时封闭补强以防进一步发展。"

    first = defects[0]
    description = str(getattr(first, "description", "") or "")
    description = re.sub(r"[，,]\s*(?:见图|照片|附图).*$", "", description).strip("，,；;。．")
    description = description.replace("，", "")
    if description:
        if any("修" in str(getattr(item, "content", "")) for item in recommendations):
            return f"{description}，需及时修复以防进一步损伤。"
        return f"{description}，需及时处置。"
    return summary.summary.risk_points


def extract_report(input_path: str | Path, *, source_file: str | None = None) -> ReportExtraction:
    """Parse one DOCX and assemble the three B2 extractors into a prediction."""

    path = Path(input_path)
    started = perf_counter()
    source_name = _relative_source(path, source_file)
    document = parse_docx(path, source_file=source_name)
    routes = route_sections(document)
    summary: SummaryExtraction = extract_summary(document, routes)
    defects: DefectExtractionResult = extract_defects(document, routes)
    recommendations: RecommendationExtractionResult = extract_recommendations(
        document,
        routes,
        infer_categories=True,
        facility_noun=summary.facility_context.facility_noun,
    )
    recommendation_records = _enrich_recommendation_locations(
        recommendations.records,
        defects.records,
        summary.facility_context.facility_noun,
    )
    if recommendation_records != recommendations.records:
        recommendations = replace(recommendations, records=recommendation_records)

    summary_text = summarize_recommendations(
        recommendations.records if recommendations.records else None,
        source_summary=summary.summary.recommendations_summary or None,
    )
    summary_value = replace(
        summary.summary,
        risk_points=_derive_risk_points(summary, defects.records, recommendations.records),
        recommendations_summary=str(summary_text["summary"]),
    )
    field_states = dict(summary.field_states)
    field_states["recommendations_summary"] = "present"
    if summary_value.risk_points:
        field_states["risk_points"] = "present"
    summary = replace(summary, summary=summary_value, field_states=field_states)
    text_sections: TextSectionExtraction = extract_text_sections(
        document,
        routes,
        recommendations.records,
        summary.summary,
        defects.records,
    )

    prediction = InspectionPrediction(
        sample_id=_sample_id(source_name),
        source_file=source_name,
        summary=summary.summary,
        detailed_conclusion=text_sections.detailed_conclusion,
        recommendations=recommendations.records,
        defects=defects.records,
        causes=text_sections.causes,
        treatments=text_sections.treatments,
        safety_impact=text_sections.safety_impact,
    )
    quality_flags = (
        *_flags("summary", summary.quality_flags),
        *_flags("defects", defects.quality_flags),
        *_flags("recommendations", recommendations.quality_flags),
    )
    if summary_text.get("conflict"):
        quality_flags += _flags("recommendations", summary_text.get("diagnostics"))
    return ReportExtraction(
        prediction=prediction,
        route_count=len(routes),
        quality_flags=quality_flags,
        duration_seconds=perf_counter() - started,
        facility_context=summary.facility_context,
        field_states=field_states,
    )


def _safe_error(error: Exception) -> str:
    message = " ".join(str(error).split())
    message = re.sub(r"[A-Za-z]:[\\/][^ ]*", "<input>", message)
    return message[:300]


def _failed_status(path: Path, input_dir: Path, error: Exception, duration: float) -> dict[str, object]:
    source_file = path.relative_to(input_dir).as_posix()
    return {
        "sample_id": _sample_id(source_file),
        "source_file": source_file,
        "status": "failed",
        "error_type": type(error).__name__,
        "error": _safe_error(error),
        "duration_ms": round(duration * 1000, 3),
    }


def predict_batch(
    input_dir: str | Path,
    output_path: str | Path,
    *,
    report_path: str | Path | None = None,
) -> dict[str, object]:
    """Write successful prediction records as JSONL and all statuses as a sidecar."""

    root = Path(input_dir)
    if not root.is_dir():
        raise NotADirectoryError(f"input directory does not exist: {root}")
    output = Path(output_path)
    report = Path(report_path) if report_path is not None else output.with_suffix(".report.json")
    paths = sorted(root.rglob("*.docx"), key=lambda item: item.relative_to(root).as_posix())
    statuses: list[dict[str, object]] = []
    success_count = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for path in paths:
            started = perf_counter()
            try:
                result = extract_report(path, source_file=path.relative_to(root).as_posix())
            except Exception as error:  # one bad report must not stop the batch
                statuses.append(_failed_status(path, root, error, perf_counter() - started))
                continue
            stream.write(json.dumps(result.prediction.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            statuses.append(result.status_record())
            success_count += 1

    payload: dict[str, object] = {
        "version": "prediction-run-v1",
        "status": "succeeded" if success_count == len(paths) else "partial",
        "input_count": len(paths),
        "prediction_count": success_count,
        "failed_count": len(paths) - success_count,
        "output": str(output),
        "unimplemented_sections": list(UNIMPLEMENTED_SECTIONS),
        "records": statuses,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload["report"] = str(report)
    return payload
