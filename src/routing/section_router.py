"""Small, deterministic section router for the parsed Word document model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from ..contracts import (
    DocumentBlock,
    DocumentModel,
    ParagraphBlock,
    SourceAnchor,
    TableBlock,
)


class SectionCategory(StrEnum):
    """The six report sections understood by the first extraction stage."""

    SCORING = "scoring"
    DEFECT_TABLE = "defect_table"
    RECOMMENDATIONS = "recommendations"
    INSPECTION_CONCLUSION = "inspection_conclusion"
    SAFETY_ASSESSMENT = "safety_assessment"
    TREATMENT_RECOMMENDATIONS = "treatment_recommendations"


@dataclass(frozen=True)
class SectionRoute:
    """One routed section, retaining its source evidence."""

    category: SectionCategory
    heading: DocumentBlock
    blocks: tuple[DocumentBlock, ...]
    source: SourceAnchor
    is_container: bool = False


_CATEGORY_ORDER = (
    SectionCategory.TREATMENT_RECOMMENDATIONS,
    SectionCategory.SAFETY_ASSESSMENT,
    SectionCategory.DEFECT_TABLE,
    SectionCategory.INSPECTION_CONCLUSION,
    SectionCategory.RECOMMENDATIONS,
    SectionCategory.SCORING,
)

_TITLE_KEYWORDS: dict[SectionCategory, tuple[str, ...]] = {
    SectionCategory.SCORING: (
        "技术状况评分",
        "总体评分",
        "评分结果",
        "评分章节",
        "评分",
    ),
    SectionCategory.DEFECT_TABLE: (
        "病害明细表",
        "病害明细",
        "病害列表",
        "病害表",
        "缺陷明细",
        "缺陷表",
        "病害部位",
        "病害类型",
        "病害描述",
        "病害",
    ),
    SectionCategory.RECOMMENDATIONS: (
        "建议明细表",
        "建议明细",
        "主要建议",
        "维修建议",
        "养护建议",
        "维护建议",
        "建议表",
        "建议",
    ),
    SectionCategory.INSPECTION_CONCLUSION: (
        "检测结论",
        "详细结论",
        "总体结论",
        "检查结论",
        "检测结果",
        "结论",
    ),
    SectionCategory.SAFETY_ASSESSMENT: (
        "安全性评估",
        "安全评估",
        "安全影响",
        "结构安全",
        "安全性",
    ),
    SectionCategory.TREATMENT_RECOMMENDATIONS: (
        "处理建议",
        "处置建议",
        "处治建议",
        "处理措施",
        "处置措施",
        "处治措施",
        "维修处理",
        "维修处治",
        "加固处理",
    ),
}

_RECOMMENDATION_CONTAINER_TITLES = (
    "结论与建议",
    "结论及建议",
    "综合评估与建议",
    "评估结论与建议",
)
_RECOMMENDATION_LEAF_TITLES = (
    "处理建议",
    "处置建议",
    "处治建议",
    "维修建议",
    "养护建议",
    "维护建议",
    "主要建议",
    # Keep the legacy section headings that are already treated as explicit
    # recommendation sections by the extractor.
    "建议明细表",
    "建议明细",
    "建议列表",
    "建议表",
    "建议",
    "处理措施",
    "处置措施",
    "处治措施",
    "应采取的措施",
    "应立即维护的设施",
    "日常养护中采取措施",
    "日常养护中采取才措施",
)

_TABLE_DEFECT_MARKERS = (
    "病害部位",
    "病害类型",
    "病害描述",
    "病害明细",
    "病害列表",
    "病害表",
    "缺陷明细",
    "缺陷表",
)
_TABLE_SCORE_MARKERS = ("总体评分", "评分等级", "评分")
_TABLE_RECOMMENDATION_MARKERS = (
    "建议类别",
    "建议内容",
    "维修建议",
    "养护建议",
    "建议明细",
)

_HEADING_STYLE_RE = re.compile(r"(?:heading|标题)[ _-]?([1-9])", re.IGNORECASE)
_NUMBER_PREFIX_RE = re.compile(
    r"^(?:"
    r"第[0-9零一二三四五六七八九十百千万]+[章节篇条]?"
    r"|[（(][0-9零一二三四五六七八九十百千万]+[）)]"
    r"|[0-9]+(?:[.][0-9]+)*[、.:：)）]?"
    r"|[零一二三四五六七八九十百千万]+[、.:：)）]"
    r")"
)


def route_sections(document: DocumentModel) -> tuple[SectionRoute, ...]:
    """Return matched sections in their original document order.

    A styled heading is considered before the keyword fallback.  When no
    heading metadata is available, a short single-line paragraph containing a
    known section keyword is treated as a heading candidate.  Tables can be
    routed directly when their header text identifies a section.
    """

    blocks = document.blocks
    candidates = [
        (index, category)
        for index, block in enumerate(blocks)
        if (category := _candidate_category(block)) is not None
    ]

    routes: list[SectionRoute] = []
    for index, category in candidates:
        block = blocks[index]
        end = _section_end(blocks, index)
        if isinstance(block, TableBlock) and _covered_table_candidate(
            routes, category, index
        ):
            continue
        routes.append(
            SectionRoute(
                category=category,
                heading=block,
                blocks=tuple(blocks[index:end]),
                source=block.source,
                is_container=(
                    isinstance(block, ParagraphBlock)
                    and is_recommendation_container_heading(block.raw_text)
                ),
            )
        )
    return tuple(routes)


def _candidate_category(block: DocumentBlock) -> SectionCategory | None:
    if isinstance(block, TableBlock):
        return _match_table(block.raw_text)

    category = _match_title(block.raw_text)
    if category is None:
        return None
    if isinstance(block, ParagraphBlock):
        if _heading_level(block) is not None:
            return category
    return category if _is_keyword_fallback_title(block.raw_text) else None


def _match_title(raw_text: str) -> SectionCategory | None:
    text = _strip_numbering(_compact(raw_text))
    if not text:
        return None

    matches: list[tuple[int, int, SectionCategory]] = []
    for order, category in enumerate(_CATEGORY_ORDER):
        for keyword in _TITLE_KEYWORDS[category]:
            if keyword in text:
                if category is SectionCategory.DEFECT_TABLE and any(
                    marker in text for marker in ("成因", "原因")
                ):
                    continue
                matches.append((len(keyword), -order, category))
    if not matches:
        return None
    return max(matches)[2]


def _match_table(raw_text: str) -> SectionCategory | None:
    text = _compact(raw_text)
    if not text:
        return None
    if any(marker in text for marker in _TABLE_DEFECT_MARKERS):
        return SectionCategory.DEFECT_TABLE
    if any(marker in text for marker in _TABLE_SCORE_MARKERS) and (
        "等级" in text or "分数" in text or "评分" in text
    ):
        return SectionCategory.SCORING
    if any(marker in text for marker in _TABLE_RECOMMENDATION_MARKERS):
        return SectionCategory.RECOMMENDATIONS
    if "处理建议" in text or "处置建议" in text or "处治建议" in text:
        return SectionCategory.TREATMENT_RECOMMENDATIONS
    if "安全性评估" in text or "安全评估" in text:
        return SectionCategory.SAFETY_ASSESSMENT
    if "检测结论" in text or "详细结论" in text or "总体结论" in text:
        return SectionCategory.INSPECTION_CONCLUSION
    if "\n" not in raw_text and len(text) <= 80:
        return _match_title(text)
    return None


def _section_end(
    blocks: tuple[DocumentBlock, ...], start: int
) -> int:
    block = blocks[start]
    if isinstance(block, TableBlock):
        return start + 1

    start_level = _heading_level(block)
    for index in range(start + 1, len(blocks)):
        next_block = blocks[index]
        next_level = _heading_level(next_block)
        if next_level is not None:
            if start_level is None or next_level <= start_level:
                return index
            continue
        if not isinstance(next_block, TableBlock) and _candidate_category(next_block) is not None:
            return index
    return len(blocks)


def _covered_table_candidate(
    routes: list[SectionRoute], category: SectionCategory, block_index: int
) -> bool:
    for route in routes:
        if route.category is not category:
            continue
        first = route.source.block_index
        last = route.blocks[-1].block_index
        if first < block_index <= last:
            return True
    return False


def _is_keyword_fallback_title(raw_text: str) -> bool:
    if "\n" in raw_text or len(_compact(raw_text)) > 80:
        return False
    text = _strip_numbering(_compact(raw_text)).rstrip("：:。.;；，,、")
    for keywords in _TITLE_KEYWORDS.values():
        if any(text == keyword or text.endswith(keyword) for keyword in keywords):
            return True
    return text.endswith(("章节", "部分")) and _match_title(text) is not None


def _normalise_heading_title(raw_text: str) -> str:
    return _strip_numbering(_compact(raw_text)).strip("：:。.;；，,、")


def is_recommendation_container_heading(raw_text: str) -> bool:
    """Return whether a heading is a composite recommendation container.

    Composite headings provide a boundary for child sections such as
    ``5.1 检测结论`` and ``5.4 处理建议``.  They must never be used as the
    plain-text recommendation source themselves.
    """

    title = _normalise_heading_title(raw_text)
    return any(title == item or title.endswith(item) for item in _RECOMMENDATION_CONTAINER_TITLES)


def is_recommendation_leaf_heading(raw_text: str) -> bool:
    """Return whether a heading can directly provide recommendation text."""

    if is_recommendation_container_heading(raw_text):
        return False
    title = _normalise_heading_title(raw_text)
    return any(title == item or title.endswith(item) for item in _RECOMMENDATION_LEAF_TITLES)


def _heading_level(block: DocumentBlock) -> int | None:
    if not isinstance(block, ParagraphBlock):
        return None
    if block.heading_level is not None:
        return max(1, block.heading_level)
    style_id = (block.style_id or "").strip()
    match = _HEADING_STYLE_RE.search(style_id)
    if match is not None:
        return int(match.group(1))
    if style_id.casefold() in {"title", "标题", "chaptertitle"}:
        return 1
    return None


def _compact(raw_text: str) -> str:
    return re.sub(r"\s+", "", raw_text).translate(
        str.maketrans({"．": ".", "（": "(", "）": ")", "：": ":"})
    )


def _strip_numbering(text: str) -> str:
    return _NUMBER_PREFIX_RE.sub("", text, count=1)
