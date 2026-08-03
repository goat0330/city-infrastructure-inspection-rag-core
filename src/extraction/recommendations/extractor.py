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
    r"|[0-9]+(?:\.[0-9]+)+[、.:：)）]?"
    r"|[0-9]+[、.:：)）]"
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
    "维修建议",
    "养护建议",
    "处理建议",
    "处置建议",
    "处治建议",
    "建议明细表",
)
_ACTION_WORDS = (
    "建议",
    "维修",
    "养护",
    "修复",
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
    "修复",
    "修补",
    "加固",
    "更换",
    "清理",
    "处理",
    "处置",
    "处治",
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
    r"(?=(?:存在|有|出现|多处|局部|设置|采取|进行|及时|应|建议|修补|维修|养护|处理|处置|处治|清理|检查|做好|严格|破损|裂缝|渗水|露筋|锈蚀|缺失|病害|等病害))"
)


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

    fallback_blocks: set[int] = set()
    for block in document.blocks:
        if block.block_index in route_blocks:
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

    for block in document.blocks:
        in_target_route = block.block_index in route_blocks
        in_fallback = block.block_index in fallback_blocks
        if not in_target_route and not in_fallback:
            previous_paragraph_candidate = None
            previous_paragraph_block = None
            previous_paragraph_numbered = False
            continue

        if isinstance(block, TableBlock):
            if not _looks_like_recommendation_table(block):
                previous_paragraph_candidate = None
                previous_paragraph_block = None
                previous_paragraph_numbered = False
                continue
            table_candidates = _table_candidates(block)
            candidates.extend(table_candidates)
            previous_paragraph_candidate = None
            previous_paragraph_block = None
            previous_paragraph_numbered = False
            continue

        if block.block_index in route_headings and _is_heading_only(block.raw_text):
            previous_paragraph_candidate = None
            previous_paragraph_block = None
            previous_paragraph_numbered = False
            continue

        paragraph_candidates = _paragraph_candidates(
            block,
            allow_plain_text=in_target_route,
        )
        if not paragraph_candidates:
            previous_paragraph_candidate = None
            previous_paragraph_block = None
            previous_paragraph_numbered = False
            continue

        contiguous = (
            previous_paragraph_block is not None
            and block.block_index == previous_paragraph_block + 1
        )
        if (
            contiguous
            and previous_paragraph_candidate is not None
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

        candidates.extend(paragraph_candidates)
        previous_paragraph_candidate = len(candidates) - 1
        previous_paragraph_block = block.block_index
        previous_paragraph_numbered = any(
            candidate.numbered for candidate in paragraph_candidates
        )

    return _finalise(candidates, infer_categories=infer_categories)


def _is_target_route(category: object) -> bool:
    value = getattr(category, "value", category)
    return str(value) in _TARGET_CATEGORIES


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
        if block_looks_like_recommendation or _is_continuation_text(item.text)
    )
    result: list[_Candidate] = []
    for item in items:
        content = _clean_text(item.text)
        if not content:
            continue
        category, body = _category_fields(content)
        location, body = _location_fields(body)
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
        result: list[_TextItem] = []
        for item in numbered:
            pieces = _ITEM_SEPARATOR_RE.split(item.text)
            for piece_index, piece in enumerate(pieces):
                piece = _clean_text(piece)
                if not piece:
                    continue
                result.append(
                    _TextItem(
                        index=item.index if piece_index == 0 else "",
                        text=piece,
                        numbered=True,
                    )
                )
        return tuple(result)

    result = []
    for line in _LINE_SEPARATOR_RE.split(text):
        for piece in _ITEM_SEPARATOR_RE.split(line):
            piece = _clean_text(piece)
            if piece:
                result.append(_TextItem(index="", text=piece, numbered=False))
    return tuple(result)


def _numbered_items(text: str) -> tuple[_TextItem, ...]:
    matches = []
    for match in _INDEX_RE.finditer(text):
        if match.start() > 0 and text[match.start() - 1] not in " \t\r\n;；。！？.!?":
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
    """Apply the current Gold-derived lexical category policy."""

    compact = _compact(content)
    if any(marker in compact for marker in ("恢复缺失", "立即处置", "变形严重")):
        return "立即处置"
    if any(
        marker in compact
        for marker in (
            "定期检查",
            "日常检查",
            "日常养护",
            "日常维护",
            "定期观测",
            "建立该桥",
            "连续性技术档案",
            "严禁行人",
            "加强社会车辆",
            "设置明显的标识",
            "加强桥梁的观测",
        )
    ):
        return "预防性养护"
    return "尽快维修"


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

    labelled = _LOCATION_LABEL_RE.match(text)
    if labelled is not None:
        remainder = labelled.group(1).strip()
        separator = re.search(r"[，,;；。]", remainder)
        if separator is None:
            return _clean_location(remainder), ""
        location = _clean_location(remainder[: separator.start()])
        return location, _clean_text(remainder[separator.end() :])

    if "：" in text or ":" in text:
        separator = re.search(r"[:：]", text)
        assert separator is not None
        prefix = _clean_text(text[: separator.start()])
        remainder = _clean_text(text[separator.end() :])
        if prefix and _is_location_prefix(prefix):
            return _clean_location(prefix), remainder

    inferred = _INFERRED_LOCATION_RE.search(text)
    if inferred is not None:
        location = inferred.group(1)
        location = re.sub(
            r"(?:纵向裂缝|横向裂缝|裂缝|破损|锈蚀|渗水|露筋|缺失|病害)$",
            "",
            location,
        )
        location = re.sub(r"(?:的|多处|局部|附近)$", "", location)
        return _clean_location(location), text
    if "桥梁" in text and text.startswith("建议"):
        return "桥梁", text
    return "", text


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
    records: list[Recommendation] = []
    flags: list[dict[str, object]] = []
    for candidate in candidates:
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


def _looks_like_recommendation_paragraph(text: str, *, allow_inspection: bool = False) -> bool:
    compact = _compact(text)
    if not compact or _is_heading_only(compact):
        return False
    if _CATEGORY_RE.search(compact):
        return True
    has_index = bool(_numbered_items(text))
    has_recommendation_action = any(word in compact for word in _RECOMMENDATION_ACTION_WORDS)
    if not has_index and any(
        marker in compact for marker in ("如下建议", "作出如下", "提出如下")
    ):
        return False
    if allow_inspection and has_index and (
        has_recommendation_action or "检查" in compact
    ):
        return True
    if has_recommendation_action and _has_location_context(compact):
        return True
    return compact.startswith(("建议", "维修", "养护", "处理", "处置", "处治"))


def _has_location_context(compact: str) -> bool:
    """Keep fallback paragraphs tied to a concrete maintenance location.

    Narrative paragraphs often contain an action word and a colon while
    describing a test method, cause, conclusion, or metadata row.  Fallback
    extraction must therefore require a short location-like prefix and reject
    those narrative markers.  Explicit recommendation routes remain more
    permissive through ``allow_inspection``.
    """

    if re.search(r"(?:病害|维修|养护|处理|处置|处治)?(?:部位|位置|构件|范围)[:：]", compact):
        return True

    separator = re.search(r"[:：]", compact)
    if separator is None:
        return False
    prefix = compact[: separator.start()]
    if not prefix or len(prefix) > 30:
        return False
    if any(
        marker in prefix
        for marker in (
            "原因",
            "结论",
            "结果",
            "试验",
            "评估",
            "评定",
            "等级",
            "状况",
            "状态",
            "名称",
            "路名",
            "单位",
            "时间",
            "项目",
        )
    ):
        return False
    return True


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
    return value.strip(" \t;；")


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
