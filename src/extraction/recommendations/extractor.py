"""Word-first, deterministic recommendation extraction.

The extractor deliberately keeps the shared :class:`Recommendation` contract
unchanged.  Uncertain category resolution is reported in the result metadata
instead of being replaced with a guessed maintenance category.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
import re
from ...contracts import (
    DocumentModel,
    ParagraphBlock,
    Recommendation,
    SourceAnchor,
    TableBlock,
    TableCell,
    TableRow,
)
from ...routing import SectionCategory, SectionRoute, route_sections


RECOMMENDATION_CATEGORIES = ("立即维修", "立即处置", "尽快维修", "预防性养护")

_TARGET_CATEGORIES = {
    SectionCategory.RECOMMENDATIONS.value,
    SectionCategory.TREATMENT_RECOMMENDATIONS.value,
}

_INDEX_RE = re.compile(
    r"(?:第[0-9零一二三四五六七八九十百千万]+[章节篇条项]?"
    r"|[（(][0-9零一二三四五六七八九十百千万]+[）)]"
    r"|[⑴-⒇]"
    r"|[①-⑳]"
    r"|[0-9]+(?:\.[0-9]+)+[、.:：)）]?"
    r"|[0-9]+[、.:：)）]"
    r"|[0-9]+(?=\s+(?:建议|应|需|须|严禁|立即|及时|对|按照|根据|修复|维修|加强|严格))"
    r"|[零一二三四五六七八九十百千万]+[、.:：)）])"
)
_ITEM_SEPARATOR_RE = re.compile(r"[;；]+")
_LINE_SEPARATOR_RE = re.compile(r"[\r\n]+")
_CATEGORY_RE = re.compile("|".join(map(re.escape, RECOMMENDATION_CATEGORIES)))
_LOCATION_LABEL_RE = re.compile(
    r"^(?:病害|维修|养护|处理|处置|处治)?"
    r"(?:部位|位置|地点|构件|范围)\s*[:：]\s*(.*)$"
)
_INDEX_MARKERS = ("序号", "编号", "序列号", "项次")
_CATEGORY_MARKERS = (
    "建议类别",
    "建议类型",
    "维修类别",
    "养护类别",
    "处理类别",
    "处置类别",
    "处治类别",
    "类别",
)
_CONTENT_MARKERS = (
    "建议内容",
    "维修建议",
    "养护建议",
    "处理建议",
    "处置建议",
    "处治建议",
    "建议明细",
    "处理措施",
    "处置措施",
    "处治措施",
    "维修措施",
    "养护措施",
    "内容",
    "措施",
)
_LOCATION_MARKERS = (
    "病害部位",
    "建议部位",
    "维修部位",
    "养护部位",
    "处理部位",
    "处置部位",
    "处治部位",
    "部位",
    "位置",
    "地点",
    "构件",
    "范围",
)
_TITLE_WORDS = (
    "建议",
    "建议明细",
    "建议列表",
    "建议明细表",
    "结论及建议",
    "结论与建议",
    "维修建议",
    "养护建议",
    "维修养护建议",
    "维护建议",
    "维护处置建议",
    "处理建议",
    "处置建议",
    "处治建议",
    "应采取的措施",
    "应立即维护的设施",
    "日常养护中采取措施",
    "日常养护中采取才措施",
)
_RECOMMENDATION_HEADING_WORDS = (
    "结论及建议",
    "结论与建议",
    "建议明细表",
    "维修养护建议",
    "维护建议",
    "维护处置建议",
    "养护建议",
    "维修建议",
    "处理建议",
    "处置建议",
    "处治建议",
    "应采取的措施",
    "应立即维护的设施",
    "日常养护中采取措施",
    "日常养护中采取才措施",
    "建议",
)
_DIRECTIVE_WORDS = (
    "建议",
    "应",
    "需",
    "须",
    "严禁",
    "禁止",
    "立即",
    "及时",
    "尽快",
    "必须",
    "做好",
    "加强",
    "严格",
    "可",
)
_ACTION_WORDS = (
    "建议",
    "维修",
    "养护",
    "维护",
    "修复",
    "中修",
    "大修",
    "小修",
    "加固",
    "处理",
    "处置",
    "处治",
    "更换",
    "清理",
    "检查",
)
_RECOMMENDATION_ACTION_WORDS = (
    "建议",
    "维修",
    "养护",
    "维护",
    "修复",
    "修补",
    "中修",
    "大修",
    "小修",
    "加固",
    "更换",
    "清理",
    "封闭",
    "浇注",
    "检查",
    "观测",
    "巡查",
    "保护",
    "处理",
    "处置",
    "处治",
)
_STRONG_RECOMMENDATION_ACTION_WORDS = tuple(
    word for word in _RECOMMENDATION_ACTION_WORDS if word != "建议"
)
_REPAIR_ACTION_WORDS = (
    "维修",
    "修复",
    "修补",
    "修理",
    "中修",
    "大修",
    "小修",
    "加固",
    "更换",
    "清理",
    "封闭",
    "浇注",
    "补强",
    "灌浆",
    "堵漏",
    "除锈",
    "涂刷",
    "疏通",
    "恢复",
    "拆除",
    "重装",
    "处理",
    "处置",
    "处治",
)
_MONITORING_ACTION_WORDS = (
    "检查",
    "观测",
    "巡查",
    "日常养护",
    "日常维护",
    "定期养护",
    "定期维护",
)
_STATISTIC_COUNT_RE = re.compile(
    r"(?:[0-9]+|[零一二三四五六七八九十百千万]+)\s*"
    r"(?:处|条|根|个|套|项|片|块|点|次|组|跨)"
)
_STATISTIC_DEFECT_WORDS = (
    "裂缝",
    "开裂",
    "破损",
    "露筋",
    "锈蚀",
    "渗水",
    "泛碱",
    "坑槽",
    "坑洞",
    "蜂窝",
    "麻面",
    "脱落",
    "剥落",
    "缺失",
    "变形",
    "磨损",
    "病害",
    "不密实",
)
_CONTINUATION_PREFIXES = (
    "具体",
    "同时",
    "并",
    "其中",
    "对于",
    "对该",
    "该",
    "上述",
    "以上",
    "施工",
    "必要时",
    "应",
    "可",
)
_INFERRED_LOCATION_RE = re.compile(
    r"(?:对于|由于|针对|对|在|于)"
    r"([^，,;；。:：]{1,50}?)"
    r"(?=(?:存在|有|出现|多处|局部|设置|采取|进行|及时|应|建议|修补|维修|中修|大修|小修|养护|处理|处置|处治|清理|检查|做好|严格|破损|裂缝|渗水|露筋|锈蚀|缺失|堵塞|刮痕|变形|错位|开裂|断裂|脱落|积水|泛碱|积土|推积|堆积|变窄|高差|止水带|坑槽|病害|等病害))"
)
_EXCLUDED_RECOMMENDATION_MARKERS = (
    "检测方法",
    "检测过程",
    "检测步骤",
    "试验",
    "试验方法",
    "试验过程",
    "试验步骤",
    "评定依据",
    "评定标准",
    "技术要求",
    "原因分析",
    "主要原因",
    "现场检查",
    "具体病害情况见",
    "具体情况见",
    "照片",
    "典型照片",
    "外观状况良好",
    "未见明显",
    "未见新开展",
    "暂未见",
    "现状为",
    "现状病害",
    "主要病害",
    "病害的发展",
    "病害如不",
    "如不及时",
    "目前而言",
    "影响较小",
    "承载能力",
    "整体性",
    "板底多条纵向裂缝",
    "目前不能够满足",
    "不维修处理就",
    "主体结构完好",
    "养护等级",
    "养护类别",
    "技术状况",
)
_EXCLUDED_RECOMMENDATION_PREFIXES = (
    "检查",
    "检测",
    "试验",
    "评定依据",
    "评定标准",
    "技术要求",
    "原因",
    "通过",
    "评价",
    "依据",
    "根据现场",
    "根据结构",
    "采用",
    "本桥技术状况",
    "桥梁名称",
    "桥梁编号",
    "所在路名",
    "等级",
    "重要桥梁",
    "较重要桥梁",
    "一般桥梁",
    "交通运输",
    "Ⅱ类养护",
    "为掌握",
    "受",
    "委托",
    "本次",
    "本报告",
    "根据",
    "根据外观检查",
    "现场检查",
)
_LOCATION_SUFFIXES = (
    "车辆刮痕",
    "车辆",
    "装饰砖",
    "保护带",
    "止水带",
    "混凝土",
    "变形严重",
    "竖裂",
    "斜裂",
    "纵裂",
    "横裂",
    "开裂",
    "裂缝",
    "刮痕",
    "破损",
    "露筋",
    "锈蚀",
    "渗水",
    "泛碱",
    "积水",
    "坑槽",
    "高差",
    "磨损",
    "漏筋",
    "积淤",
    "等",
    "堵塞",
    "缺失",
    "长草",
    "杂草",
    "病害",
    "情况",
    "部位",
    "处",
)
_INFERRED_SUFFIX_RE = re.compile(
    r"(?:纵向裂缝|横向裂缝|裂缝|破损|锈蚀|渗水|露筋|缺失|病害|斜裂|开裂|刮痕|坑槽|断裂|变形|沉降|脱落|堆积|覆盖|混凝土|保护带|止水带|装饰砖|车辆|接缝填料|填料|泥沙|泥土|垃圾|杂物|积水|盖板)$"
)
_DANGLING_LOCATION_RE = re.compile(r"(?:的|多处|局部|附近|均|都|等|已)$")
_SUBJECT_STATE_RE = re.compile(
    r"^([^，,；;。:：]{1,24}?)(?:存在|出现)"
)
_COMPOUND_LOCATION_RE = re.compile(
    r"(?:对|对于)?"
    r"(桥面|伸缩缝|主梁|桥台|盖梁|栏杆|护栏|梁体|防撞栏杆|支座|梁底|腹板|底板|翼板|湿接缝|横隔板|泄水管|人行道)"
    r"(?:和|、|与)"
    r"(桥面|伸缩缝|主梁|桥台|盖梁|栏杆|护栏|梁体|防撞栏杆|支座|梁底|腹板|底板|翼板|湿接缝|横隔板|泄水管|人行道)"
    r"(?=及时进行|建议及时|等情况)"
)
_GENERIC_LOCATION_MARKERS = (
    ("做好桥梁的日常检查", "桥梁"),
    ("加强桥梁的定期检查", "桥梁"),
    ("建立该桥", "桥梁"),
    ("连续性技术档案", "桥梁"),
    ("加强桥梁的观测", "桥梁"),
    ("在桥上同步跑动", "桥上"),
    ("严禁行人在桥上", "桥上"),
    ("加强对天桥的观测", "天桥"),
    ("及时清理桥面", "桥面"),
    ("清理桥面泥沙", "桥面"),
)
_JOINER_NORMALISE_RE = re.compile(r"及|和|与")
_JUXTAPOSED_LOCATIONS = {
    "桥台盖梁": "桥台、盖梁",
    "墩台盖梁": "墩台、盖梁",
}
_SPECIFIC_AFTER_QIAOMIAN = ("伸缩缝", "防撞栏杆", "防撞护栏", "栏杆")


@dataclass(frozen=True)
class RecommendationExtractionResult:
    """Recommendation records and row-level quality metadata.

    ``records`` contains the shared contract objects.  Each quality flag is a
    schema-compatible mapping with a ``code`` and the exact
    ``quality_flag`` value, plus the affected recommendation index and source
    anchor where available.
    """

    records: tuple[Recommendation, ...] = field(default_factory=tuple)
    quality_flags: tuple[dict[str, object], ...] = field(default_factory=tuple)

    @property
    def recommendations(self) -> tuple[Recommendation, ...]:
        """Alias useful to callers that use the prediction field name."""

        return self.records

    @property
    def quality_flag_codes(self) -> tuple[str, ...]:
        return tuple(str(flag.get("code", "")) for flag in self.quality_flags)

    def to_dict(self) -> dict[str, object]:
        return {
            "recommendations": [asdict(record) for record in self.records],
            "quality_flags": [dict(flag) for flag in self.quality_flags],
        }


@dataclass
class _Candidate:
    index: str
    category_hint: str
    content: str
    location: str
    anchors: list[SourceAnchor]
    source_kind: str
    block_index: int
    numbered: bool = False
    preferred: bool = False


@dataclass(frozen=True)
class _TextItem:
    index: str
    text: str
    numbered: bool


def extract_recommendations(
    document: DocumentModel,
    routes: Sequence[SectionRoute] | None = None,
    *,
    infer_categories: bool = False,
) -> RecommendationExtractionResult:
    """Extract recommendations from routed and fallback Word evidence.

    Recommendation and treatment-recommendation routes are consumed first.
    Matching tables and recommendation-like paragraphs outside those routes
    are then used as deterministic fallback evidence.  No category is inferred
    from a treatment route alone.
    """

    effective_routes = tuple(route_sections(document) if routes is None else routes)
    target_routes = tuple(
        route for route in effective_routes if _is_target_route(route.category)
    )
    route_blocks = {
        block.block_index
        for route in target_routes
        for block in route.blocks
    }
    route_headings = {
        route.heading.block_index
        for route in target_routes
    }
    custom_blocks, custom_headings = _custom_recommendation_blocks(
        document,
        target_routes,
    )
    explicit_route_blocks = {
        block.block_index
        for route in target_routes
        if isinstance(route.heading, ParagraphBlock)
        and _is_recommendation_heading(route.heading)
        for block in route.blocks
    }
    explicit_route_headings = {
        route.heading.block_index
        for route in target_routes
        if isinstance(route.heading, ParagraphBlock)
        and _is_recommendation_heading(route.heading)
    }
    # An explicit recommendation section is a stronger source boundary than
    # a broad route inferred from a summary table or historical narrative.
    # Do not mix the two sources once that section is available.
    preferred_blocks = explicit_route_blocks or custom_blocks or route_blocks
    preferred_headings = (
        explicit_route_headings
        or custom_headings
        or route_headings
    )
    explicit_preferred_blocks = {
        block.block_index
        for block in document.blocks
        if not explicit_route_blocks
        and not custom_blocks
        and isinstance(block, ParagraphBlock)
        and block.block_index not in preferred_blocks
        and _is_explicit_numbered_recommendation(block.raw_text)
    }

    fallback_blocks: set[int] = set()
    for block in document.blocks:
        if block.block_index in preferred_blocks | explicit_preferred_blocks:
            continue
        if isinstance(block, TableBlock):
            if _looks_like_recommendation_table(block):
                fallback_blocks.add(block.block_index)
        elif isinstance(block, ParagraphBlock) and _looks_like_recommendation_paragraph(
            block.raw_text
        ):
            fallback_blocks.add(block.block_index)

    candidates: list[_Candidate] = []
    previous_paragraph_candidate: int | None = None
    previous_paragraph_block: int | None = None
    previous_paragraph_numbered = False
    previous_paragraph_preferred = False

    for block in document.blocks:
        in_preferred = block.block_index in preferred_blocks | explicit_preferred_blocks
        in_fallback = block.block_index in fallback_blocks
        if not in_preferred and not in_fallback:
            previous_paragraph_candidate = None
            previous_paragraph_block = None
            previous_paragraph_numbered = False
            previous_paragraph_preferred = False
            continue

        if isinstance(block, TableBlock):
            if not _looks_like_recommendation_table(block):
                previous_paragraph_candidate = None
                previous_paragraph_block = None
                previous_paragraph_numbered = False
                previous_paragraph_preferred = False
                continue
            table_candidates = _table_candidates(block)
            for candidate in table_candidates:
                candidate.preferred = in_preferred
            candidates.extend(table_candidates)
            previous_paragraph_candidate = None
            previous_paragraph_block = None
            previous_paragraph_numbered = False
            previous_paragraph_preferred = False
            continue

        if block.block_index in preferred_headings and _is_heading_only(block.raw_text):
            previous_paragraph_candidate = None
            previous_paragraph_block = None
            previous_paragraph_numbered = False
            previous_paragraph_preferred = False
            continue

        paragraph_candidates = _paragraph_candidates(
            block,
            allow_plain_text=in_preferred,
        )
        if not paragraph_candidates:
            previous_paragraph_candidate = None
            previous_paragraph_block = None
            previous_paragraph_numbered = False
            previous_paragraph_preferred = False
            continue

        contiguous = (
            previous_paragraph_block is not None
            and block.block_index == previous_paragraph_block + 1
        )
        if (
            contiguous
            and previous_paragraph_candidate is not None
            and previous_paragraph_preferred == in_preferred
            and not paragraph_candidates[0].numbered
            and (
                previous_paragraph_numbered
                or _is_continuation_text(paragraph_candidates[0].content)
            )
            and len(paragraph_candidates) == 1
        ):
            _merge_continuation(
                candidates[previous_paragraph_candidate],
                paragraph_candidates[0],
                block.source,
            )
            previous_paragraph_block = block.block_index
            previous_paragraph_numbered = previous_paragraph_numbered
            continue

        for candidate in paragraph_candidates:
            candidate.preferred = in_preferred
        candidates.extend(paragraph_candidates)
        previous_paragraph_candidate = len(candidates) - 1
        previous_paragraph_block = block.block_index
        previous_paragraph_numbered = any(
            candidate.numbered for candidate in paragraph_candidates
        )
        previous_paragraph_preferred = in_preferred

    return _finalise(candidates, infer_categories=infer_categories)


def _is_target_route(category: object) -> bool:
    value = getattr(category, "value", category)
    return str(value) in _TARGET_CATEGORIES


def _custom_recommendation_blocks(
    document: DocumentModel,
    target_routes: Sequence[SectionRoute],
) -> tuple[set[int], set[int]]:
    """Recover recommendation sections whose heading won another route."""

    target_blocks = {
        block.block_index
        for route in target_routes
        for block in route.blocks
    }
    preferred_blocks: set[int] = set()
    preferred_headings: set[int] = set()
    blocks = document.blocks
    for position, block in enumerate(blocks):
        if not isinstance(block, ParagraphBlock):
            continue
        if block.block_index in target_blocks or not _is_recommendation_heading(block):
            continue
        end = _custom_section_end(blocks, position)
        if any(
            route.heading.block_index > block.block_index
            and route.heading.block_index < end
            and _is_recommendation_heading(route.heading)
            for route in target_routes
        ):
            continue
        preferred_headings.add(block.block_index)
        preferred_blocks.update(item.block_index for item in blocks[position:])
        preferred_blocks.difference_update(
            item.block_index for item in blocks[end:]
        )
    return preferred_blocks, preferred_headings


def _custom_section_end(blocks: Sequence[object], start: int) -> int:
    heading = blocks[start]
    start_level = _heading_level_value(heading)
    blank_run = 0
    for position in range(start + 1, len(blocks)):
        block = blocks[position]
        if isinstance(block, ParagraphBlock) and not _compact(block.raw_text):
            blank_run += 1
            if blank_run >= 3:
                return position - blank_run + 1
        else:
            blank_run = 0
        if not isinstance(block, ParagraphBlock):
            continue
        level = _heading_level_value(block)
        if start_level is not None and level is not None and level <= start_level:
            if _is_recommendation_heading(block):
                continue
            return position
        if start_level is None and (level is not None or _is_heading_only(block.raw_text)):
            return position
    return len(blocks)


def _heading_level_value(block: object) -> int | None:
    level = getattr(block, "heading_level", None)
    if isinstance(level, int):
        return max(1, level)
    style_id = str(getattr(block, "style_id", "") or "").strip()
    match = re.search(r"(?:heading|标题)[ _-]?([1-9])", style_id, re.IGNORECASE)
    return int(match.group(1)) if match is not None else None


def _is_recommendation_heading(block: ParagraphBlock) -> bool:
    compact = _INDEX_RE.sub("", _compact(block.raw_text), count=1).strip("：:。.;；，,、")
    return any(
        compact == title or compact.endswith(title)
        for title in _RECOMMENDATION_HEADING_WORDS
    )


def _is_explicit_numbered_recommendation(text: str) -> bool:
    compact = _compact(text)
    stripped = _INDEX_RE.sub("", compact, count=1)
    if any(marker in compact for marker in _EXCLUDED_RECOMMENDATION_MARKERS):
        return False
    if stripped.startswith(_EXCLUDED_RECOMMENDATION_PREFIXES):
        return False
    return any(
        item.numbered and _is_recommendation_item(item.text, allow_monitoring=True)
        for item in _split_text_items(text)
    )


def _table_candidates(table: TableBlock) -> list[_Candidate]:
    rows = list(table.rows)
    mapping, header_index = _table_mapping(table)
    start = header_index + 1 if header_index is not None else 0
    result: list[_Candidate] = []
    for row in rows[start:]:
        if _is_repeated_header(row):
            continue
        fields, anchors = _table_row_fields(row, mapping, table.source)
        index, category, content, location = fields
        if not any((index, category, content, location)):
            continue
        if not content:
            if result and any((index, category, location)):
                _merge_table_continuation(result[-1], row, fields, anchors)
            continue
        fragments = _split_text_items(content)
        for fragment_number, fragment in enumerate(fragments):
            fragment_index = fragment.index or (index if fragment_number == 0 else "")
            result.append(
                _Candidate(
                    index=_clean_index(fragment_index),
                    category_hint=category,
                    content=fragment.text,
                    location=location,
                    anchors=list(anchors),
                    source_kind="table",
                    block_index=table.block_index,
                    numbered=fragment.numbered,
                )
            )
    return result


def _table_mapping(table: TableBlock) -> tuple[dict[str, int], int | None]:
    rows = list(table.rows)
    for header_index, row in enumerate(rows[:8]):
        mapping: dict[str, int] = {}
        for cell in row.cells:
            field_name = _header_field(cell.raw_text)
            if field_name is not None and field_name not in mapping:
                mapping[field_name] = cell.column_index
        if "content" in mapping and (
            "category" in mapping or "location" in mapping or "index" in mapping
        ):
            return mapping, header_index

    if rows:
        width = max((cell.column_index + cell.column_span for cell in rows[0].cells), default=0)
        if width >= 4:
            return {"index": 0, "category": 1, "content": 2, "location": 3}, None
        if width == 3:
            return {"index": 0, "content": 1, "location": 2}, None
        if width == 2:
            return {"content": 0, "location": 1}, None
    return {}, None


def _header_field(text: str) -> str | None:
    compact = _compact(text)
    if not compact:
        return None
    if any(marker in compact for marker in _INDEX_MARKERS):
        return "index"
    if any(marker in compact for marker in _CATEGORY_MARKERS):
        return "category"
    if any(marker in compact for marker in _LOCATION_MARKERS):
        return "location"
    if any(marker in compact for marker in _CONTENT_MARKERS):
        return "content"
    return None


def _table_row_fields(
    row: TableRow,
    mapping: Mapping[str, int],
    table_source: SourceAnchor,
) -> tuple[tuple[str, str, str, str], tuple[SourceAnchor, ...]]:
    values: dict[str, str] = {}
    anchors: list[SourceAnchor] = []
    for field_name in ("index", "category", "content", "location"):
        column = mapping.get(field_name)
        if column is None:
            values[field_name] = ""
            continue
        cell = _cell_at(row, column)
        if cell is None:
            values[field_name] = ""
            continue
        values[field_name] = _clean_text(cell.raw_text)
        if values[field_name] and cell.source is not None:
            anchors.append(cell.source)
    if not anchors:
        anchors.append(table_source)
    return (
        (
            values.get("index", ""),
            values.get("category", ""),
            values.get("content", ""),
            values.get("location", ""),
        ),
        _unique_anchors(anchors),
    )


def _cell_at(row: TableRow, column: int) -> TableCell | None:
    for cell in row.cells:
        if cell.column_index <= column < cell.column_index + max(1, cell.column_span):
            return cell
    return None


def _merge_table_continuation(
    candidate: _Candidate,
    row: TableRow,
    fields: tuple[str, str, str, str],
    anchors: Iterable[SourceAnchor],
) -> None:
    index, category, content, location = fields
    if content:
        candidate.content = _join_content(candidate.content, content)
    if category and not candidate.category_hint:
        candidate.category_hint = category
    if location and not candidate.location:
        candidate.location = location
    candidate.anchors = list(_unique_anchors((*candidate.anchors, *anchors)))


def _paragraph_candidates(
    block: ParagraphBlock,
    *,
    allow_plain_text: bool,
) -> list[_Candidate]:
    items = _split_text_items(block.raw_text)
    block_looks_like_recommendation = _looks_like_recommendation_paragraph(
        block.raw_text,
        allow_inspection=allow_plain_text,
    )
    items = tuple(
        item
        for item in items
        if block_looks_like_recommendation
        or (
            allow_plain_text
            and item.numbered
            and _is_recommendation_item(item.text, allow_monitoring=True)
        )
        or _is_recommendation_continuation(item.text)
    )
    result: list[_Candidate] = []
    for item in items:
        content = _clean_text(item.text)
        if not content:
            continue
        category, body = _category_fields(content)
        location, body = _location_fields(body)
        if allow_plain_text and not location:
            location = "桥梁"
        result.append(
            _Candidate(
                index=_clean_index(item.index),
                category_hint=category,
                content=body,
                location=location,
                anchors=[block.source],
                source_kind="paragraph",
                block_index=block.block_index,
                numbered=item.numbered,
            )
        )
    return result


def _split_text_items(text: str) -> tuple[_TextItem, ...]:
    text = text.replace("\u00a0", " ")
    numbered = _numbered_items(text)
    if numbered:
        return tuple(
            _TextItem(
                index=item.index,
                text=_clean_text(item.text),
                numbered=True,
            )
            for item in numbered
            if _clean_text(item.text)
        )

    result = []
    for line in _LINE_SEPARATOR_RE.split(text):
        pieces = _ITEM_SEPARATOR_RE.split(line)
        if len(pieces) > 1 and not all(
            _looks_like_recommendation_paragraph(piece) for piece in pieces
        ):
            pieces = [line]
        for piece in pieces:
            piece = _clean_text(piece)
            if piece:
                result.append(_TextItem(index="", text=piece, numbered=False))
    return tuple(result)


def _numbered_items(text: str) -> tuple[_TextItem, ...]:
    matches = []
    for match in _INDEX_RE.finditer(text):
        if match.start() > 0 and text[match.start() - 1] not in " \t\r\n;；。！？.!?)]）】":
            continue
        matches.append(match)
    if not matches:
        return ()
    result: list[_TextItem] = []
    if matches[0].start() > 0:
        prefix = _clean_text(text[: matches[0].start()])
        if prefix:
            result.append(_TextItem(index="", text=prefix, numbered=False))
    for number, match in enumerate(matches):
        end = matches[number + 1].start() if number + 1 < len(matches) else len(text)
        body = _clean_text(text[match.end() : end])
        if body:
            result.append(
                _TextItem(
                    index=_clean_index(match.group(0)),
                    text=body,
                    numbered=True,
                )
            )
    return tuple(result)


def _category_fields(text: str) -> tuple[str, str]:
    categories = _categories_in(text)
    if len(categories) != 1:
        return (categories[0] if len(categories) == 1 else ""), text
    category = categories[0]
    match = re.match(
        rf"^(?:建议(?:类别|类型)?\s*[:：]?\s*)?{re.escape(category)}"
        r"\s*(?:[:：,，、—-]\s*)?",
        text,
    )
    if match is not None:
        return category, text[match.end() :].strip()
    return category, text


def _resolve_category(
    category_hint: str,
    content: str,
    *,
    infer_categories: bool = False,
) -> tuple[str, bool]:
    hint_categories = _categories_in(category_hint)
    content_categories = _categories_in(content)
    if len(hint_categories) > 1:
        return "", True
    if hint_categories:
        if content_categories and content_categories != hint_categories:
            return "", True
        return hint_categories[0], False
    if len(content_categories) == 1:
        return content_categories[0], False
    if infer_categories:
        inferred = _infer_category(content)
        if inferred:
            return inferred, False
    return "", True


def _infer_category(content: str) -> str | None:
    """Apply the current Gold-derived lexical category policy.

    A concrete repair action (surface sealing, grouting, patching, hardening,
    or replacement) is treated as the dominant intent: it overrides generic
    monitoring markers such as ``定期观测``, which appear inside repair
    procedures on their own.
    """

    compact = _compact(content)
    if "立即维修" in compact or "立即修" in compact:
        return "立即维修"
    if any(
        marker in compact
        for marker in (
            "立即处置",
            "立即处理",
            "危及安全",
            "危及结构安全",
            "恢复缺失",
            "变形严重",
        )
    ):
        return "立即处置"
    if any(
        marker in compact
        for marker in (
            "表面封闭处理",
            "封闭处理",
            "压力灌浆",
            "灌浆修补",
            "修补处理",
            "及时进行修复",
            "进行修补",
            "加固处理",
            "更换止水带",
            "及时修复",
        )
    ):
        return "尽快维修"
    if any(
        marker in compact
        for marker in (
            "定期检查",
            "日常检查",
            "日常养护",
            "日常维护",
            "定期观测",
            "检查",
            "观测",
            "巡查",
            "建立该桥",
            "连续性技术档案",
            "严禁行人",
            "严禁超载",
            "严禁超速",
            "加强社会车辆",
            "设置明显的标识",
            "加强桥梁的观测",
        )
    ):
        return "预防性养护"
    if any(
        marker in compact
        for marker in (
            "维修",
            "修复",
            "修补",
            "修理",
            "中修",
            "大修",
            "小修",
            "处理",
            "清理",
            "更换",
            "封闭",
            "浇注",
            "灌浆",
            "堵漏",
            "补强",
            "加固",
            "除锈",
            "涂刷",
            "疏通",
            "恢复",
            "重装",
        )
    ):
        return "尽快维修"
    return None


def _categories_in(text: str) -> list[str]:
    return [
        category
        for category in RECOMMENDATION_CATEGORIES
        if category in text
    ]


def _location_fields(text: str) -> tuple[str, str]:
    text = _clean_text(text)
    if not text:
        return "", ""

    recommendation_location = _recommendation_location(text)
    if recommendation_location and _INFERRED_LOCATION_RE.search(text) is None:
        return recommendation_location, text

    labelled = _LOCATION_LABEL_RE.match(text)
    if labelled is not None:
        remainder = labelled.group(1).strip()
        separator = re.search(r"[，,;；。]", remainder)
        if separator is None:
            return _clean_location_phrase(remainder), ""
        location = _clean_location(remainder[: separator.start()])
        return _clean_location_phrase(location), _clean_text(remainder[separator.end() :])

    if "：" in text or ":" in text:
        separator = re.search(r"[:：]", text)
        assert separator is not None
        prefix = _clean_text(text[: separator.start()])
        remainder = _clean_text(text[separator.end() :])
        if prefix and _is_location_prefix(prefix):
            return _clean_location_phrase(prefix), remainder

    inferred = _INFERRED_LOCATION_RE.search(text)
    if inferred is not None:
        location = _compound_location(text, inferred)
        if location:
            return location, text

    leading = re.match(
        r"^(.{1,50}?)(?=(?:存在|均存在|出现|局部|多处|破损|裂缝|渗水|露筋|锈蚀|缺失|病害|进行(?:清理|维修|修复|修补|处理|养护|维护|更换)))",
        text,
    )
    if leading is not None:
        location = _clean_location_phrase(leading.group(1))
        if location:
            return location, text

    relation_location = re.match(
        r"^(?:对于|针对|对)(?:存在|有|出现)?[^，,;；。:：]{0,40}?"
        r"(桥面铺装|防撞护栏|防撞栏杆|伸缩缝|顶板|底板|腹板|侧墙|桥台|盖梁|主梁|梁体|梁底|板底|栏杆|护栏|支座|锥坡|桥面|桥梁)"
        r"(?=(?:存在|有|出现|多处|局部|进行|采取|及时|应|建议|维修|修复|修补|中修|大修|小修|养护|处理|清理|更换))",
        text,
    )
    if relation_location is not None:
        return relation_location.group(1), text

    for action in ("清理", "维修", "修复", "修补", "处理", "养护", "维护", "设置"):
        match = re.search(rf"{action}(?:[^，,;；。:：]{{0,8}})(桥面|桥梁|伸缩缝)", text)
        if match is not None:
            return match.group(1), text
    generic = _generic_location(text)
    if generic:
        return generic, text
    if "桥梁" in text and any(
        marker in text for marker in ("做好", "加强", "设置", "养护", "维护", "检查", "观测")
    ):
        return "桥梁", text
    return "", text


def _clean_inferred_location(value: str) -> str:
    location = re.sub(_DANGLING_LOCATION_RE, "", value)
    location = re.sub(_INFERRED_SUFFIX_RE, "", location)
    location = re.sub(_DANGLING_LOCATION_RE, "", location)
    return _normalise_location(_clean_location(location))


def _normalise_location(location: str) -> str:
    if not location:
        return ""
    for raw, replacement in _JUXTAPOSED_LOCATIONS.items():
        if location == raw:
            return replacement
    location = _JOINER_NORMALISE_RE.sub("、", location)
    for marker in _SPECIFIC_AFTER_QIAOMIAN:
        if location.startswith("桥面") and location[len("桥面") :].startswith(marker):
            return location[len("桥面") :]
    return location


def _generic_location(text: str) -> str:
    compact = _compact(text)
    for marker, location in _GENERIC_LOCATION_MARKERS:
        if marker in compact:
            return location
    return ""


def _recommendation_location(text: str) -> str:
    compact = _compact(text)
    if not compact:
        return ""
    if (
        "主梁" in compact
        and "横向联系" in compact
    ):
        return "主梁、横向联系"
    if "斜拉索" in compact:
        return "斜拉索"
    if (
        "主桥" in compact
        and "引桥" in compact
        and "上部结构" in compact
        and "下部结构" in compact
        and "桥面系" in compact
    ):
        return "全桥"
    components: list[str] = []
    if re.search(r"桥墩|墩柱|\d+#?墩|墩", compact):
        components.append("桥墩")
    if re.search(r"桥台|\d+#?台|台浸水|台裂缝|台前墙", compact):
        components.append("桥台")
    if components:
        return "、".join(dict.fromkeys(components))
    if "桥面系" in compact:
        return "桥面系"
    if "桥梁" in compact or "大桥" in compact:
        return "桥梁"
    return ""


def _compound_location(text: str, inferred: re.Match[str]) -> str:
    clause_end = re.search(r"[，,;；。:：]", text[inferred.start() :])
    end = inferred.start() + clause_end.start() if clause_end is not None else len(text)
    clause = text[inferred.start() : end]
    relation = re.match(r"(?:对于|由于|针对|对|在|于)", clause)
    body = clause[relation.end() :] if relation is not None else clause
    if re.search(r"和|及(?!时)|与|、", body):
        parts = re.split(r"和|及(?!时)|与|、", body)
        locations = [_clean_location_phrase(part) for part in parts]
        locations = [location for location in locations if location]
        if len(locations) >= 2:
            return "、".join(locations)
    return _clean_location_phrase(inferred.group(1))


def _clean_location_phrase(value: str) -> str:
    location = _clean_location(value)
    location = location.replace("桥面系", "桥面")
    location = re.sub(r"^(?:对于|由于|针对|对|在|于)", "", location)
    location = re.sub(_DANGLING_LOCATION_RE, "", location)
    location = re.split(
        r"(?:存在|均存在|有|出现|多处|局部|采取|进行|及时|应|建议|病害)",
        location,
        maxsplit=1,
    )[0]
    location = re.sub(r"^(?:破损的|锈蚀的|变形的|缺失的|局部|多处|个别)", "", location)
    location = re.sub(r"^(?:两个|两处|多个|若干|该|本)(?=桥台|桥墩|桥梁|桥面|栏杆|护栏)", "", location)
    location = location.rstrip("的")
    location = re.sub(r"(?:和|与|及(?!时))", "、", location)
    location = re.sub(r"均$", "", location)
    location = re.sub(_INFERRED_SUFFIX_RE, "", location)
    changed = True
    while changed and location:
        changed = False
        for suffix in _LOCATION_SUFFIXES:
            if location.endswith(suffix):
                location = location[: -len(suffix)].rstrip("的")
                changed = True
                break
    for head in (
        "桥面铺装",
        "防撞护栏",
        "防撞栏杆",
        "伸缩缝",
        "顶板",
        "底板",
        "腹板",
        "侧墙",
        "桥台",
        "盖梁",
        "主梁",
        "梁体",
        "梁底",
        "板底",
        "栏杆",
        "护栏",
        "支座",
        "锥坡",
        "桥面",
        "桥梁",
    ):
        position = location.find(head)
        if position > 0 and not re.search(r"[、,，]", location) and (
            "处" in location[:position] or location.startswith(("桥面", "桥梁"))
        ):
            location = location[position:]
            break
    location = re.sub(_DANGLING_LOCATION_RE, "", location)
    return _normalise_location(_clean_location(location))


def _is_location_prefix(prefix: str) -> bool:
    compact = _compact(prefix)
    if not compact or any(word in compact for word in _ACTION_WORDS):
        return False
    if _CATEGORY_RE.search(compact):
        return False
    return True


def _is_continuation_text(text: str) -> bool:
    compact = _compact(text)
    return compact.startswith(_CONTINUATION_PREFIXES)


def _is_recommendation_continuation(text: str) -> bool:
    compact = _compact(text)
    if not _is_continuation_text(compact):
        return False
    if any(marker in compact for marker in _EXCLUDED_RECOMMENDATION_MARKERS):
        return False
    return any(word in compact for word in _STRONG_RECOMMENDATION_ACTION_WORDS)


def _merge_continuation(
    candidate: _Candidate,
    item: _Candidate,
    anchor: SourceAnchor,
) -> None:
    if item.content:
        candidate.content = _join_content(candidate.content, item.content)
    if item.category_hint and not candidate.category_hint:
        candidate.category_hint = item.category_hint
    if item.location and not candidate.location:
        candidate.location = item.location
    candidate.anchors = list(_unique_anchors((*candidate.anchors, anchor)))


def _finalise(
    candidates: Iterable[_Candidate],
    *,
    infer_categories: bool = False,
) -> RecommendationExtractionResult:
    candidate_list = list(candidates)
    if any(candidate.preferred for candidate in candidate_list):
        candidate_list = [
            candidate for candidate in candidate_list if candidate.preferred
        ]
    records: list[Recommendation] = []
    flags: list[dict[str, object]] = []
    for candidate in candidate_list:
        content = _clean_text(candidate.content)
        if not content:
            continue
        category, unresolved = _resolve_category(
            candidate.category_hint,
            content,
            infer_categories=infer_categories,
        )
        location = _clean_location(candidate.location)
        evidence = _unique_anchors(candidate.anchors)
        record = Recommendation(
            index=_clean_index(candidate.index),
            category=category,
            content=content,
            location=location,
            evidence=evidence,
        )
        records.append(record)
        if unresolved:
            anchor = evidence[0] if evidence else None
            flag: dict[str, object] = {
                "code": "recommendation_category_unresolved",
                "quality_flag": "recommendation_category_unresolved",
                "recommendation_index": record.index,
                "message": (
                    "Recommendation category is absent or not one of the allowed "
                    "categories."
                ),
            }
            if anchor is not None:
                flag["source"] = anchor.to_dict()
            flags.append(flag)
        elif infer_categories and not candidate.category_hint and not _categories_in(content):
            flags.append(
                {
                    "code": "recommendation_category_inferred",
                    "quality_flag": "recommendation_category_inferred",
                    "recommendation_index": record.index,
                    "message": "Recommendation category was assigned by the opt-in Gold-derived lexical policy.",
                }
            )
    return RecommendationExtractionResult(tuple(records), tuple(flags))


def _looks_like_recommendation_table(table: TableBlock) -> bool:
    mapping, header_index = _table_mapping(table)
    # Positional guesses are unsafe for long engineering tables: a later cell
    # mentioning “建议” must not turn an unrelated table into recommendations.
    if header_index is None or "content" not in mapping:
        return False
    header_text = _compact("".join(cell.raw_text for cell in table.rows[header_index].cells))
    if not any(marker in header_text for marker in ("建议", "维修", "养护", "处理", "处置", "处治")):
        return False
    return "category" in mapping or "location" in mapping or "index" in mapping


def _is_disease_statistics(text: str) -> bool:
    compact = _compact(text)
    if not _STATISTIC_COUNT_RE.search(compact):
        return False
    if not any(marker in compact for marker in _STATISTIC_DEFECT_WORDS):
        return False
    if any(marker in compact for marker in _DIRECTIVE_WORDS):
        return False
    return not bool(
        re.search(
            r"(?:对|对于|针对)[^。；]{0,40}?"
            r"(?:进行|采取|维修|修复|修补|处理|处置|处治|清理|检查|观测|养护|维护|封闭)",
            compact,
        )
    )


def _is_recommendation_item(text: str, *, allow_monitoring: bool) -> bool:
    compact = _compact(text)
    if not compact or _is_disease_statistics(compact):
        return False
    if any(action in compact for action in _REPAIR_ACTION_WORDS):
        return True
    if allow_monitoring and any(action in compact for action in _MONITORING_ACTION_WORDS):
        return any(marker in compact for marker in _DIRECTIVE_WORDS) or any(
            marker in compact for marker in ("定期", "日常", "变形观测", "监测")
        )
    return any(marker in compact for marker in _DIRECTIVE_WORDS)


def _looks_like_recommendation_paragraph(text: str, *, allow_inspection: bool = False) -> bool:
    compact = _compact(text)
    if not compact or _is_heading_only(compact):
        return False
    if _is_disease_statistics(compact):
        return False
    index_prefix = _INDEX_RE.match(compact)
    stripped = compact[index_prefix.end() :] if index_prefix is not None else compact
    has_index = bool(_numbered_items(text))
    action_text = re.sub(r"《[^》]*》", "", compact)
    has_repair_action = any(word in action_text for word in _REPAIR_ACTION_WORDS)
    has_monitoring_action = any(
        word in action_text for word in _MONITORING_ACTION_WORDS
    )
    has_directive = any(word in action_text for word in _DIRECTIVE_WORDS)
    has_recommendation_action = has_repair_action or (
        allow_inspection and has_monitoring_action
    ) or has_directive
    if has_monitoring_action and not has_repair_action and not allow_inspection:
        return False
    explicit_repair = bool(
        re.search(
            r"(?:应|需|须|建议|立即|及时|尽快|进行|采取|做好|加强).{0,30}?"
            r"(?:维修|修复|修补|中修|大修|小修|清理|处理|更换|加固|灌浆|除锈|疏通|养护|维护|封闭|观测|检查)",
            action_text,
        )
    )
    if any(marker in compact for marker in _EXCLUDED_RECOMMENDATION_MARKERS):
        if not (allow_inspection and has_index and "技术状况" in compact and explicit_repair):
            return False
    if stripped.startswith(_EXCLUDED_RECOMMENDATION_PREFIXES):
        return False
    if not has_index and any(
        marker in compact
        for marker in ("如下建议", "建议如下", "作出如下", "提出如下")
    ):
        return False
    return has_recommendation_action and _has_location_context(compact)


def _has_location_context(compact: str) -> bool:
    """Keep fallback paragraphs tied to a concrete maintenance location.

    Narrative paragraphs often contain an action word and a colon while
    describing a test method, cause, conclusion, or metadata row.  Fallback
    extraction must therefore require a short location-like prefix and reject
    those narrative markers.  Explicit route handling is applied by the
    caller before this location check.
    """

    location, _ = _location_fields(compact)
    return bool(location)


def _is_heading_only(text: str) -> bool:
    compact = _compact(text).strip("：:。.;；，,、")
    if not compact:
        return True
    return any(
        compact == title or (len(compact) <= 20 and compact.endswith(title))
        for title in _TITLE_WORDS
    )


def _is_repeated_header(row: TableRow) -> bool:
    fields = [_compact(cell.raw_text) for cell in row.cells]
    mapped = {_header_field(field) for field in fields}
    return "content" in mapped and bool(mapped & {"category", "location", "index"})


def _clean_index(value: str) -> str:
    return _clean_text(value).strip(" .、:：)]）")


def _clean_location(value: str) -> str:
    return _clean_text(value).strip("：:，,;；")


def _clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[ \t\r\n]+", " ", value)
    return value.strip(" \t")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).translate(
        str.maketrans({"．": ".", "（": "(", "）": ")", "：": ":"})
    )


def _join_content(left: str, right: str) -> str:
    left = _clean_text(left)
    right = _clean_text(right)
    if not left:
        return right
    if not right:
        return left
    return f"{left} {right}"


def _unique_anchors(anchors: Iterable[SourceAnchor]) -> tuple[SourceAnchor, ...]:
    result: list[SourceAnchor] = []
    seen: set[SourceAnchor] = set()
    for anchor in anchors:
        if anchor not in seen:
            seen.add(anchor)
            result.append(anchor)
    return tuple(result)
