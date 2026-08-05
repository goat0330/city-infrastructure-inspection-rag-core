"""Conservative final-output normalization for strict exact-match scoring.

The extractor keeps source evidence intact.  This module only removes
presentation-only noise from the public prediction fields after extraction.
It deliberately does not rewrite facts, numbers, dates, categories, or record
counts.
"""

from __future__ import annotations

from dataclasses import replace
import re
from typing import Mapping, Sequence

from ..contracts import InspectionPrediction, Recommendation

_RECOMMENDATION_CATEGORIES = ("立即处置", "尽快维修", "预防性养护")
_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s*条?\s*(?P<category>立即处置|立即维修|尽快维修|预防性养护)"
)
_LEADING_ITEM_RE = re.compile(
    r"^\s*(?:[（(]?\d+[）).、．:]|[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十]+[、.．])\s*"
)
_TRAILING_FIGURE_RE = re.compile(
    r"(?:[，,；;。\s]+)"
    r"(?:详见|参见|见)?\s*(?:照片|图片|附图|图|照)\s*"
    r"[0-9一二三四五六七八九十百千]+(?:[.．\-—_][0-9一二三四五六七八九十百千]+)*"
    r"(?:\s*[、,，及和至~～\-]\s*(?:(?:照片|图片|附图|图|照)\s*)?"
    r"[0-9一二三四五六七八九十百千]+(?:[.．\-—_][0-9一二三四五六七八九十百千]+)*)*"
    r"\s*[）)]?\s*[。．.]?$"
)
_TRAILING_UNIT_NOTE_RE = re.compile(
    r"\s*[（(]\s*单位\s*[:：]?\s*[^）)]{1,16}[）)]\s*$"
)
_DATE_RE = re.compile(
    r"^(?P<year>\d{4})年(?P<month>0?\d{1,2})月(?:(?P<day>0?\d{1,2})日)?$"
)


def _display(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


def normalize_report_date(value: object) -> str:
    """Remove zero padding while preserving the source date granularity."""

    text = _display(value)
    match = _DATE_RE.fullmatch(text)
    if match is None:
        return text
    month = int(match.group("month"))
    day = match.group("day")
    if day is None:
        return f"{match.group('year')}年{month}月"
    return f"{match.group('year')}年{month}月{int(day)}日"


def normalize_defect_description(value: object, *, preserve_figure_refs: bool) -> str:
    """Remove only display-only tails; measured facts remain untouched."""

    text = _display(value)
    text = _TRAILING_UNIT_NOTE_RE.sub("", text).strip("，,；;。． ")
    if not preserve_figure_refs:
        text = _TRAILING_FIGURE_RE.sub("", text).strip("，,；;。． ")
    return text


def normalize_defect_type(value: object) -> str:
    text = _display(value)
    text = re.sub(r"\s*[,，/＋+]\s*", "、", text)
    text = re.sub(r"、+", "、", text)
    return text.strip("、，,；;。． ")


def normalize_recommendation_text(value: object) -> str:
    """Remove duplicate list labels but keep semantic punctuation and wording."""

    text = _display(value)
    return _LEADING_ITEM_RE.sub("", text).strip()


def recommendation_counts(recommendations: Sequence[object]) -> dict[str, int]:
    counts = {category: 0 for category in _RECOMMENDATION_CATEGORIES}
    for recommendation in recommendations:
        category = (
            recommendation.get("category", "")
            if isinstance(recommendation, Mapping)
            else getattr(recommendation, "category", "")
        )
        canonical = "立即处置" if str(category) == "立即维修" else str(category)
        if canonical in counts:
            counts[canonical] += 1
    return counts


def normalize_recommendations_summary(
    recommendations: Sequence[object],
    *,
    source_summary: object = "",
) -> str:
    """Rebuild counts while retaining the report's Gold-facing display style.

    When the source contains a parseable summary, its category omission and
    optional trailing ``建议`` are presentation evidence.  Counts always come
    from the final structured recommendations so the display cannot drift from
    the detail table.  Without source style evidence, all three categories are
    emitted.
    """

    counts = recommendation_counts(recommendations)
    source = _display(source_summary)
    source_categories: list[str] = []
    for match in _COUNT_RE.finditer(source):
        category = match.group("category")
        category = "立即处置" if category == "立即维修" else category
        if category not in source_categories:
            source_categories.append(category)
    categories = source_categories or list(_RECOMMENDATION_CATEGORIES)
    suffix = "建议" if source.endswith("建议") else ""
    return "、".join(f"{counts[category]}条{category}" for category in categories) + suffix


def _facility_type(facility_context: object, prediction: InspectionPrediction) -> str:
    if isinstance(facility_context, Mapping):
        value = facility_context.get("facility_type")
    else:
        value = getattr(facility_context, "facility_type", "")
    if value:
        return str(value)
    identity = f"{prediction.sample_id} {prediction.summary.bridge_name}"
    if any(marker in identity for marker in ("人行通道", "人行地通道", "地下通道", "地通道")):
        return "pedestrian_underpass"
    if "人行天桥" in identity:
        return "pedestrian_overpass"
    return "bridge"


def normalize_prediction_output(
    prediction: InspectionPrediction,
    *,
    facility_context: object = None,
    source_recommendations_summary: object = "",
) -> InspectionPrediction:
    """Return a normalized prediction without changing factual content."""

    facility_type = _facility_type(facility_context, prediction)
    preserve_refs = facility_type in {
        "pedestrian_underpass",
        "pedestrian_passage",
        "vehicle_underpass",
        "underpass",
    }
    defects = tuple(
        replace(
            defect,
            description=normalize_defect_description(
                defect.description,
                preserve_figure_refs=preserve_refs,
            ),
        )
        for defect in prediction.defects
    )
    recommendations = tuple(
        replace(
            recommendation,
            content=normalize_recommendation_text(recommendation.content),
            location=_display(recommendation.location),
        )
        for recommendation in prediction.recommendations
    )
    summary = replace(
        prediction.summary,
        report_date=normalize_report_date(prediction.summary.report_date),
        recommendations_summary=normalize_recommendations_summary(
            recommendations,
            source_summary=source_recommendations_summary
            or prediction.summary.recommendations_summary,
        ),
    )
    return replace(
        prediction,
        summary=summary,
        recommendations=recommendations,
        defects=defects,
    )
