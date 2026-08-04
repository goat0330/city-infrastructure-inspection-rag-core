"""Extract one :class:`DefectObservation` per concrete table row.

The extractor consumes the existing ``DocumentModel`` produced by
``parse_docx``.  It first uses ``defect_table`` routes and then falls back to
defect-shaped table headers.  The result wrapper keeps quality flags beside
the contract-compatible observations; it also behaves as a read-only
sequence for callers that only need the records.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import re
import unicodedata

from ...contracts import (
    DefectObservation,
    DocumentModel,
    ParagraphBlock,
    SourceAnchor,
    TableBlock,
    TableCell,
    TableRow,
)
from ...routing import SectionCategory, SectionRoute, route_sections


QualityFlag = dict[str, object]

_FIELD_ORDER = (
    "index",
    "location",
    "defect_type",
    "description",
    "is_new",
    "previous_status",
    "development",
)
_REQUIRED_FIELDS = frozenset(("index", "location", "defect_type", "description"))
_INHERITED_FIELDS = frozenset(("index", "location", "defect_type"))
_DEFAULT_FIELDS = {"is_new": "否", "previous_status": "无", "development": "无"}

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "index": ("序号", "编号", "病害编号", "缺陷编号", "病害序号", "缺陷序号"),
    "location": ("病害部位", "病害位置", "缺陷位置", "所在部位", "部位", "位置"),
    "defect_type": (
        "病害类型",
        "病害种类",
        "缺陷类型",
        "病害名称",
        "缺陷名称",
        "类型",
    ),
    "description": (
        "病害描述",
        "缺陷描述",
        "具体描述",
        "具体位置",
        "病害情况",
        "病害特征",
        "病害内容",
        "描述",
    ),
    "is_new": ("是否新增", "新增情况", "是否新病害", "新旧"),
    "previous_status": ("上一次定检状态", "历史状态", "上次状态", "既有病害状态", "既往状态"),
    "development": ("发展程度", "发展趋势", "病害发展", "变化趋势", "发展"),
}
_EXPLICIT_TABLE_MARKERS = (
    "病害明细",
    "病害列表",
    "病害表",
    "缺陷明细",
    "缺陷表",
)
_EXACT_ONLY_ALIASES = frozenset(
    ("序号", "编号", "部位", "位置", "类型", "描述", "具体位置", "发展", "新旧")
)
_PHOTO_REFERENCE_RE = re.compile(
    r"[，,;；。]?\s*(?:见\s*)?(?:照片|照|附图|图)\s*[\w./+#-]+\s*[。；;]?$"
)
_LANE_PREFIX_RE = re.compile(r"^(左幅|右幅|左侧|右侧)")
_SECTION_MARKERS = ("上部结构", "下部结构", "桥面系")


@dataclass(frozen=True)
class DefectExtractionResult(Sequence[DefectObservation]):
    """Contract-compatible observations plus non-silent extraction flags."""

    records: tuple[DefectObservation, ...] = ()
    quality_flags: tuple[QualityFlag, ...] = ()

    @property
    def observations(self) -> tuple[DefectObservation, ...]:
        """Alias useful to callers that name the output observations."""

        return self.records

    @property
    def defects(self) -> tuple[DefectObservation, ...]:
        """Alias matching the prediction contract field name."""

        return self.records

    def __iter__(self) -> Iterator[DefectObservation]:
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int | slice) -> DefectObservation | tuple[DefectObservation, ...]:
        return self.records[index]


@dataclass(frozen=True)
class _HeaderRow:
    row_index: int
    mapping: dict[str, int]
    candidates: dict[str, tuple[int, ...]]


@dataclass(frozen=True)
class _HeaderAnalysis:
    rows: tuple[_HeaderRow, ...]
    seen_fields: frozenset[str]
    ambiguous: dict[str, tuple[int, ...]]


def extract_defects(
    document: DocumentModel,
    routes: Sequence[SectionRoute] | None = None,
) -> DefectExtractionResult:
    """Extract individual defect rows from a parsed Word document.

    ``routes`` may be the output of :func:`route_sections`; when omitted it is
    computed.  A routed defect section is preferred.  If no such route has a
    table, tables with defect-shaped headers are consumed in document order
    and ``fallback_defect_table_routing`` is emitted.  Blank index, location,
    and type cells inherit the most recent non-blank value, including across
    repeated headers; descriptions are never merged or rewritten.
    """

    active_routes = tuple(route_sections(document) if routes is None else routes)
    tables, used_fallback = _locate_tables(document, active_routes)
    flags: list[QualityFlag] = []
    if used_fallback and tables:
        flags.append(
            _flag(
                "fallback_defect_table_routing",
                "No routed defect table was available; structural/header evidence selected the table.",
                table_indices=[table.table_index for table in tables],
            )
        )
    if not tables:
        flags.append(
            _flag(
                "missing_defect_table",
                "No routed or structurally defect-shaped table was found.",
            )
        )
        return DefectExtractionResult((), tuple(flags))

    records: list[DefectObservation] = []
    for table in tables:
        table_records, table_flags = _extract_table(
            table,
            section_label=_section_label_for_table(document, table),
        )
        records.extend(table_records)
        flags.extend(table_flags)
    if not records:
        flags.append(
            _flag(
                "no_defect_rows",
                "A defect table was located but no concrete non-header rows were extracted.",
            )
        )
    return DefectExtractionResult(tuple(records), tuple(flags))


def _locate_tables(
    document: DocumentModel,
    routes: Sequence[SectionRoute],
) -> tuple[tuple[TableBlock, ...], bool]:
    defect_routes = [route for route in routes if _is_defect_route(route)]
    routed_tables = _unique_tables(
        block
        for route in defect_routes
        for block in route.blocks
        if isinstance(block, TableBlock)
    )
    if routed_tables:
        marked = tuple(table for table in routed_tables if _looks_like_defect_table(table))
        return (marked or routed_tables), False

    structural = tuple(
        table
        for table in document.blocks
        if isinstance(table, TableBlock) and _looks_like_defect_table(table)
    )
    return structural, True


def _is_defect_route(route: SectionRoute) -> bool:
    category = getattr(route, "category", None)
    return category is SectionCategory.DEFECT_TABLE or str(category) == SectionCategory.DEFECT_TABLE.value


def _unique_tables(tables: Sequence[TableBlock] | Iterator[TableBlock]) -> tuple[TableBlock, ...]:
    result: list[TableBlock] = []
    seen: set[tuple[int, int]] = set()
    for table in tables:
        key = (table.block_index, table.table_index)
        if key in seen:
            continue
        seen.add(key)
        result.append(table)
    return tuple(result)


def _looks_like_defect_table(table: TableBlock) -> bool:
    analysis = _analyse_headers(table)
    # ``位置`` and ``类型`` also occur in structural calculation tables.
    # Require a description field plus an identity field so those tables and
    # recommendation tables are not re-read as defects on router fallback.
    has_description = "description" in analysis.seen_fields
    has_location = "location" in analysis.seen_fields
    header_text = _normalise_header(
        "".join(
            cell.raw_text
            for row in table.rows[:8]
            for cell in row.cells
        )
    )
    has_explicit_domain_header = any(
        marker in header_text for marker in ("病害", "缺陷")
    )
    has_coherent_header = any(
        "description" in row.mapping
        and bool(set(row.mapping) & {"location", "defect_type"})
        for row in analysis.rows
    )
    return (
        has_description
        and has_coherent_header
        and (has_location or has_explicit_domain_header)
    ) or any(
        marker in header_text for marker in _EXPLICIT_TABLE_MARKERS
    )


def _section_label_for_table(document: DocumentModel, table: TableBlock) -> str:
    """Return the nearest preceding section name (桥面系/上部结构/下部结构).

    The section heading immediately before a defect table is used to expand
    bare lane locations (``左幅``/``右幅``) that only name the lane but not
    the member, e.g. ``左幅`` + ``上部结构`` -> ``左幅上部结构``.
    """

    table_position: int | None = None
    for index, block in enumerate(document.blocks):
        if (
            isinstance(block, TableBlock)
            and block.block_index == table.block_index
            and block.table_index == table.table_index
        ):
            table_position = index
            break
    if table_position is None:
        return ""
    for block in reversed(document.blocks[:table_position]):
        if not isinstance(block, ParagraphBlock):
            continue
        text = _normalise_header(block.raw_text)
        for marker in _SECTION_MARKERS:
            if marker in text:
                return marker
    return ""


def _extract_table(
    table: TableBlock,
    section_label: str = "",
) -> tuple[list[DefectObservation], list[QualityFlag]]:
    analysis = _analyse_headers(table)
    flags: list[QualityFlag] = []
    details = {"table_index": table.table_index, "block_index": table.block_index}

    if analysis.ambiguous:
        flags.append(
            _flag(
                "ambiguous_defect_columns",
                "A defect header maps more than one column to the same field; the leftmost column was used.",
                **details,
                columns={
                    field: list(analysis.ambiguous[field])
                    for field in _ordered_fields(analysis.ambiguous)
                },
            )
        )

    positional = not analysis.rows
    if positional:
        flags.append(
            _flag(
                "missing_defect_header",
                "No recognized defect header row was found; canonical column order was used for this routed table.",
                **details,
            )
        )
        column_map = _positional_map(table)
        flags.append(
            _flag(
                "fallback_positional_columns",
                "Defect values were read by canonical position because header evidence was missing.",
                **details,
                columns={field: column_map[field] for field in _ordered_fields(column_map)},
            )
        )
        missing = [field for field in _REQUIRED_FIELDS if field not in column_map]
        if missing:
            flags.append(
                _flag(
                    "missing_defect_columns",
                    "Required defect columns are absent from the positional table shape.",
                    **details,
                    columns=_ordered_fields(set(missing)),
                )
            )
    else:
        column_map = {}
        missing = _REQUIRED_FIELDS - analysis.seen_fields
        if missing:
            flags.append(
                _flag(
                    "missing_defect_columns",
                    "Required defect columns were not identified in the table headers.",
                    **details,
                    columns=_ordered_fields(missing),
                )
            )

    header_by_row = {header.row_index: header for header in analysis.rows}
    first_header = min(header_by_row) if header_by_row else None
    inherited_values: dict[str, str] = {}
    inherited_anchors: dict[str, SourceAnchor] = {}
    defaulted_fields: set[str] = set()
    result: list[DefectObservation] = []

    for row in table.rows:
        header = header_by_row.get(row.row_index)
        if header is not None:
            if not positional:
                column_map.update(header.mapping)
            continue
        if first_header is not None and row.row_index < first_header:
            continue
        if not column_map:
            continue

        values: dict[str, str] = {}
        origins: dict[str, SourceAnchor] = {}
        has_raw_text = any(_display_text(cell.raw_text) for cell in row.cells)
        if not has_raw_text:
            continue

        for field in _FIELD_ORDER:
            column = column_map.get(field)
            cell = _cell_at(row, column) if column is not None else None
            value = _display_text(cell.raw_text) if cell is not None else ""
            if field == "description":
                value = _clean_description(value)
            if value:
                values[field] = value
                origins[field] = _cell_anchor(table, cell)
                if field in _INHERITED_FIELDS:
                    inherited_values[field] = value
                    inherited_anchors[field] = origins[field]
            elif field in _INHERITED_FIELDS and field in inherited_values:
                values[field] = inherited_values[field]
                origins[field] = inherited_anchors[field]
            elif field in _DEFAULT_FIELDS:
                values[field] = _DEFAULT_FIELDS[field]
                defaulted_fields.add(field)
            else:
                values[field] = ""

        if not any(values[field] for field in _REQUIRED_FIELDS):
            continue

        _expand_lane_and_section(values, section_label)

        anchors = _row_anchors(table, row)
        for field in _INHERITED_FIELDS:
            if field in origins and origins[field] not in anchors:
                anchors.append(origins[field])
        result.append(
            DefectObservation(
                index=values["index"],
                location=values["location"],
                defect_type=values["defect_type"],
                description=values["description"],
                is_new=values["is_new"],
                previous_status=values["previous_status"],
                development=values["development"],
                evidence=tuple(anchors),
            )
        )
    if defaulted_fields:
        flags.append(
            _flag(
                "defaulted_defect_fields",
                "Missing defect status fields use the current Gold template defaults.",
                fields=_ordered_fields(defaulted_fields),
            )
        )
    return result, flags


def _analyse_headers(table: TableBlock) -> _HeaderAnalysis:
    rows: list[_HeaderRow] = []
    seen_fields: set[str] = set()
    ambiguous: dict[str, tuple[int, ...]] = {}
    for row in table.rows:
        candidates = _header_candidates(row)
        if not candidates:
            continue
        mapping = {field: min(columns) for field, columns in candidates.items()}
        row_info = _HeaderRow(
            row_index=row.row_index,
            mapping=mapping,
            candidates={field: tuple(columns) for field, columns in candidates.items()},
        )
        rows.append(row_info)
        seen_fields.update(mapping)
        for field, columns in row_info.candidates.items():
            if len(columns) > 1:
                ambiguous[field] = tuple(columns)
    return _HeaderAnalysis(tuple(rows), frozenset(seen_fields), ambiguous)


def _header_candidates(row: TableRow) -> dict[str, list[int]]:
    candidates: dict[str, list[int]] = {}
    for cell in row.cells:
        text = _normalise_header(cell.raw_text)
        if not text:
            continue
        for field, aliases in _HEADER_ALIASES.items():
            if any(_alias_matches(text, alias) for alias in aliases):
                candidates.setdefault(field, []).append(cell.column_index)
    return candidates


def _alias_matches(text: str, alias: str) -> bool:
    value = _normalise_header(alias)
    if text == value:
        return True
    if value in _EXACT_ONLY_ALIASES:
        return False
    return len(value) >= 2 and value in text


def _positional_map(table: TableBlock) -> dict[str, int]:
    if not table.rows:
        return {}
    row = max(
        table.rows,
        key=lambda candidate: (len(_row_columns(candidate)), -candidate.row_index),
    )
    columns = _row_columns(row)
    return {
        field: column
        for field, column in zip(_FIELD_ORDER, columns)
    }


def _row_columns(row: TableRow) -> list[int]:
    columns: set[int] = set()
    for cell in row.cells:
        columns.update(
            range(cell.column_index, cell.column_index + max(1, cell.column_span))
        )
    return sorted(columns)


def _cell_at(row: TableRow, column: int | None) -> TableCell | None:
    if column is None:
        return None
    for cell in row.cells:
        start = cell.column_index
        end = start + max(1, cell.column_span)
        if start <= column < end:
            return cell
    return None


def _row_anchors(table: TableBlock, row: TableRow) -> list[SourceAnchor]:
    anchors: list[SourceAnchor] = []
    for cell in row.cells:
        anchor = _cell_anchor(table, cell)
        if anchor not in anchors:
            anchors.append(anchor)
    if not anchors:
        anchors.append(table.source)
    return anchors


def _cell_anchor(table: TableBlock, cell: TableCell | None) -> SourceAnchor:
    if cell is not None and cell.source is not None:
        return cell.source
    return table.source


def _normalise_header(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _display_text(value: str) -> str:
    # Preserve internal line breaks and punctuation in descriptions; only
    # surrounding layout whitespace is not part of a cell value.
    return (value or "").replace("\u00a0", " ").strip()


def _expand_lane_and_section(values: dict[str, str], section_label: str) -> None:
    """Expand a bare location into a fuller location using the description.

    A lane/side token (``左幅``/``右幅``/``左侧``/``右侧``) that appears at
    the start of the description but not in the location cell is prepended to
    the location (``车行道`` + ``右幅`` -> ``右幅车行道``, ``栏杆`` + ``右侧``
    -> ``右侧栏杆``).  A location that contains only a lane token is expanded
    with the table's section name (``左幅`` + ``上部结构`` -> ``左幅上部结构``).
    """

    location = values.get("location") or ""
    if not location:
        return
    lane_match = _LANE_PREFIX_RE.match(values.get("description") or "")
    if lane_match and not location.startswith(lane_match.group(1)):
        location = lane_match.group(1) + location
    if location in ("左幅", "右幅") and section_label and not location.endswith(section_label):
        location = location + section_label
    if location != (values.get("location") or ""):
        values["location"] = location


def _clean_description(value: str) -> str:
    """Remove a trailing photo/figure citation from a defect description."""

    return _PHOTO_REFERENCE_RE.sub("", value).strip()


def _ordered_fields(fields: Sequence[str] | set[str] | frozenset[str] | dict[str, object]) -> list[str]:
    field_set = set(fields)
    return [field for field in _FIELD_ORDER if field in field_set]


def _flag(code: str, message: str, **details: object) -> QualityFlag:
    flag: QualityFlag = {"code": code, "message": message}
    if details:
        flag["details"] = details
    return flag
