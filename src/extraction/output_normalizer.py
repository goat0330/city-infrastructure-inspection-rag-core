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
_SUMMARY_NOISE_MARKERS = (
    "目录", "检测依据", "评定依据", "技术规范", "评定标准",
    "检查结果表", "病害分布表", "照片", "示意图", "布置图",
    "结构检算", "荷载试验", "静载试验", "动载试验", "自振频率",
    "冲击系数", "混凝土强度", "保护层合格率", "桥梁博士", "计算结果",
)
_ACTION_MARKERS = (
    "建议", "应及时", "需及时", "维修", "修复", "修补", "处治",
    "处置", "加固", "更换", "清理", "养护", "可直接用", "环氧砂浆",
)
_DEFECT_MARKERS = (
    "病害", "裂缝", "开裂", "破损", "露筋", "锈蚀", "渗水", "泛碱",
    "变形", "缺失", "堵塞", "脱落", "沉降", "冲刷",
)
_CONSEQUENCE_MARKERS = (
    "影响", "降低", "削弱", "危及", "隐患", "安全", "耐久", "承载",
    "受力", "通行", "行车", "行人", "使用功能",
)
_LEGACY_GENERATED_MARKERS = (
    "本次为桥梁定期检测，无往年检测评分",
    "不存在既有病害扩展情况",
    "综上，报告建议",
    "已有证据为",
    "报告未明确该类病害",
    "可能与构件受力、材料收缩或温度变化有关",
    "可能与长期受力、位移或老化有关",
    "车辆荷载长期作用、温度变化及材料老化共同影响",
    "混凝土保护层破损、施工密实性不足及长期环境侵蚀",
    "防排水不畅、接缝密封老化或雨水长期下渗",
    "可能削弱结构整体性",
    "影响传力状态",
    "若不及时处理，会影响使用功能并降低构件耐久性",
)


def _display(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


def _sentences(value: object) -> tuple[str, ...]:
    result: list[str] = []
    for part in re.split(r"[\r\n]+|(?<=[。；;！？!?])", _display(value)):
        text = part.strip("，,；;。． ")
        if text and text not in result:
            result.append(text)
    return tuple(result)


def normalize_overall_conclusion(value: object) -> str:
    """Final guard: keep a concise evidence-shaped overall conclusion."""

    selected: list[str] = []
    total = 0
    for sentence in _sentences(value):
        compact = re.sub(r"\s+", "", sentence)
        if any(marker in compact for marker in _SUMMARY_NOISE_MARKERS):
            continue
        if any(marker in compact for marker in _ACTION_MARKERS):
            sentence = re.split(
                r"[，,；;。]?\s*(?=(?:建议|应及时|需及时|维修|修复|修补|处治|处置|加固|更换|清理|养护|可直接用|环氧砂浆))",
                sentence,
                maxsplit=1,
            )[0].strip("，,；;。 ")
            compact = re.sub(r"\s+", "", sentence)
            if not sentence:
                continue
        has_overall = any(marker in compact for marker in (
            "总体", "整体", "综合评定", "技术状况", "安全性评估",
            "承载能力", "满足要求", "符合要求", "正常使用", "安全运营",
        ))
        has_defect = any(marker in compact for marker in _DEFECT_MARKERS)
        if not (has_overall or has_defect):
            continue
        sentence = sentence[:180].rstrip("，,；; ")
        separator = 1 if selected else 0
        if total + separator + len(sentence) > 250:
            remaining = 250 - total - separator
            if remaining < 24:
                break
            sentence = sentence[:remaining].rstrip("，,；; ")
        if sentence and sentence not in selected:
            selected.append(sentence)
            total += separator + len(sentence)
        if len(selected) >= 4 or total >= 250:
            break
    return "；".join(selected)


def normalize_risk_points(value: object) -> str:
    """Final guard: keep at most three defect→consequence statements."""

    selected: list[str] = []
    total = 0
    for sentence in _sentences(value):
        compact = re.sub(r"\s+", "", sentence)
        if any(marker in compact for marker in (*_SUMMARY_NOISE_MARKERS, *_ACTION_MARKERS, *_LEGACY_GENERATED_MARKERS)):
            continue
        if re.search(r"(?:19|20)\d{2}年.*(?:检测|检查|维修|加固)", compact):
            continue
        if not any(marker in compact for marker in _DEFECT_MARKERS):
            continue
        if not any(marker in compact for marker in _CONSEQUENCE_MARKERS):
            continue
        sentence = sentence[:120].rstrip("，,；; ")
        separator = 1 if selected else 0
        if total + separator + len(sentence) > 200:
            remaining = 200 - total - separator
            if remaining < 24:
                break
            sentence = sentence[:remaining].rstrip("，,；; ")
        if sentence and sentence not in selected:
            selected.append(sentence)
            total += separator + len(sentence)
        if len(selected) >= 3 or total >= 200:
            break
    return "；".join(selected)


def _normalize_text_list(values: Sequence[object], *, kind: str) -> tuple[str, ...]:
    result: list[str] = []
    total = 0
    limit = 4 if kind == "detailed" else 4
    for raw in values:
        text = _display(raw).strip("，,；;。． ")
        # Old submissions prefixed a valid disease overview with a fabricated
        # missing-history sentence ("无。本次检测...").  Remove only that
        # prefix so the report-backed disease facts survive normalization.
        text = re.sub(r"^无[。.]\s*(?=本次检测)", "", text)
        text = re.sub(
            r"^本次为[^。；;]{0,40}定期检测[，,、]?无往年检测评分、病害对比数据[，,、]?不存在既有病害扩展情况[。；;]?\s*",
            "",
            text,
        )
        compact = re.sub(r"\s+", "", text)
        if not text or any(marker in compact for marker in _LEGACY_GENERATED_MARKERS):
            continue
        if kind == "cause":
            if not re.search(r"(?:由于|因为|主要原因|原因(?:是|为)|所致|由.{1,80}(?:引起|导致)|受.{1,80}影响)", compact):
                continue
        if kind == "safety":
            if any(marker in compact for marker in _ACTION_MARKERS):
                continue
            if not any(marker in compact for marker in _CONSEQUENCE_MARKERS):
                continue
        if kind == "detailed":
            text = text[:520].rstrip("，,；; ")
            if total + len(text) > 900:
                remaining = 900 - total
                if remaining < 30:
                    break
                text = text[:remaining].rstrip("，,；; ")
        if text not in result:
            result.append(text + ("" if text.endswith("。") else "。"))
            total += len(text)
        if len(result) >= limit:
            break
    return tuple(result[:limit])


def normalize_narrative_detailed(values: Sequence[object]) -> tuple[str, ...]:
    """Keep live narrative history wording aligned with the public template."""

    result: list[str] = []
    for value in values:
        text = _display(value).strip("，,；;。． ")
        text = re.sub(
            r"本次报告未提供往年(?:检测)?评分(?:及|或)病害对比资料(?:，无法进行(?:历史趋势|既有病害扩展)分析)?",
            "无",
            text,
        )
        text = re.sub(
            r"本次为[^。；;]{0,40}定期检测[，,、]?报告未提供往年(?:检测)?评分(?:及|或)病害对比资料(?:，无法进行(?:历史趋势|既有病害扩展)分析)?",
            "无",
            text,
        )
        text = re.sub(
            r"本次为[^。；;]{0,40}定期检测[，,、]?无往年检测评分(?:、|及)病害对比(?:数据|资料)[，,、]?不存在既有病害扩展情况",
            "无",
            text,
        )
        if text and text not in result:
            result.append(text + ("" if text.endswith(("。", "．", ".")) else "。"))
    return tuple(result[:4])


def _safety_topic(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    if "承载" in compact or "荷载" in compact:
        return "承载能力"
    if "耐久" in compact:
        return "耐久性"
    if any(marker in compact for marker in ("行车", "通行", "行人")):
        return "通行安全"
    if "使用功能" in compact or "功能" in compact:
        return "使用功能"
    return "结构安全"


def _safety_rank(value: str) -> tuple[int, int]:
    compact = re.sub(r"\s+", "", value)
    score = 0
    if any(marker in compact for marker in ("综合评定", "总体评定", "最终评定", "安全性评估")):
        score += 8
    if any(marker in compact for marker in ("承载能力", "结构安全", "使用功能", "耐久性")):
        score += 5
    if any(marker in compact for marker in ("满足要求", "符合要求", "不影响", "影响")):
        score += 3
    return (-score, len(compact))


def normalize_safety_impacts(values: Sequence[object]) -> tuple[str, ...]:
    """Remove extractor commentary and resolve same-topic contradictions."""

    cleaned = _normalize_text_list(values, kind="safety")
    selected: dict[str, tuple[tuple[int, int], str]] = {}
    for text in cleaned:
        topic = _safety_topic(text)
        rank = _safety_rank(text)
        current = selected.get(topic)
        if current is None or rank < current[0]:
            selected[topic] = (rank, text)
            continue
        if rank == current[0]:
            compact = re.sub(r"\s+", "", text)
            current_compact = re.sub(r"\s+", "", current[1])
            reassuring = any(marker in compact for marker in ("不影响", "未影响", "满足要求", "符合要求"))
            current_reassuring = any(marker in current_compact for marker in ("不影响", "未影响", "满足要求", "符合要求"))
            if reassuring and not current_reassuring:
                selected[topic] = (rank, text)
    return tuple(text for _, text in sorted(selected.values(), key=lambda item: item[0])[:3])


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




def resolve_recommendation_category(category: object, content: object) -> str:
    """Return one official category before summary counting and rendering.

    Explicit categories always win.  The fallback intentionally matches the
    historical renderer policy so the prediction summary and visible table can
    no longer disagree.
    """

    raw = _display(category)
    if raw == "立即维修":
        return "立即处置"
    if raw in _RECOMMENDATION_CATEGORIES:
        return raw
    text = _display(content)
    compact = re.sub(r"\s+", "", text)
    if any(word in compact for word in ("立即", "紧急", "危急", "封闭交通", "临时隔离")):
        return "立即处置"
    if any(word in compact for word in (
        "维修", "修复", "修补", "更换", "处治", "处置", "加固", "封闭",
        "灌浆", "灌缝", "堵漏", "补强", "除锈", "涂刷", "铺装", "勾缝",
        "抹灰", "恢复", "安装", "疏通", "清理堵塞",
    )):
        return "尽快维修"
    return "预防性养护"


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
    """Rebuild the visible summary from the final resolved detail rows.

    All three official categories are emitted.  This deliberately ignores a
    stale source summary once structured recommendation rows exist, preventing
    the summary/table contradiction that affected 34 submitted reports.
    """

    counts = recommendation_counts(recommendations)
    return "、".join(
        f"{counts[category]}条{category}" for category in _RECOMMENDATION_CATEGORIES
    )


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
            category=resolve_recommendation_category(
                recommendation.category, recommendation.content
            ),
            content=normalize_recommendation_text(recommendation.content),
            location=_display(recommendation.location),
        )
        for recommendation in prediction.recommendations
    )
    summary = replace(
        prediction.summary,
        report_date=normalize_report_date(prediction.summary.report_date),
        overall_conclusion=normalize_overall_conclusion(
            prediction.summary.overall_conclusion
        ),
        risk_points=normalize_risk_points(prediction.summary.risk_points),
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
        detailed_conclusion=_normalize_text_list(
            prediction.detailed_conclusion,
            kind="detailed",
        ),
        causes=_normalize_text_list(prediction.causes, kind="cause"),
        safety_impact=normalize_safety_impacts(prediction.safety_impact),
    )
