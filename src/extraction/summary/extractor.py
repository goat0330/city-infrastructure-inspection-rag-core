"""Deterministic extraction of bridge identity, scores, dates, and summary facts.

The extractor consumes the native :class:`~src.contracts.DocumentModel` produced
by ``parse_docx``.  It deliberately keeps every observed candidate and its Word
anchor; the selected value is only a deterministic view over those candidates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

from ...contracts import (
    BridgeSummary,
    DocumentModel,
    ParagraphBlock,
    SourceAnchor,
    TableBlock,
)
from ...routing import route_sections


MISSING_VALUE = "missing_value"
CONFLICTING_CANDIDATES = "conflicting_candidates"

_SUMMARY_FIELDS = (
    "bridge_name",
    "bridge_id",
    "report_date",
    "overall_score",
    "overall_grade",
    "superstructure_score",
    "superstructure_grade",
    "substructure_score",
    "substructure_grade",
    "deck_score",
    "deck_grade",
    "previous_overall_score",
    "previous_overall_grade",
    "trend",
    "overall_conclusion",
    "risk_points",
    "recommendations_summary",
)

_SCORE_FIELDS = {
    "overall_score",
    "overall_grade",
    "superstructure_score",
    "superstructure_grade",
    "substructure_score",
    "substructure_grade",
    "deck_score",
    "deck_grade",
    "previous_overall_score",
    "previous_overall_grade",
}


def _normalised_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[\s:：=|,，;；。．.（）()\[\]【】]", "", value).casefold()

# These are intentionally labels, not semantic guesses.  A score and a grade
# are added independently whenever the source states them independently.
_ALIASES: dict[str, tuple[str, ...]] = {
    "bridge_name": ("桥梁名称", "桥名", "项目名称"),
    "bridge_id": ("桥梁编号", "桥梁ID", "桥梁Id", "桥梁id"),
    "report_date": ("报告日期", "出具日期", "报告出具日期", "签发日期", "签字日期", "检测日期", "检测时间"),
    "overall_score": (
        "总体技术状况评分",
        "总体技术状况得分",
        "总体评分",
        "总体分数",
        "总体得分",
        "总评评分",
        "总评得分",
    ),
    "overall_grade": (
        "总体技术状况等级",
        "总体技术状况级别",
        "总体等级",
        "总体级别",
        "总评等级",
        "总评级别",
    ),
    "superstructure_score": (
        "上部结构技术状况评分",
        "上部结构评分",
        "上部结构分数",
        "上部结构得分",
    ),
    "superstructure_grade": (
        "上部结构技术状况等级",
        "上部结构技术状况级别",
        "上部结构等级",
        "上部结构级别",
    ),
    "substructure_score": (
        "下部结构技术状况评分",
        "下部结构评分",
        "下部结构分数",
        "下部结构得分",
    ),
    "substructure_grade": (
        "下部结构技术状况等级",
        "下部结构技术状况级别",
        "下部结构等级",
        "下部结构级别",
    ),
    "deck_score": (
        "桥面系技术状况评分",
        "桥面系评分",
        "桥面系分数",
        "桥面系得分",
    ),
    "deck_grade": (
        "桥面系技术状况等级",
        "桥面系技术状况级别",
        "桥面系等级",
        "桥面系级别",
    ),
    "previous_overall_score": (
        "上一次总体技术状况评分",
        "上一次总体评分",
        "上次总体评分",
        "上年度总体评分",
        "往年总体评分",
        "历史总体评分",
    ),
    "previous_overall_grade": (
        "上一次总体技术状况等级",
        "上一次总体等级",
        "上次总体等级",
        "上年度总体等级",
        "往年总体等级",
        "历史总体等级",
    ),
    "trend": ("病害发展趋势与具体说明", "病害发展趋势", "发展趋势"),
    "overall_conclusion": ("总体结论", "检测结论", "检查结论"),
    "risk_points": ("主要风险点", "风险点", "主要风险", "安全风险", "安全隐患"),
    "recommendations_summary": ("建议", "建议汇总", "建议概况"),
}

_ALIAS_TO_FIELD = {
    _normalised_key(alias): field
    for field, aliases in _ALIASES.items()
    for alias in aliases
}
_SORTED_ALIASES = sorted(
    ((alias, field) for field, aliases in _ALIASES.items() for alias in aliases),
    key=lambda item: (-len(_normalised_key(item[0])), item[0]),
)

_SOURCE_PRIORITY = {
    "overall_assessment_table": 400,
    "cover": 320,
    "section_score_table": 300,
    "section_score": 280,
    "sign": 260,
    "summary_page": 200,
    "detection": 180,
    "conclusion": 100,
    "safety_assessment": 100,
    "paragraph": 80,
    "recommendations_table": 300,
    "bci": 500,
    "project_name": 50,
}

_DATE_PRIORITY = {"cover": 300, "sign": 250, "detection": 200}
_DATE_RE = re.compile(
    r"(?:19|20)\d{2}(?:年\s*(?:0?[1-9]|1[0-2])月(?:\s*(?:0?[1-9]|[12]\d|3[01])日)?|[./-]\s*(?:0?[1-9]|1[0-2])(?:[./-]\s*(?:0?[1-9]|[12]\d|3[01]))?)"
)
_SCORE_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")
_GRADE_RE = re.compile(r"(?:[A-Ea-e]\s*级?|[一二三四五六]类|优等?|良好?|中等?|差)")
_RECOMMENDATION_COUNT_RE = re.compile(r"(\d+)\s*条")

_BCI_SCORE_RE = re.compile(r"BCI\s*([mMsSxX]?)\s*[=＝]\s*(\d+(?:\.\d+)?)")
_BCI_COMPONENT = {"m": "deck", "s": "superstructure", "x": "substructure"}
_GRADE_AFTER_RE = re.compile(r"评定(?:为)?\s*([A-Ea-e]\s*级|[一二三四五六]类)")
_OVERALL_GRADE_RE = re.compile(
    r"(?<!下部结构)(?<!上部结构)(?<!桥面系)整体技术状况等级(?:评定|定)?为\s*([A-Ea-e]\s*级)"
)
_UNDERPASS_GRADE_RE = re.compile(r"技术状况总评\s*[，,、\t\s]*([一二三四五六]类)")

_CN_DIGIT: dict[str, int] = {character: 0 for character in "〇零○ＯＯO0"}
_CN_DIGIT.update({"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9})
_CN_DATE_RE = re.compile(
    r"([〇零○ＯＯO0一二三四五六七八九]{4})年\s*([〇零○ＯＯO0一二三四五六七八九十]+)月(?:\s*([〇零○ＯＯO0一二三四五六七八九十]+)日)?"
)

_SCORE_MARKERS = ("评分", "分数", "得分", "等级", "级别")
_OVERALL_ASSESSMENT_MARKERS = (
    "总体技术状况评定",
    "总体技术状况评价",
    "总体技术状况等级",
    "总体评定",
    "总体评价",
    "技术状况评定结果",
    "评定结果",
    "总体评定表",
)
_RECOMMENDATION_MARKERS = (
    "建议类别",
    "建议内容",
    "维修建议",
    "养护建议",
    "建议明细",
)
_ROUTE_SCORE = "scoring"
_ROUTE_CONCLUSION = "inspection_conclusion"
_ROUTE_SAFETY = "safety_assessment"


def _clean(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _source_sort_key(source: SourceAnchor | None) -> tuple[int, int, int, int, int]:
    if source is None:
        return (10**9, 10**9, 10**9, 10**9, 10**9)
    return (
        source.block_index,
        source.table_index if source.table_index is not None else 10**9,
        source.row_index if source.row_index is not None else 10**9,
        source.column_index if source.column_index is not None else 10**9,
        source.paragraph_index if source.paragraph_index is not None else 10**9,
    )


@dataclass(frozen=True)
class SummaryCandidate:
    """One observed field value with the Word location that supplied it."""

    field: str
    value: str
    source_kind: str
    source: SourceAnchor | None = None
    priority: int = 0
    label: str = ""
    date_kind: str | None = None

    @property
    def anchor(self) -> SourceAnchor | None:
        """Alias useful to callers that use the contract's anchor vocabulary."""

        return self.source

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "field": self.field,
            "value": self.value,
            "source_kind": self.source_kind,
            "priority": self.priority,
            "label": self.label,
        }
        if self.date_kind is not None:
            result["date_kind"] = self.date_kind
        result["source"] = self.source.to_dict() if self.source is not None else None
        return result

    def __getitem__(self, key: str) -> object:
        return self.to_dict()[key]


@dataclass(frozen=True)
class SummaryExtraction:
    """Selected summary values plus all candidate and quality evidence."""

    summary: BridgeSummary
    candidates: Mapping[str, tuple[SummaryCandidate, ...]]
    sources: Mapping[str, tuple[SourceAnchor, ...]]
    quality_flags: tuple[dict[str, object], ...]
    recommendation_count: int | None = None

    @property
    def bridge_id_candidates(self) -> tuple[SummaryCandidate, ...]:
        return self.candidates.get("bridge_id", ())

    @property
    def report_date_candidates(self) -> tuple[SummaryCandidate, ...]:
        return self.candidates.get("report_date", ())

    @property
    def field_candidates(self) -> Mapping[str, tuple[SummaryCandidate, ...]]:
        """Backward-friendly name for the complete candidate mapping."""

        return self.candidates

    @property
    def conclusion_entries(self) -> tuple[SummaryCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates.get("overall_conclusion", ())
            if candidate.source_kind == "conclusion"
        )

    @property
    def risk_entries(self) -> tuple[SummaryCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates.get("risk_points", ())
            if candidate.source_kind in {"conclusion", "safety_assessment"}
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": asdict(self.summary),
            "candidates": {
                field: [candidate.to_dict() for candidate in values]
                for field, values in self.candidates.items()
            },
            "sources": {
                field: [source.to_dict() for source in values]
                for field, values in self.sources.items()
            },
            "quality_flags": [dict(flag) for flag in self.quality_flags],
            "recommendation_count": self.recommendation_count,
            "bridge_id_candidates": [candidate.to_dict() for candidate in self.bridge_id_candidates],
            "report_date_candidates": [candidate.to_dict() for candidate in self.report_date_candidates],
        }

    def __getitem__(self, key: str) -> object:
        if key == "summary":
            return self.summary
        if key == "recommendation_count":
            return self.recommendation_count
        if key == "quality_flags":
            return self.quality_flags
        if key == "candidates":
            return self.candidates
        if key == "sources":
            return self.sources
        if key == "bridge_id_candidates":
            return self.bridge_id_candidates
        if key == "report_date_candidates":
            return self.report_date_candidates
        if key in _SUMMARY_FIELDS:
            return getattr(self.summary, key)
        raise KeyError(key)

    def get(self, key: str, default: object = None) -> object:
        try:
            return self[key]
        except KeyError:
            return default


class _CandidateCollector:
    def __init__(self) -> None:
        self.values: dict[str, list[SummaryCandidate]] = {
            field: [] for field in _SUMMARY_FIELDS
        }
        self.values["recommendation_count"] = []

    def add(
        self,
        field: str,
        value: str,
        source_kind: str,
        source: SourceAnchor | None,
        *,
        label: str = "",
        date_kind: str | None = None,
    ) -> None:
        if field not in self.values:
            return
        candidate = SummaryCandidate(
            field=field,
            value=_normalise_field_value(field, value),
            source_kind=source_kind,
            source=source,
            priority=_SOURCE_PRIORITY.get(source_kind, 0),
            label=_clean(label),
            date_kind=date_kind,
        )
        identity = (
            candidate.field,
            candidate.value,
            candidate.source_kind,
            _source_sort_key(candidate.source),
        )
        if any(
            (
                item.field,
                item.value,
                item.source_kind,
                _source_sort_key(item.source),
            )
            == identity
            for item in self.values[field]
        ):
            return
        self.values[field].append(candidate)


def extract_summary(
    document: DocumentModel,
    routes: Iterable[object] | None = None,
) -> SummaryExtraction:
    """Extract a deterministic bridge summary from a parsed Word document.

    ``routes`` may be supplied by the shared section router.  When omitted, the
    router is run locally.  All values are text from the document; scores and
    grades are never derived from one another.
    """

    if not isinstance(document, DocumentModel):
        raise TypeError("extract_summary expects a parsed DocumentModel")

    selected_routes = tuple(route_sections(document) if routes is None else routes)
    route_categories = _route_categories(selected_routes)
    collector = _CandidateCollector()
    first_heading = _first_heading_index(document, selected_routes)

    blocks = tuple(document.blocks)
    for block in blocks:
        categories = route_categories.get(block.block_index, set())
        if isinstance(block, TableBlock):
            source_kind = _table_source_kind(block, categories, blocks)
            _extract_table(block, source_kind, collector)
        elif isinstance(block, ParagraphBlock):
            source_kind = _paragraph_source_kind(block, categories, first_heading)
            _extract_paragraph(block, source_kind, collector)

    _extract_route_text(selected_routes, collector)
    _extract_cover_dates(blocks, first_heading, collector)
    recommendation_count = _select_recommendation_count(collector)
    if recommendation_count is None:
        recommendation_count = _recommendation_count_from_summary(collector)

    summary = BridgeSummary(
        **{
            field: _selected_or_missing(field, collector.values[field])
            for field in _SUMMARY_FIELDS
        }
    )
    quality_flags = _quality_flags(collector.values, summary, recommendation_count)
    candidates = {
        field: tuple(
            sorted(
                values,
                key=lambda candidate: (
                    -candidate.priority,
                    _source_sort_key(candidate.source),
                    candidate.source_kind,
                    candidate.value,
                ),
            )
        )
        for field, values in collector.values.items()
    }
    sources = {
        field: tuple(
            candidate.source
            for candidate in values
            if candidate.source is not None
        )
        for field, values in candidates.items()
    }
    return SummaryExtraction(
        summary=summary,
        candidates=candidates,
        sources=sources,
        quality_flags=tuple(quality_flags),
        recommendation_count=recommendation_count,
    )


def _route_categories(routes: Sequence[object]) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    for route in routes:
        category = getattr(route, "category", "")
        category = getattr(category, "value", category)
        category_name = str(category)
        for block in getattr(route, "blocks", ()):
            result.setdefault(block.block_index, set()).add(category_name)
    return result


def _first_heading_index(
    document: DocumentModel,
    routes: Sequence[object],
) -> int:
    heading_indices = [
        block.block_index
        for block in document.blocks
        if isinstance(block, ParagraphBlock) and block.heading_level is not None
    ]
    heading_indices.extend(
        route.source.block_index
        for route in routes
        if getattr(route, "source", None) is not None
    )
    return min(heading_indices, default=10**9)


def _paragraph_source_kind(
    block: ParagraphBlock,
    categories: set[str],
    first_heading: int,
) -> str:
    if _ROUTE_CONCLUSION in categories:
        return "conclusion"
    if _ROUTE_SAFETY in categories:
        return "safety_assessment"
    if _ROUTE_SCORE in categories:
        return "section_score"
    if block.block_index < first_heading:
        return "cover"
    return "paragraph"


def _table_source_kind(
    table: TableBlock,
    categories: set[str],
    blocks: Sequence[object],
) -> str:
    compact = _compact(table.raw_text)
    previous_block = next(
        (block for block in reversed(blocks) if block.block_index < table.block_index),
        None,
    )
    previous = (
        _compact(previous_block.raw_text)
        if isinstance(previous_block, ParagraphBlock)
        else ""
    )
    if _is_overall_assessment_table(compact, previous):
        return "overall_assessment_table"
    if _is_summary_table(table):
        return "summary_page"
    if _ROUTE_SCORE in categories or _looks_like_score_table(compact):
        return "section_score_table"
    if _ROUTE_CONCLUSION in categories:
        return "conclusion"
    if _ROUTE_SAFETY in categories:
        return "safety_assessment"
    return "paragraph"


def _is_overall_assessment_table(compact: str, previous: str = "") -> bool:
    return any(marker in compact or marker in previous for marker in _OVERALL_ASSESSMENT_MARKERS)


def _is_summary_table(table: TableBlock) -> bool:
    keys = {
        _normalised_key(cell.raw_text)
        for row in table.rows[:8]
        for cell in row.cells
    }
    metadata = {
        _normalised_key(alias)
        for field in ("bridge_name", "bridge_id", "report_date")
        for alias in _ALIASES[field]
    }
    return len(keys & metadata) >= 1 and (
        len(keys & metadata) >= 2
        or any(marker in _compact(table.raw_text) for marker in ("字段内容", "项目值"))
    )


def _looks_like_score_table(compact: str) -> bool:
    if "bci" in compact.casefold() and "技术状况" in compact:
        return True
    return sum(marker in compact for marker in _SCORE_MARKERS) >= 2 and any(
        marker in compact for marker in ("总体", "上部", "下部", "桥面", "评分")
    )


def _extract_table(
    table: TableBlock,
    source_kind: str,
    collector: _CandidateCollector,
) -> None:
    rows = [[_clean(cell.raw_text) for cell in row.cells] for row in table.rows]
    if not rows:
        return

    for row_index, row in enumerate(table.rows):
        cells = list(row.cells)
        for index, cell in enumerate(cells):
            key_field = _field_for_key(cell.raw_text)
            if key_field is not None and index + 1 < len(cells):
                value_cell = cells[index + 1]
                candidate_kind = (
                    "project_name"
                    if key_field == "bridge_name" and _compact(cell.raw_text) == "项目名称"
                    else source_kind
                )
                _add_field(
                    collector,
                    key_field,
                    value_cell.raw_text,
                    candidate_kind,
                    value_cell.source or cell.source or table.source,
                    label=cell.raw_text,
                )
            if key_field is None:
                _extract_embedded_fields(
                    cell.raw_text,
                    source_kind,
                    cell.source or table.source,
                    collector,
                )

    _extract_header_columns(table, source_kind, collector)
    if source_kind in {"overall_assessment_table", "section_score_table"}:
        _extract_score_matrix(table, source_kind, collector)

    _extract_bci_scores(_clean(table.raw_text), table.source, collector)

    recommendation_header = _recommendation_header_index(rows)
    if recommendation_header is not None:
        data_rows = rows[recommendation_header + 1 :]
        count = sum(
            1
            for data_row in data_rows
            if any(_clean(value) for value in data_row)
            and not _looks_like_recommendation_header(data_row)
        )
        collector.add(
            "recommendation_count",
            str(count),
            "recommendations_table",
            table.source,
            label="建议明细",
        )


def _extract_header_columns(
    table: TableBlock,
    source_kind: str,
    collector: _CandidateCollector,
) -> None:
    rows = list(table.rows)
    for header_index, header in enumerate(rows[:8]):
        header_fields = {
            cell.column_index: _field_for_key(cell.raw_text)
            for cell in header.cells
            if _field_for_key(cell.raw_text) is not None
        }
        if len(header_fields) < 2:
            continue
        for row in rows[header_index + 1 :]:
            for cell in row.cells:
                field = header_fields.get(cell.column_index)
                if field is None:
                    continue
                _add_field(
                    collector,
                    field,
                    cell.raw_text,
                    source_kind,
                    cell.source or table.source,
                    label=header.cells[0].raw_text,
                )
        return


def _extract_score_matrix(
    table: TableBlock,
    source_kind: str,
    collector: _CandidateCollector,
) -> None:
    rows = list(table.rows)
    for header_index, header in enumerate(rows[:8]):
        score_columns: dict[int, str] = {}
        grade_columns: dict[int, str] = {}
        for cell in header.cells:
            kind = _score_column_kind(cell.raw_text)
            if kind is not None:
                if kind == "grade" or kind.endswith("_grade"):
                    grade_columns[cell.column_index] = kind
                elif kind == "score" or kind.endswith("_score"):
                    score_columns[cell.column_index] = kind
        if not score_columns and not grade_columns:
            continue
        for row in rows[header_index + 1 :]:
            category_cell = next(iter(row.cells), None)
            if category_cell is None:
                continue
            base = _field_base_for_category(category_cell.raw_text)
            for cell in row.cells:
                if cell.column_index in score_columns:
                    if not _clean(cell.raw_text):
                        continue
                    column_kind = score_columns[cell.column_index]
                    field = (
                        f"{base}_score"
                        if column_kind == "score" and base is not None
                        else column_kind
                    )
                    if field == "score":
                        continue
                    _add_field(
                        collector,
                        field,
                        cell.raw_text,
                        source_kind,
                        cell.source or table.source,
                        label=category_cell.raw_text,
                    )
                if cell.column_index in grade_columns:
                    if not _clean(cell.raw_text):
                        continue
                    column_kind = grade_columns[cell.column_index]
                    field = (
                        f"{base}_grade"
                        if column_kind == "grade" and base is not None
                        else column_kind
                    )
                    if field == "grade":
                        continue
                    _add_field(
                        collector,
                        field,
                        cell.raw_text,
                        source_kind,
                        cell.source or table.source,
                        label=category_cell.raw_text,
                    )
        return


def _score_column_kind(value: str) -> str | None:
    compact = _compact(value)
    folded = compact.casefold()
    if folded == "bcim":
        return "deck_score"
    if folded == "bcik":
        return "superstructure_score"
    if folded == "bcix":
        return "substructure_score"
    if folded == "bci":
        return "overall_score"
    if "桥梁整体" in compact and ("等级" in compact or "级别" in compact):
        return "overall_grade"
    if "等级" in compact or "级别" in compact:
        return "grade"
    if (
        "评分" in compact
        or "分数" in compact
        or "得分" in compact
        or "指数" in compact
    ):
        return "score"
    if compact == "技术状况":
        return "grade"
    return None


def _field_base_for_category(value: str) -> str | None:
    compact = _compact(value)
    if "上一次" in compact or "上次" in compact or "上年度" in compact:
        return "previous_overall"
    if "上部" in compact:
        return "superstructure"
    if "下部" in compact:
        return "substructure"
    if "桥面" in compact:
        return "deck"
    if "总体" in compact or "整体" in compact or compact in {"总评", "全桥"}:
        return "overall"
    return None


def _field_for_key(value: str) -> str | None:
    return _ALIAS_TO_FIELD.get(_normalised_key(value))


def _extract_paragraph(
    block: ParagraphBlock,
    source_kind: str,
    collector: _CandidateCollector,
) -> None:
    if not _clean(block.raw_text):
        return
    _extract_embedded_fields(block.raw_text, source_kind, block.source, collector)
    _extract_score_phrases(block.raw_text, source_kind, block.source, collector)


def _extract_embedded_fields(
    raw_text: str,
    source_kind: str,
    source: SourceAnchor | None,
    collector: _CandidateCollector,
) -> None:
    text = _clean(raw_text)
    if not text:
        return
    matches: list[tuple[int, int, str, str]] = []
    for alias, field in _SORTED_ALIASES:
        pattern = re.compile(
            re.escape(alias)
            + r"\s*(?:(?:[:：=])\s*|(?:为|是)\s*|(?=[\d.无暂无不适用A-Ea-e优良中差]|$))"
        )
        for match in pattern.finditer(text):
            if any(start <= match.start() < end or match.start() <= start < match.end() for start, end, _, _ in matches):
                continue
            matches.append((match.start(), match.end(), field, alias))
    matches.sort(key=lambda item: item[0])
    for index, (start, end, field, alias) in enumerate(matches):
        value_end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        value = text[end:value_end].strip(" \t:：=，,；;。．")
        if not value and field not in {"bridge_id", "previous_overall_score", "previous_overall_grade"}:
            continue
        date_kind = _date_kind_for_alias(alias) if field == "report_date" else None
        candidate_kind = _date_source_kind(source_kind, date_kind)
        _add_field(
            collector,
            field,
            value,
            candidate_kind,
            source,
            label=alias,
            date_kind=date_kind,
        )

    _extract_bci_scores(text, source, collector)


def _extract_bci_scores(
    text: str,
    source: SourceAnchor | None,
    collector: _CandidateCollector,
) -> None:
    """Extract BCI/BCIm/BCIs/BCIx score phrases and their attached grades.

    These phrases appear in technical-condition-index reports (often inside
    large cover tables) where the component scores are written as
    ``桥面系BCIm=89.00，评定为B级`` style sentences.  The text phrase is the
    authoritative statement; a dedicated high-priority source kind lets it win
    over the score matrix when the matrix carries a misprint.
    """

    for match in _BCI_SCORE_RE.finditer(text):
        suffix = (match.group(1) or "").lower()
        base = _BCI_COMPONENT.get(suffix, "overall")
        _add_field(
            collector,
            f"{base}_score",
            match.group(2),
            "bci",
            source,
            label="BCI指数",
        )
        window = text[match.end(): match.end() + 200]
        if base == "overall":
            overall_match = _OVERALL_GRADE_RE.search(window)
            if overall_match:
                _add_field(
                    collector,
                    "overall_grade",
                    overall_match.group(1),
                    "bci",
                    source,
                    label="整体技术状况等级",
                )
            else:
                fallback = _GRADE_AFTER_RE.search(window)
                if fallback:
                    _add_field(
                        collector,
                        "overall_grade",
                        fallback.group(1),
                        "bci",
                        source,
                        label="整体技术状况等级",
                    )
        else:
            grade_match = _GRADE_AFTER_RE.search(window)
            if grade_match:
                _add_field(
                    collector,
                    f"{base}_grade",
                    grade_match.group(1),
                    "bci",
                    source,
                    label="BCI等级",
                )

    underpass = _UNDERPASS_GRADE_RE.search(text)
    if underpass:
        _add_field(
            collector,
            "overall_grade",
            underpass.group(1),
            "bci",
            source,
            label="技术状况总评",
        )


def _extract_score_phrases(
    raw_text: str,
    source_kind: str,
    source: SourceAnchor | None,
    collector: _CandidateCollector,
) -> None:
    text = _clean(raw_text)
    patterns = (
        ("overall", "总体(?:技术状况)?"),
        ("superstructure", "上部结构"),
        ("substructure", "下部结构"),
        ("deck", "桥面系"),
    )
    for base, label_pattern in patterns:
        score_match = re.search(
            rf"({label_pattern})\s*(?:评分|分数|得分)\s*(?:为|是|[:：=])?\s*([^，,；;。\s]*(?:\s*分)?)",
            text,
        )
        if score_match:
            _add_field(
                collector,
                f"{base}_score",
                score_match.group(2),
                source_kind,
                source,
                label=score_match.group(1),
            )
            grade_match = re.search(
                rf"{re.escape(score_match.group(2).strip())}\s*分?\s*[（(]\s*({_GRADE_RE.pattern})\s*[）)]",
                text,
            )
            if grade_match:
                _add_field(
                    collector,
                    f"{base}_grade",
                    grade_match.group(1),
                    source_kind,
                    source,
                    label=score_match.group(1),
                )
        grade_match = re.search(
            rf"{label_pattern}\s*(?:技术状况)?\s*(?:等级|级别)\s*(?:为|是|[:：=])?\s*({_GRADE_RE.pattern})",
            text,
        )
        if grade_match:
            _add_field(
                collector,
                f"{base}_grade",
                grade_match.group(1),
                source_kind,
                source,
                label=base,
            )
    previous_patterns = (
        ("previous_overall_score", r"上一次(?:总体)?(?:技术状况)?(?:评分|分数|得分)"),
        ("previous_overall_grade", r"上一次(?:总体)?(?:技术状况)?(?:等级|级别)"),
    )
    for field, label_pattern in previous_patterns:
        match = re.search(
            rf"{label_pattern}\s*(?:为|是|[:：=])?\s*([^，,；;。\s]*(?:\s*分)?)",
            text,
        )
        if match:
            _add_field(collector, field, match.group(1), source_kind, source, label=field)


def _extract_route_text(
    routes: Sequence[object],
    collector: _CandidateCollector,
) -> None:
    for route in routes:
        category = getattr(route, "category", "")
        category = getattr(category, "value", category)
        category = str(category)
        blocks = tuple(getattr(route, "blocks", ()))
        if category == _ROUTE_CONCLUSION:
            body = [
                block
                for block in blocks
                if isinstance(block, ParagraphBlock)
                and block.block_index != getattr(route, "source", SourceAnchor("", -1, "")).block_index
                and _clean(block.raw_text)
            ]
            if body and not any(collector.values["overall_conclusion"]):
                collector.add(
                    "overall_conclusion",
                    "\n".join(_clean(block.raw_text) for block in body),
                    "conclusion",
                    body[0].source,
                    label="检测结论",
                )
            risk_body = [
                block
                for block in body
                if any(marker in _compact(block.raw_text) for marker in ("风险", "隐患", "安全"))
            ]
            if risk_body and not any(collector.values["risk_points"]):
                collector.add(
                    "risk_points",
                    "\n".join(_clean(block.raw_text) for block in risk_body),
                    "conclusion",
                    risk_body[0].source,
                    label="风险点",
                )
        elif category == _ROUTE_SAFETY:
            body = [
                block
                for block in blocks
                if isinstance(block, ParagraphBlock)
                and _clean(block.raw_text)
                and block.block_index != getattr(route, "source", SourceAnchor("", -1, "")).block_index
            ]
            risk_body = [
                block
                for block in body
                if any(marker in _compact(block.raw_text) for marker in ("风险", "隐患", "安全", "影响"))
            ]
            if risk_body and not any(collector.values["risk_points"]):
                collector.add(
                    "risk_points",
                    "\n".join(_clean(block.raw_text) for block in risk_body),
                    "safety_assessment",
                    risk_body[0].source,
                    label="安全评估",
                )


def _cn_units(value: str) -> int:
    digits = _CN_DIGIT
    if "十" in value:
        tens_part, _, ones_part = value.partition("十")
        tens = digits.get(tens_part, 1) if tens_part else 1
        ones = digits.get(ones_part, 0) if ones_part else 0
        return tens * 10 + ones
    return digits.get(value, 0)


def _extract_cn_date(text: str) -> str | None:
    """Convert a Chinese-numeral cover date like ``二○一三年二月`` to ``2013年2月``."""

    match = _CN_DATE_RE.search(text)
    if match is None:
        return None
    year = "".join(str(_CN_DIGIT.get(character, 0)) for character in match.group(1))
    month = _cn_units(match.group(2))
    if not year or month < 1 or month > 12:
        return None
    result = f"{year}年{month}月"
    if match.group(3):
        day = _cn_units(match.group(3))
        if 1 <= day <= 31:
            result += f"{day}日"
    return result


def _extract_cover_dates(
    blocks: Sequence[object],
    first_heading: int,
    collector: _CandidateCollector,
) -> None:
    for block in blocks:
        if not isinstance(block, ParagraphBlock) or block.block_index >= first_heading:
            continue
        text = _clean(block.raw_text)
        cn_date = _extract_cn_date(text)
        if cn_date is not None:
            collector.add(
                "report_date",
                cn_date,
                "cover",
                block.source,
                label="封面中文日期",
                date_kind="cover",
            )
        if any(_compact(alias) in _compact(text) for alias in _ALIASES["report_date"]):
            continue
        for match in _DATE_RE.finditer(text):
            collector.add(
                "report_date",
                match.group(0),
                "cover",
                block.source,
                label="封面日期",
                date_kind="cover",
            )


def _add_field(
    collector: _CandidateCollector,
    field: str,
    value: str,
    source_kind: str,
    source: SourceAnchor | None,
    *,
    label: str = "",
    date_kind: str | None = None,
) -> None:
    if field == "report_date":
        date_kind = date_kind or _date_kind_for_alias(label)
        value = _date_value(value)
        if not value and _clean(value) != "":
            return
    collector.add(
        field,
        value,
        source_kind,
        source,
        label=label,
        date_kind=date_kind,
    )


def _date_kind_for_alias(alias: str) -> str:
    compact = _compact(alias)
    if "检测" in compact:
        return "detection"
    if "签发" in compact or "签字" in compact or "出具" in compact:
        return "sign"
    return "cover"


def _date_source_kind(source_kind: str, date_kind: str | None) -> str:
    if date_kind in {"cover", "sign", "detection"} and source_kind in {
        "cover",
        "paragraph",
        "conclusion",
        "safety_assessment",
    }:
        return date_kind
    return source_kind


def _date_value(value: str) -> str:
    cleaned = _clean(value).strip("：:=，,；;。．")
    match = _DATE_RE.search(cleaned)
    return match.group(0) if match else cleaned


def _normalise_field_value(field: str, value: str) -> str:
    cleaned = _clean(value).strip("：:=，,；;。．")
    if field == "bridge_name":
        cleaned = re.split(
            r"(?:所在路名|在路名|路名|桥梁编号|桥梁ID|等级)\s*[:：=]",
            cleaned,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        cleaned = re.sub(r"(?:检测评估|评估报告|外观检查)\s*$", "", cleaned)
        return cleaned.strip("：:=，,；;。． ")
    if field.endswith("_score"):
        if not cleaned or cleaned in {"无", "暂无", "不适用"}:
            return cleaned
        match = _SCORE_RE.search(cleaned)
        return match.group(0) if match else cleaned
    if field.endswith("_grade"):
        if not cleaned or cleaned in {"无", "暂无", "不适用"}:
            return cleaned
        match = _GRADE_RE.search(cleaned)
        if match is None:
            return cleaned
        grade = match.group(0).replace(" ", "")
        return f"{grade}级" if re.fullmatch(r"[A-Ea-e]", grade) else grade
    if field == "report_date":
        return _date_value(cleaned)
    if field == "recommendation_count":
        match = re.search(r"\d+", cleaned)
        return match.group(0) if match else cleaned
    return cleaned


def _select_value(field: str, values: Sequence[SummaryCandidate]) -> str:
    if not values:
        return ""
    ordered = sorted(
        values,
        key=lambda candidate: (
            -(_DATE_PRIORITY.get(candidate.date_kind or "", 0) if field == "report_date" else _selection_priority(field, candidate)),
            -candidate.priority,
            _source_sort_key(candidate.source),
            candidate.source_kind,
            candidate.value,
        ),
    )
    nonempty = [candidate for candidate in ordered if candidate.value.strip()]
    return (nonempty[0] if nonempty else ordered[0]).value


def _selected_or_missing(field: str, values: Sequence[SummaryCandidate]) -> str:
    value = _select_value(field, values)
    if not value.strip() and field in _SCORE_FIELDS:
        return "无"
    return value


def _selection_priority(field: str, candidate: SummaryCandidate) -> int:
    if field not in _SCORE_FIELDS:
        return candidate.priority
    return {
        "bci": 500,
        "overall_assessment_table": 400,
        "section_score_table": 300,
        "section_score": 280,
        "summary_page": 200,
        "conclusion": 100,
    }.get(candidate.source_kind, 80)


def _select_recommendation_count(
    collector: _CandidateCollector,
) -> int | None:
    values = collector.values["recommendation_count"]
    if not values:
        return None
    ordered = sorted(
        values,
        key=lambda candidate: (
            -candidate.priority,
            _source_sort_key(candidate.source),
            candidate.value,
        ),
    )
    top_priority = ordered[0].priority
    top = [candidate for candidate in ordered if candidate.priority == top_priority]
    if all(candidate.source_kind == "recommendations_table" for candidate in top):
        try:
            return sum(int(candidate.value) for candidate in top)
        except ValueError:
            return None
    try:
        return int(ordered[0].value)
    except ValueError:
        return None


def _recommendation_count_from_summary(
    collector: _CandidateCollector,
) -> int | None:
    values = collector.values["recommendations_summary"]
    if not values:
        return None
    for candidate in sorted(values, key=lambda item: _source_sort_key(item.source)):
        numbers = [int(value) for value in _RECOMMENDATION_COUNT_RE.findall(candidate.value)]
        if numbers:
            collector.add(
                "recommendation_count",
                str(sum(numbers)),
                candidate.source_kind,
                candidate.source,
                label="建议汇总",
            )
            return sum(numbers)
        if candidate.value in {"无", "暂无", "无建议"}:
            collector.add(
                "recommendation_count",
                "0",
                candidate.source_kind,
                candidate.source,
                label="建议汇总",
            )
            return 0
    return None


def _recommendation_header_index(rows: Sequence[Sequence[str]]) -> int | None:
    for index, row in enumerate(rows[:8]):
        if _looks_like_recommendation_header(row):
            return index
    return None


def _looks_like_recommendation_header(row: Sequence[str]) -> bool:
    compact = "".join(_compact(value) for value in row)
    return any(marker in compact for marker in _RECOMMENDATION_MARKERS) and (
        "建议" in compact or "维修" in compact or "养护" in compact
    )


def _quality_flags(
    candidates: Mapping[str, Sequence[SummaryCandidate]],
    summary: BridgeSummary,
    recommendation_count: int | None,
) -> list[dict[str, object]]:
    flags: list[dict[str, object]] = []
    required = (
        "bridge_name",
        "report_date",
        "overall_score",
        "overall_grade",
        "superstructure_score",
        "superstructure_grade",
        "substructure_score",
        "substructure_grade",
        "deck_score",
        "deck_grade",
    )
    for field in required:
        values = candidates.get(field, ())
        if not values or not any(candidate.value.strip() for candidate in values):
            flags.append(
                {
                    "code": MISSING_VALUE,
                    "message": f"No non-empty candidate was found for {field}.",
                    "details": {"field": field},
                }
            )

    if not candidates.get("bridge_id", ()):
        flags.append(
            {
                "code": MISSING_VALUE,
                "message": "No bridge-id candidate was found; an explicit empty value is valid.",
                "details": {"field": "bridge_id"},
            }
        )
    if recommendation_count is None:
        flags.append(
            {
                "code": MISSING_VALUE,
                "message": "No recommendation count candidate was found.",
                "details": {"field": "recommendation_count"},
            }
        )

    for field in _SUMMARY_FIELDS:
        values = candidates.get(field, ())
        distinct = sorted({candidate.value for candidate in values if candidate.value.strip()})
        if len(distinct) <= 1:
            continue
        flags.append(
            {
                "code": CONFLICTING_CANDIDATES,
                "message": f"Conflicting candidates were preserved for {field}.",
                "details": {
                    "field": field,
                    "selected": getattr(summary, field, recommendation_count),
                    "values": distinct,
                    "candidates": [candidate.to_dict() for candidate in values],
                },
            }
        )
    return flags
