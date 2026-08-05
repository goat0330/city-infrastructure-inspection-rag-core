"""Integrate the deterministic B2 extractors into prediction records."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from time import perf_counter
from typing import Any

from ..contracts import InspectionPrediction
from ..parsing import parse_docx
from ..routing import route_sections
from .defects import DefectExtractionResult, extract_defects
from .recommendations import RecommendationExtractionResult, extract_recommendations
from .summary import SummaryExtraction, extract_summary
from .text_sections import TextSectionExtraction, extract_text_sections


UNIMPLEMENTED_SECTIONS: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportExtraction:
    """One prediction and the non-contract run metadata for one DOCX."""

    prediction: InspectionPrediction
    route_count: int
    quality_flags: tuple[dict[str, object], ...]
    duration_seconds: float

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
    )
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
    return ReportExtraction(
        prediction=prediction,
        route_count=len(routes),
        quality_flags=quality_flags,
        duration_seconds=perf_counter() - started,
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
