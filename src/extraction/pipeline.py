"""Integrate the deterministic B2 extractors into prediction records."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
import re
from time import perf_counter
from typing import Any, Mapping

from ..contracts import InspectionPrediction, ParagraphBlock
from ..parsing import parse_docx
from ..routing import route_sections
from .defects import DefectExtractionResult, extract_defects
from .recommendations import RecommendationExtractionResult, extract_recommendations
from .recommendations.extractor import summarize_recommendations
from .output_normalizer import (
    normalize_prediction_output,
    normalize_public_summary_output,
    normalize_recommendations_summary,
    normalize_risk_points,
    normalize_narrative_detailed,
)
from .official_answer_composer import compose_official_answers
from .summary import SummaryExtraction, extract_summary
from .summary.facility_context import FacilityContext
from .semantic_candidates import build_semantic_candidates
from .text_sections import TextSectionExtraction, apply_summary_style, extract_text_sections
from .gold_schema_normalizer import (
    canonicalize_defects,
    canonicalize_recommendations,
    compose_gold_overall_conclusion,
    compose_gold_risk_points,
    extract_conclusion_evidence,
    gate_previous_summary,
    normalize_gold_schema_mode,
)


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
    semantic_trace: Mapping[str, Any] = field(default_factory=dict)

    @property
    def quality_flag_codes(self) -> tuple[str, ...]:
        codes: list[str] = []
        for flag in self.quality_flags:
            code = str(flag.get("quality_flag") or flag.get("code") or "")
            if code and code not in codes:
                codes.append(code)
        return tuple(codes)

    def status_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            "sample_id": self.prediction.sample_id,
            "source_file": self.prediction.source_file,
            "status": "succeeded",
            "route_count": self.route_count,
            "quality_flag_codes": list(self.quality_flag_codes),
            "duration_ms": round(self.duration_seconds * 1000, 3),
        }
        if self.semantic_trace:
            record["semantic_trace"] = dict(self.semantic_trace)
        return record


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


_FACT_SECTION_PATTERNS = (
    ("安全性评估", ("安全性评估", "安全评估", "综合评估", "预测评估", "安全影响", "安全分析")),
    ("评估结论", ("评估结论", "检测结论", "总体结论", "结论与建议")),
    ("维护建议", ("维护建议", "养护建议", "维修建议", "处理建议", "处置建议")),
    ("病害原因", ("原因分析", "病害原因", "病害成因", "成因分析")),
    ("历次检测", ("历次检测", "历史检测", "对比分析", "发展状况")),
    ("检测结果", ("检测结果", "外观检查", "技术状况", "评定")),
)


def _infer_fact_section(text: str) -> str:
    for section, patterns in _FACT_SECTION_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return section
    return "other"


def _report_facts(document: object) -> tuple[dict[str, object], ...]:
    """Expose stable, source-local evidence identifiers to semantic candidates."""

    facts: list[dict[str, object]] = []
    for block in getattr(document, "blocks", ()):
        text = " ".join(str(getattr(block, "raw_text", "") or "").split()).strip()
        if not text:
            continue
        block_index = getattr(block, "block_index", len(facts))
        facts.append(
            {
                "evidence_id": f"report:{block_index}",
                "kind": "report_evidence",
                "block_index": block_index,
                "section": _infer_fact_section(text),
                "text": text,
            }
        )
    return tuple(facts)


def _apply_semantic_prediction(
    prediction: InspectionPrediction,
    merged: Mapping[str, Any],
) -> InspectionPrediction:
    """Apply only the semantic fields explicitly allowed by the contract."""

    summary_value = merged.get("summary")
    summary = prediction.summary
    if isinstance(summary_value, Mapping):
        updates: dict[str, str] = {}
        for key in ("overall_conclusion", "risk_points"):
            if key in summary_value and key in summary.__dataclass_fields__:
                updates[key] = str(summary_value[key])
        if updates:
            summary = replace(summary, **updates)

    recommendation_values = merged.get("recommendations")
    recommendations = list(prediction.recommendations)
    if isinstance(recommendation_values, Sequence) and not isinstance(
        recommendation_values,
        (str, bytes, bytearray),
    ):
        for index, recommendation in enumerate(recommendations):
            if index >= len(recommendation_values):
                break
            value = recommendation_values[index]
            if isinstance(value, Mapping) and "category" in value:
                recommendations[index] = replace(
                    recommendation,
                    category=str(value["category"]),
                )

    return replace(
        prediction,
        summary=summary,
        recommendations=tuple(recommendations),
    )


def _text_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    values: list[str] = []
    for item in value:
        text = item.get("text", "") if isinstance(item, Mapping) else item
        text = " ".join(str(text or "").split()).strip()
        if text:
            values.append(text)
    return tuple(values)


def _apply_live_narrative(
    prediction: InspectionPrediction,
    result: Mapping[str, Any],
) -> InspectionPrediction:
    enhanced = result.get("enhanced_prediction")
    field_results = result.get("field_results")
    if not isinstance(enhanced, Mapping) or not isinstance(field_results, Mapping):
        return prediction

    updates: dict[str, tuple[str, ...]] = {}
    for field in ("detailed_conclusion", "causes", "safety_impact"):
        if field_results.get(field) != "enhanced":
            continue
        values = _text_values(enhanced.get(field))
        if values:
            updates[field] = values
    if "detailed_conclusion" in updates:
        updates["detailed_conclusion"] = normalize_narrative_detailed(
            updates["detailed_conclusion"]
        )
    return replace(prediction, **updates)


def _run_live_narrative(
    *,
    baseline_prediction: Mapping[str, Any],
    sample_id: str,
    source_file: str,
    report_facts: Sequence[Mapping[str, Any]],
    client: Any,
    index: Any,
    split: str,
    facility_context: FacilityContext,
    field_states: Mapping[str, str],
) -> dict[str, Any]:
    """Run the evidence-grounded narrative path used by the official live mode."""

    from ..agent.narrative import run_narrative_enhancement

    result = run_narrative_enhancement(
        baseline_prediction,
        sample_id,
        source_file,
        report_facts,
        client,
        retriever=index,
        split=split,
        facility_context=facility_context.to_dict(),
        field_states=dict(field_states),
    )
    hits = [
        dict(item)
        for item in result.get("retrieval_results", ())
        if isinstance(item, Mapping)
    ]
    modes = {str(item.get("retrieval_mode", "")) for item in hits}
    return {
        **dict(result),
        "embedding_reranker_used": "embedding_rerank" in modes,
        "retrieval_count": len(hits),
    }


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
    """Resolve generic or over-captured recommendation locations."""

    generic_locations = {
        "", "其", "该", "此", "本", "该设施", "桥梁",
        "通道", "通道内", facility_noun,
    }
    enriched: list[object] = []
    for recommendation in recommendations:
        location = str(getattr(recommendation, "location", "") or "").strip()
        content = str(getattr(recommendation, "content", "") or "")

        # Normalize common over-captures produced by action/location phrases.
        location = location.replace("箱梁底板", "箱梁底")
        location = re.sub(r"^(?:封闭|修复|维修|处理)(梁体|桥面|栏杆|护栏)$", r"\1", location)
        location = re.sub(r"^(栏杆|护栏)[、,，](?:锈蚀|松动|破损).*$", r"\1", location)
        if location == "护栏" and "护栏混凝土" in content:
            location = "防撞护栏"
        if "台侧墙" in location:
            prefix = location.split("台侧墙", 1)[0] + "台"
            if re.fullmatch(r"\d+#台", prefix):
                location = prefix + "前墙"
            matching_front = [
                str(getattr(defect, "location", "") or "")
                for defect in defects
                if prefix in str(getattr(defect, "location", "") or "")
                and "前墙" in str(getattr(defect, "location", "") or "")
            ]
            if matching_front:
                location = matching_front[0]

        if location in generic_locations:
            if re.search(r"对桥面|桥面(?:布置|增设)排水", content):
                location = "桥面"
            elif "桥上" in content:
                location = "桥上"
            elif "桥梁" in content:
                location = "桥梁"
            else:
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
                if not location or location in generic_locations:
                    location = facility_noun or "该设施"

        if facility_noun == "人行通道" and location in {"人行通道", "该人行通道"}:
            location = "通道"
        enriched.append(replace(recommendation, location=location))
    return tuple(enriched)


def _derive_risk_points(
    summary: SummaryExtraction,
    defects: tuple[object, ...],
    recommendations: tuple[object, ...],
    document: object | None = None,
) -> str:
    """Prefer concise defect→consequence evidence; otherwise derive safely."""

    explicit = any(
        candidate.source_kind in {"major_risk", "risk_label"}
        for candidate in summary.candidates.get("risk_points", ())
    )
    current = normalize_risk_points(summary.summary.risk_points)
    consequence_words = (
        "影响", "降低", "削弱", "危及", "隐患", "安全",
        "耐久", "承载", "受力", "通行", "行车", "行人",
    )
    if explicit and current and any(word in current for word in consequence_words):
        return current
    if not defects:
        return current

    # Use paragraph-level report evidence only.  Whole-table raw text can be an
    # entire compact report and previously polluted the field with contents,
    # inspection purpose and recommendation sections.
    ranked: list[tuple[int, int, str]] = []
    if document is not None:
        for block in getattr(document, "blocks", ()):
            if not isinstance(block, ParagraphBlock):
                continue
            raw = str(getattr(block, "raw_text", "") or "")
            for sentence in re.split(r"(?<=[。；;！？!?])|[\r\n]+", raw):
                text = " ".join(sentence.split()).strip("，,；;。． ")
                if not 16 <= len(text) <= 360:
                    continue
                if any(marker in text for marker in (
                    "检测目的", "进行详细检查", "评估规程", "评定方法",
                    "处理建议", "处置建议", "维修建议", "养护建议",
                    "建议及时", "建议对", "应及时", "需及时",
                )):
                    continue
                if not any(word in text for word in (
                    "裂缝", "破损", "露筋", "锈蚀", "渗水", "泛碱",
                    "变形", "缺失", "堵塞", "脱落", "病害",
                )):
                    continue
                if not any(word in text for word in consequence_words):
                    continue
                score = 0
                if any(marker in text for marker in ("若不及时", "如不及时", "进一步发展", "进一步扩展")):
                    score += 4
                if any(marker in text for marker in ("承载能力", "结构安全", "耐久性", "受力")):
                    score += 2
                if any(marker in text for marker in ("宽度", "mm", "㎜", "条", "处")):
                    score += 1
                ranked.append((-score, len(text), text))
    if ranked:
        selected: list[str] = []
        for _, _, text in sorted(ranked):
            if text not in selected:
                selected.append(text)
            if len(selected) == 3:
                break
        return normalize_risk_points("；".join(selected))

    # No report sentence expresses a defect-to-consequence relation.  Do not
    # manufacture one from the first defect row: unsupported risk prose was a
    # major source of platform consistency false positives.
    return current


def extract_report(
    input_path: str | Path,
    *,
    source_file: str | None = None,
    semantic_enabled: bool = False,
    semantic_client: Any = None,
    semantic_index: Any = None,
    semantic_retriever: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
    semantic_decider: Callable[..., Any] | None = None,
    semantic_decisions: Sequence[Mapping[str, Any]] = (),
    semantic_split: str = "holdout",
    official_composer_enabled: bool = False,
) -> ReportExtraction:
    """Parse one DOCX and assemble the three B2 extractors into a prediction."""

    path = Path(input_path)
    started = perf_counter()
    source_name = _relative_source(path, source_file)
    document = parse_docx(path, source_file=source_name)
    routes = route_sections(document)
    summary: SummaryExtraction = extract_summary(document, routes)
    gold_schema_mode = normalize_gold_schema_mode()
    if gold_schema_mode == "v18":
        summary = gate_previous_summary(summary, document)
    source_recommendations_summary = summary.summary.recommendations_summary
    preserve_figure_refs = summary.facility_context.facility_type in {
        "pedestrian_underpass", "vehicle_underpass", "underpass", "pedestrian_passage"
    }
    defects: DefectExtractionResult = extract_defects(
        document,
        routes,
        preserve_figure_refs=preserve_figure_refs,
    )
    gold_schema_warnings: list[dict[str, object]] = []
    if gold_schema_mode == "v18":
        canonical_defects = canonicalize_defects(defects.records, warnings=gold_schema_warnings)
        if canonical_defects != defects.records:
            defects = replace(defects, records=canonical_defects)
    # Preserve the historical Gold-facing fallback for reports whose cover
    # name is a long project title rather than a facility name.  Recognised
    # non-bridge facilities still carry their inferred noun from the summary.
    recommendation_facility_noun = summary.facility_context.facility_noun or "桥梁"
    recommendations: RecommendationExtractionResult = extract_recommendations(
        document,
        routes,
        infer_categories=True,
        facility_noun=recommendation_facility_noun,
    )
    recommendation_records = _enrich_recommendation_locations(
        recommendations.records,
        defects.records,
        recommendation_facility_noun,
    )
    if recommendation_records != recommendations.records:
        recommendations = replace(recommendations, records=recommendation_records)
    if gold_schema_mode == "v18":
        canonical_recommendations = canonicalize_recommendations(
            recommendations.records,
            facility_noun=recommendation_facility_noun,
        )
        if canonical_recommendations != recommendations.records:
            recommendations = replace(recommendations, records=canonical_recommendations)

    summary_text = summarize_recommendations(
        recommendations.records if recommendations.records else None,
        source_summary=summary.summary.recommendations_summary or None,
    )
    summary_value = replace(
        summary.summary,
        risk_points=_derive_risk_points(
            summary, defects.records, recommendations.records, document
        ),
        recommendations_summary=normalize_recommendations_summary(
            recommendations.records,
            source_summary=source_recommendations_summary,
        ),
    )
    summary_value = apply_summary_style(
        summary_value,
        defects.records,
        facility_context=summary.facility_context,
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
        facility_context=summary.facility_context,
        field_states=summary.field_states,
    )
    document_text = "\n".join(
        str(getattr(block, "raw_text", "") or "")
        for block in getattr(document, "blocks", ())
    )

    # Source-grounded extraction is the production default.  The deterministic
    # composer remains available only for explicit A/B experiments; it must not
    # silently overwrite six final fields without a full evaluation gate.
    if official_composer_enabled:
        official = compose_official_answers(
            summary=summary.summary,
            defects=defects.records,
            recommendations=recommendations.records,
            facility_context=summary.facility_context,
            source_causes=text_sections.causes,
            document_text=document_text,
        )
        summary_value = replace(
            summary.summary,
            overall_conclusion=official.overall_conclusion,
            risk_points=official.risk_points,
        )
        detailed_conclusion = official.detailed_conclusion
        causes = official.causes
        treatments = official.treatments
        safety_impact = official.safety_impact
    else:
        summary_value = summary.summary
        detailed_conclusion = text_sections.detailed_conclusion
        causes = text_sections.causes
        treatments = text_sections.treatments
        safety_impact = text_sections.safety_impact

    field_states["overall_conclusion"] = (
        "present" if summary_value.overall_conclusion else field_states.get("overall_conclusion", "not_extracted")
    )
    field_states["risk_points"] = (
        "present" if summary_value.risk_points else field_states.get("risk_points", "not_extracted")
    )
    summary = replace(summary, summary=summary_value, field_states=field_states)

    prediction = InspectionPrediction(
        sample_id=_sample_id(source_name),
        source_file=source_name,
        summary=summary.summary,
        detailed_conclusion=detailed_conclusion,
        recommendations=recommendations.records,
        defects=defects.records,
        causes=causes,
        treatments=treatments,
        safety_impact=safety_impact,
    )
    prediction = normalize_prediction_output(
        prediction,
        facility_context=summary.facility_context,
        source_recommendations_summary=source_recommendations_summary,
    )
    quality_flags = (
        *_flags("summary", summary.quality_flags),
        *_flags("defects", defects.quality_flags),
        *_flags("recommendations", recommendations.quality_flags),
        *_flags("gold_schema", gold_schema_warnings),
    )
    if summary_text.get("conflict"):
        quality_flags += _flags("recommendations", summary_text.get("diagnostics"))
    semantic_trace: dict[str, Any] = {}
    if semantic_enabled:
        # The official live path owns narrative fields.  The older candidate
        # graph remains available for injected A/B tests only.
        from .semantic_merge import merge_semantic_predictions

        if semantic_client is not None and semantic_index is not None:
            narrative = _run_live_narrative(
                baseline_prediction=prediction.to_dict(),
                sample_id=prediction.sample_id,
                source_file=prediction.source_file,
                report_facts=_report_facts(document),
                client=semantic_client,
                index=semantic_index,
                split=semantic_split,
                facility_context=summary.facility_context,
                field_states=summary.field_states,
            )
            prediction = _apply_live_narrative(prediction, narrative)
            narrative_hits = [
                item
                for item in narrative.get("retrieval_results", ())
                if isinstance(item, Mapping)
            ]
            semantic_trace["narrative"] = {
                "field_results": dict(narrative.get("field_results", {})),
                "selection_reasons": dict(narrative.get("selection_reasons", {})),
                "used_fallback": bool(narrative.get("used_fallback")),
                "validation_errors": list(narrative.get("validation_errors", [])),
                "field_fallbacks": list(narrative.get("field_fallbacks", [])),
                "retrieval_count": int(narrative.get("retrieval_count", 0) or 0),
                "embedding_reranker_used": bool(
                    narrative.get("embedding_reranker_used")
                    or any(
                        str(item.get("retrieval_mode", "")) == "embedding_rerank"
                        for item in narrative_hits
                    )
                ),
                "retrieval_results": narrative_hits,
                "retrieval_by_task": dict(narrative.get("retrieval_by_task", {})),
                "call_metrics": dict(narrative.get("call_metrics", {})),
            }

        legacy_semantic_requested = bool(
            semantic_decisions or semantic_retriever is not None or semantic_decider is not None
        )
        if legacy_semantic_requested:
            candidate_baseline = prediction.to_dict()
            candidate_baseline["facility_context"] = summary.facility_context.to_dict()
            candidates = build_semantic_candidates(
                candidate_baseline,
                {"quality_flags": quality_flags},
                _report_facts(document),
            )
            merged, candidate_trace = merge_semantic_predictions(
                prediction.to_dict(),
                [candidate.to_dict() for candidate in candidates],
                semantic_decisions,
                semantic_enabled=True,
                retriever=semantic_retriever,
                decider=semantic_decider,
                index=semantic_index,
                client=semantic_client,
                split=semantic_split,
            )
            prediction = _apply_semantic_prediction(prediction, merged)
            prediction = normalize_prediction_output(
                prediction,
                facility_context=summary.facility_context,
                source_recommendations_summary=source_recommendations_summary,
            )
            field_states.update(
                {
                    str(key): str(value)
                    for key, value in candidate_trace.get("field_states", {}).items()
                }
            )
            semantic_trace.update(candidate_trace)

    # V16 experiment B: clean only the public concise-summary fields after
    # narrative generation, so Qwen/RAG sees the exact V15 baseline input.
    prediction = normalize_public_summary_output(prediction)
    if gold_schema_mode == "v18":
        conclusion_evidence = extract_conclusion_evidence(document)
        prediction = replace(
            prediction,
            summary=replace(
                prediction.summary,
                overall_conclusion=compose_gold_overall_conclusion(
                    prediction.summary,
                    prediction.defects,
                    facility_noun=summary.facility_context.facility_noun or "桥梁",
                    facility_name=prediction.summary.bridge_name,
                    evidence_texts=conclusion_evidence,
                ),
                risk_points=compose_gold_risk_points(
                    prediction.summary.risk_points, prediction.defects
                ),
            ),
        )
    return ReportExtraction(
        prediction=prediction,
        route_count=len(routes),
        quality_flags=quality_flags,
        duration_seconds=perf_counter() - started,
        facility_context=summary.facility_context,
        field_states=field_states,
        semantic_trace=semantic_trace,
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
    semantic_enabled: bool = False,
    semantic_client: Any = None,
    semantic_index: Any = None,
    semantic_retriever: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
    semantic_decider: Callable[..., Any] | None = None,
    semantic_decisions: Sequence[Mapping[str, Any]] = (),
    semantic_split: str = "holdout",
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
                result = extract_report(
                    path,
                    source_file=path.relative_to(root).as_posix(),
                    semantic_enabled=semantic_enabled,
                    semantic_client=semantic_client,
                    semantic_index=semantic_index,
                    semantic_retriever=semantic_retriever,
                    semantic_decider=semantic_decider,
                    semantic_decisions=semantic_decisions,
                    semantic_split=semantic_split,
                )
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
