"""Deterministic fact extraction for the remaining text-list sections."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence

from ..contracts import BridgeSummary, DocumentModel, ParagraphBlock, Recommendation


@dataclass(frozen=True)
class TextSectionExtraction:
    """The four text-list values assembled from existing Word evidence."""

    detailed_conclusion: tuple[str, ...] = ()
    causes: tuple[str, ...] = ()
    treatments: tuple[str, ...] = ()
    safety_impact: tuple[str, ...] = ()


@dataclass(frozen=True)
class _TextUnit:
    block_index: int
    text: str


_DISEASE_WORDS = (
    "病害", "缺陷", "裂缝", "裂纹", "破损", "损坏", "锈蚀", "露筋", "渗水", "积水",
    "缺失", "离析", "不密实", "泛碱", "变形", "磨损", "腐蚀", "开裂", "松动", "脱落",
    "坑槽", "沉降", "剥落", "老化", "冲刷", "断裂", "移位",
)
_COMPONENT_WORDS = (
    "桥面", "上部", "下部", "梁", "板", "腹板", "翼板", "横隔板", "湿接缝", "盖梁",
    "支座", "桥台", "桥墩", "栏杆", "护栏", "伸缩缝", "泄水", "钢筋", "保护层", "结构",
    "构件", "桥梁", "路面", "墩台",
)
_CAUSE_WORDS = ("由于", "因", "主要原因为", "原因是", "导致", "造成", "受影响")
_IMPACT_WORDS = (
    "影响", "风险", "隐患", "承载能力", "行车安全", "结构安全", "耐久性", "使用功能",
    "可能导致", "不利于",
)
_ACTION_WORDS = (
    "建议", "应及时", "需及时", "及时进行", "及时处理", "及时修复", "及时维修", "修补",
    "维修", "养护", "处置", "处理", "加固", "清理", "封闭", "更换", "复位", "除锈",
    "涂刷", "灌浆", "修理",
)
_FACT_WORDS = (
    "总体", "整体", "技术状况", "评分", "等级", "BCI", "病害", "裂缝", "破损", "锈蚀",
    "露筋", "渗水", "缺失", "结构", "承载", "良好", "完好", "满足", "合格率",
)
_SECTION_PATTERNS = {
    "safety": re.compile(r"(?:\d+(?:\.\d+)*\s*)?安全性评估(?!规程|等级|内容)"),
    "conclusion": re.compile(r"(?:\d+(?:\.\d+)*\s*)?(?:检测结论|评估结论|检查结论|总体结论)"),
    "treatment": re.compile(r"(?:\d+(?:\.\d+)*\s*)?(?:处理建议|处置建议|处治建议|处理意见|建议明细)"),
    "overview": re.compile(r"(?:\d+(?:\.\d+)*\s*)?(?:外观检查结果|外观病害检查)"),
}
_TITLE_RE = re.compile(
    r"^(?:第?\d+(?:\.\d+)*\s*)?(?:检测结论|评估结论|检查结论|总体结论|安全性评估|"
    r"现状评估|预测评估|综合评估|外观检查|专项检测|桥面系|上部结构|下部结构|处理建议|"
    r"处置建议|处治建议|建议明细|病害明细|技术状况等级评定|结构检算|静载试验|动载试验|基本状况卡)$"
)
_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:[（(]?[0-9一二三四五六七八九十百千万]+[）)\.、:：]?|[⑴-⒇])\s*"
)


def extract_text_sections(
    document: DocumentModel,
    routes: Sequence[object],
    recommendations: Sequence[Recommendation],
    summary: BridgeSummary,
) -> TextSectionExtraction:
    """Extract fact-only text sections without introducing new report facts."""

    all_units = _document_units(document)
    heading_blocks = _heading_blocks(routes)
    recommendation_blocks = _route_blocks(routes, {"recommendations", "treatment_recommendations"})
    inspection_units = _route_units(routes, "inspection_conclusion", recommendation_blocks)
    safety_units = _section_window(all_units, "safety", {"conclusion", "treatment"})
    if not safety_units:
        safety_units = tuple(unit for unit in all_units if unit.block_index not in recommendation_blocks)
    if not inspection_units:
        inspection_units = _pre_section_units(all_units, "safety")

    detailed = _detailed_conclusion(
        summary,
        inspection_units,
        safety_units,
        all_units,
        heading_blocks,
    )
    causes = _causes(safety_units, heading_blocks)
    safety = _safety_impact(safety_units, heading_blocks)
    treatments = _treatments(recommendations)
    return TextSectionExtraction(detailed, causes, treatments, safety)


def _document_units(document: DocumentModel) -> tuple[_TextUnit, ...]:
    return tuple(
        _TextUnit(block.block_index, str(getattr(block, "raw_text", "")))
        for block in document.blocks
        if _compact(str(getattr(block, "raw_text", "")))
    )


def _heading_blocks(routes: Sequence[object]) -> set[int]:
    result: set[int] = set()
    for route in routes:
        heading = getattr(route, "heading", None)
        if heading is not None:
            result.add(int(getattr(heading, "block_index", -1)))
    return result


def _route_blocks(routes: Sequence[object], categories: set[str]) -> set[int]:
    result: set[int] = set()
    for route in routes:
        category = getattr(getattr(route, "category", ""), "value", getattr(route, "category", ""))
        if str(category) not in categories:
            continue
        result.update(int(getattr(block, "block_index", -1)) for block in getattr(route, "blocks", ()))
    return result


def _route_units(
    routes: Sequence[object],
    category_name: str,
    excluded_blocks: set[int],
) -> tuple[_TextUnit, ...]:
    result: list[_TextUnit] = []
    seen: set[tuple[int, str]] = set()
    for route in routes:
        category = getattr(getattr(route, "category", ""), "value", getattr(route, "category", ""))
        if str(category) != category_name:
            continue
        for block in getattr(route, "blocks", ()):
            if not isinstance(block, ParagraphBlock) or block.block_index in excluded_blocks:
                continue
            key = (block.block_index, block.raw_text)
            if key not in seen and _compact(block.raw_text):
                seen.add(key)
                result.append(_TextUnit(block.block_index, block.raw_text))
    return tuple(sorted(result, key=lambda item: item.block_index))


def _section_window(
    units: Sequence[_TextUnit],
    start_name: str,
    end_names: set[str],
) -> tuple[_TextUnit, ...]:
    start = _last_marker(units, _SECTION_PATTERNS[start_name])
    if start is None:
        return ()
    end = _first_marker_after(units, end_names, start)
    return _slice_units(units, start, end)


def _pre_section_units(units: Sequence[_TextUnit], section_name: str) -> tuple[_TextUnit, ...]:
    marker = _last_marker(units, _SECTION_PATTERNS[section_name])
    if marker is None:
        return tuple(units)
    result: list[_TextUnit] = []
    for unit in units:
        if unit.block_index < marker[0]:
            result.append(unit)
        elif unit.block_index == marker[0] and marker[1] > 0:
            result.append(_TextUnit(unit.block_index, unit.text[: marker[1]]))
    return tuple(result)


def _last_marker(units: Sequence[_TextUnit], pattern: re.Pattern[str]) -> tuple[int, int, int, int] | None:
    matches: list[tuple[int, int, int, int]] = []
    for order, unit in enumerate(units):
        for match in pattern.finditer(unit.text):
            matches.append((unit.block_index, match.start(), match.end(), order))
    return max(matches, key=lambda item: (item[0], item[1])) if matches else None


def _first_marker_after(
    units: Sequence[_TextUnit], names: set[str], start: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    matches: list[tuple[int, int, int, int]] = []
    for name in names:
        marker = _last_marker_after(units, _SECTION_PATTERNS[name], start)
        if marker is not None:
            matches.append(marker)
    return min(matches, key=lambda item: (item[0], item[1])) if matches else None


def _last_marker_after(
    units: Sequence[_TextUnit], pattern: re.Pattern[str], start: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    matches: list[tuple[int, int, int, int]] = []
    for order, unit in enumerate(units):
        for match in pattern.finditer(unit.text):
            marker = (unit.block_index, match.start(), match.end(), order)
            if (unit.block_index, match.start()) > (start[0], start[1]):
                matches.append(marker)
    return min(matches, key=lambda item: (item[0], item[1])) if matches else None


def _slice_units(
    units: Sequence[_TextUnit],
    start: tuple[int, int, int, int],
    end: tuple[int, int, int, int] | None,
) -> tuple[_TextUnit, ...]:
    result: list[_TextUnit] = []
    for unit in units:
        if unit.block_index < start[0]:
            continue
        if unit.block_index == start[0]:
            text = unit.text[start[2] :]
        else:
            text = unit.text
        if end is not None:
            if unit.block_index > end[0]:
                break
            if unit.block_index == end[0]:
                offset = start[2] if unit.block_index == start[0] else 0
                text = text[: end[1] - offset]
        if _compact(text):
            result.append(_TextUnit(unit.block_index, text))
    return tuple(result)


def _detailed_conclusion(
    summary: BridgeSummary,
    inspection_units: Sequence[_TextUnit],
    safety_units: Sequence[_TextUnit],
    all_units: Sequence[_TextUnit],
    heading_blocks: set[int],
) -> tuple[str, ...]:
    result: list[str] = []
    score = _score_sentence(summary)
    if score:
        result.append(score)

    descriptions: list[str] = []
    for unit in inspection_units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        raw = _clean_text(unit.text, strip_number=False)
        if _is_noise(raw) or _is_title(raw):
            continue
        candidates = _split_sentences(raw, split_semicolon=True)
        for value in candidates:
            text = _compact(value)
            if not _has_defect(text) or not _has_any(text, _COMPONENT_WORDS):
                continue
            if _is_noise(value) or _is_cause(value) or _is_impact(value) or _is_action(value):
                continue
            if _has_any(text, ("检查", "检测", "试验", "照片", "见表", "见图", "规范", "测点", "指数", "评分", "验算", "表格", "实测", "结构尺寸", "安全性评估", "处理建议", "处理意见", "变形规律", "变形稳定", "各项观测", "荷载", "加载")):
                continue
            descriptions.append(_truncate_description(value))
    if not descriptions:
        overview_units = _section_window(all_units, "overview", {"safety"})
        if not overview_units:
            overview_units = _pre_section_units(all_units, "safety")
        for unit in overview_units:
            raw = _clean_text(unit.text, strip_number=False)
            if _is_noise(raw) or _is_title(raw):
                continue
            for value in _split_sentences(raw, split_semicolon=True):
                text = _compact(value)
                if not _has_defect(text) or not _has_any(text, _COMPONENT_WORDS):
                    continue
                if _is_noise(value) or _is_cause(value) or _is_impact(value) or _is_action(value):
                    continue
                if _has_any(text, ("检查", "检测", "试验", "照片", "见表", "见图", "规范", "测点", "指数", "评分", "验算", "表格", "实测", "结构尺寸", "安全性评估", "处理建议", "处理意见", "变形规律", "变形稳定", "各项观测", "荷载", "加载")):
                    continue
                descriptions.append(_truncate_description(value))
    descriptions = _unique(value for value in descriptions if len(_compact(value)) >= 4)
    if descriptions:
        result.append("；".join(descriptions[:6]))

    state: list[str] = []
    for unit in safety_units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        raw = _clean_text(unit.text, strip_number=False)
        if _is_noise(raw) or _is_title(raw):
            continue
        for value in _split_sentences(raw):
            text = _compact(value)
            if _is_cause(value) or _is_impact(value) or _is_action(value):
                continue
            if _has_any(text, ("检测表明", "满足", "合格率", "承载能力", "状况良好", "外观良好", "试验结果", "检算结果")):
                state.append(value)
    state = _unique(state)
    if state:
        result.append(" ".join(state))

    conclusion = _section_window(all_units, "conclusion", {"treatment"})
    final_facts: list[str] = []
    for unit in conclusion:
        for value in _split_sentences(unit.text):
            text = _compact(value)
            if _is_noise(value) or _is_title(value) or _is_cause(value) or _is_impact(value) or _is_action(value):
                continue
            if _has_any(text, _FACT_WORDS):
                final_facts.append(value)
    final_facts = _unique(final_facts)
    if final_facts:
        result.append(" ".join(final_facts))
    return _unique(result)


def _causes(units: Sequence[_TextUnit], heading_blocks: set[int]) -> tuple[str, ...]:
    result: list[str] = []
    for unit in units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        raw = _clean_text(unit.text, strip_number=False)
        if _is_noise(raw) or _is_title(raw):
            continue
        for value in _split_sentences(raw, split_semicolon=True):
            if _is_cause(value) and _has_any(_compact(value), _DISEASE_WORDS + _COMPONENT_WORDS):
                if _has_any(_compact(value), ("检查建议", "巡查建议", "定期检查", "日常检查")):
                    continue
                result.append(value)
    return _unique(result)


def _safety_impact(units: Sequence[_TextUnit], heading_blocks: set[int]) -> tuple[str, ...]:
    buckets: dict[str, list[str]] = {"deck": [], "upper": [], "lower": [], "overall": []}
    for unit in units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        raw = _clean_text(unit.text, strip_number=False)
        if _is_noise(raw) or _is_title(raw):
            continue
        for value in _split_sentences(raw, split_semicolon=True):
            text = _compact(value)
            if _is_cause(value) or _is_action(value) or not _has_any(text, _IMPACT_WORDS):
                continue
            if not _has_any(text, _DISEASE_WORDS + _COMPONENT_WORDS):
                continue
            if _has_any(text, ("检查建议", "巡查建议", "定期检查", "日常检查")):
                continue
            if _has_any(text, ("桥面", "路面", "栏杆", "护栏", "泄水", "行车", "行人")):
                bucket = "deck"
            elif _has_any(text, ("上部", "梁", "板", "腹板", "翼板", "湿接缝")):
                bucket = "upper"
            elif _has_any(text, ("下部", "桥台", "盖梁", "支座", "桥墩", "墩台")):
                bucket = "lower"
            else:
                bucket = "overall"
            buckets[bucket].append(value)
    return tuple(
        _clean_text(" ".join(_unique(values)), strip_number=False)
        for values in buckets.values()
        if values
    )


def _treatments(recommendations: Sequence[Recommendation]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for recommendation in recommendations:
        key = (recommendation.category, recommendation.location, recommendation.content)
        if key in seen or not recommendation.content.strip():
            continue
        seen.add(key)
        result.append(
            "；".join(
                part
                for part in (
                    recommendation.category,
                    recommendation.location,
                    recommendation.content,
                )
                if part.strip()
            )
        )
    return tuple(result)


def _score_sentence(summary: BridgeSummary) -> str:
    def value(item: object) -> str:
        return str(item or "").strip()

    if not value(summary.overall_score) and not value(summary.overall_grade):
        return ""
    result = (
        f"经综合评定，该桥总体技术状况评分 {value(summary.overall_score)} 分，"
        f"总体技术状况等级为 {value(summary.overall_grade)}。"
    )
    parts: list[str] = []
    for label, score, grade in (
        ("上部结构", summary.superstructure_score, summary.superstructure_grade),
        ("下部结构", summary.substructure_score, summary.substructure_grade),
        ("桥面系", summary.deck_score, summary.deck_grade),
    ):
        if value(score) or value(grade):
            parts.append(f"{label}评分 {value(score)} 分（{value(grade)}）。")
    if parts:
        result += "其中，" + " ".join(parts)
    return _clean_text(result, strip_number=False)


def _split_sentences(value: str, *, split_semicolon: bool = False) -> tuple[str, ...]:
    pattern = r"(?<=[。！？!?；;])\s*" if split_semicolon else r"(?<=[。！？!?])\s*"
    return tuple(
        cleaned
        for part in re.split(pattern, " ".join(value.split()))
        if (cleaned := _clean_text(part)) and not _is_noise(cleaned)
    )


def _clean_text(value: str, *, strip_number: bool = True) -> str:
    result = re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip(" \t;；")
    if strip_number:
        result = _NUMBER_PREFIX_RE.sub("", result)
    return result.strip(" \t;；")


def _truncate_description(value: str) -> str:
    result = _clean_text(value, strip_number=False)
    for marker in ("具体病害情况见", "现场病害典型照片见"):
        if marker in result:
            result = result.split(marker, 1)[0].rstrip(" 。；;")
    return result


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _has_any(value: str, words: Iterable[str]) -> bool:
    return any(word in value for word in words)


def _has_defect(value: str) -> bool:
    return _has_any(value, _DISEASE_WORDS) or bool(re.search(r"无(?:泄水|排水|防护|设施|孔盖)", value))


def _is_cause(value: str) -> bool:
    text = _compact(value)
    for word in _CAUSE_WORDS:
        if word != "因" and word in text:
            return True
    return bool(re.search(r"(?<!此)因(?!此)", text))


def _is_impact(value: str) -> bool:
    return _has_any(_compact(value), _IMPACT_WORDS)


def _is_action(value: str) -> bool:
    return _has_any(_compact(value), _ACTION_WORDS)


def _is_title(value: str) -> bool:
    return bool(_TITLE_RE.fullmatch(_compact(value).strip("：:。.;；，,、")))


def _is_heading_unit(unit: _TextUnit, heading_blocks: set[int]) -> bool:
    if unit.block_index not in heading_blocks:
        return False
    text = _clean_text(unit.text, strip_number=False)
    return len(_compact(text)) <= 80 and _is_title(text)


def _is_noise(value: str) -> bool:
    text = _compact(value)
    if len(text) < 4 or "……" in value or "...." in value:
        return True
    if re.fullmatch(r"(?:表|图|照片)?[0-9一二三四五六七八九十.-]+", text):
        return True
    if re.fullmatch(r"(?:第)?[0-9一二三四五六七八九十]+页", text):
        return True
    if re.match(r"^(?:图|照片)\s*[0-9]", text):
        return True
    if re.match(r"^表\s*[0-9]", text) and not _has_any(text, ("病害为", "病害主要", "主要病害")):
        return True
    if "目录" in text and not _has_any(text, _DISEASE_WORDS + _IMPACT_WORDS):
        return True
    return False


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _compact(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)
