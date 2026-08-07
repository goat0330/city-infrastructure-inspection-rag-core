"""Extract one :class:`DefectObservation` per concrete table row.

The extractor consumes the existing ``DocumentModel`` produced by
``parse_docx``.  It first uses ``defect_table`` routes and then falls back to
defect-shaped table headers.  The result wrapper keeps quality flags beside
the contract-compatible observations; it also behaves as a read-only
sequence for callers that only need the records.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
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
_DEFAULT_FIELDS = {"is_new": "", "previous_status": "", "development": ""}

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
_LANE_PREFIX_RE = re.compile(r"^(左幅|右幅|左侧|右侧)")
_SECTION_MARKERS = ("上部结构", "下部结构", "桥面系")

_TEXT_DEFECT_WORDS = (
    "无泄水孔及排水设施",
    "无泄水孔",
    "无排水设施",
    "渗水泛碱",
    "裂缝修补",
    "露筋锈蚀",
    "混凝土破损",
    "裂缝",
    "破损",
    "损坏",
    "渗水",
    "漏水",
    "锈蚀",
    "露筋",
    "剥落",
    "脱空",
    "堵塞",
    "缺失",
    "变形",
    "沉降",
    "错台",
    "老化",
    "松动",
    "开裂",
    "腐蚀",
    "离析",
    "不密实",
    "磨损",
    "泛碱",
    "风化",
    "下挠",
    "冲刷",
    "积水",
    "淤积",
    "残缺",
    "坑槽",
    "麻面",
    "蜂窝",
    "胀模",
    "脱落",
    "网裂",
    "车辙",
    "未设置",
    "外倾",
    "掏空",
    "坑洼",
    "长草",
    # Formal defect tables use a broader vocabulary than narrative fallback
    # rules. Keep common surface, clearance, debris and joint conditions here
    # so one-field text candidates are not lost.
    "堆积",
    "推积",
    "覆盖",
    "凸起",
    "突起",
    "外鼓",
    "滑痕",
    "刮痕",
    "划痕",
    "烟熏",
    "断裂",
    "拆除",
    "被拆",
    "高差",
    "变窄",
    "错位",
    "纵裂",
    "横裂",
    "斜裂",
    "竖裂",
    "漏浆",
    "空洞",
    "缺角",
    "积土",
    "积淤",
    "锈胀",
    "跳车",
    "龟裂",
    "磨光",
    "露骨",
    "坑洞",
    "不饱满",
    "抵死",
    "抵拢",
    "杂物",
    "垃圾",
    "杂草丛生",
    "模板未拆",
)
_TEXT_LOCATION_WORDS = (
    "桥面铺装",
    "上部结构",
    "下部结构",
    "防撞护栏",
    "防撞栏杆",
    "防护栏",
    "防护网",
    "伸缩缝",
    "保护带",
    "止水带",
    "路缘石",
    "湿接缝",
    "横隔板",
    "支座垫板",
    "支座垫石",
    "支座",
    "栏杆",
    "护栏",
    "车行道",
    "人行道",
    "桥面系",
    "桥面",
    "铺装",
    "箱梁",
    "梁底",
    "梁体",
    "主梁",
    "腹板",
    "底板",
    "翼缘板",
    "翼板",
    "顶板",
    "板底",
    "盖梁",
    "桥台",
    "墩台",
    "桥墩",
    "墩身",
    "侧墙",
    "墙身",
    "翼墙",
    "前墙",
    "基础",
    "泄水孔",
    "排水",
    "沉降缝",
    "通道",
    "盖板",
    "构件",
    "钢丝网",
    "桩",
    "柱",
    "拱",
    "涂层",
    "塔身",
    "横梁",
    "斜拉索",
    "钢套管",
    "梯道",
    "限高牌",
    "其他",
    "梁体翼板",
)
_TEXT_POSITIVE_WORDS = (
    "存在",
    "出现",
    "局部",
    "多处",
    "有",
    "发现",
    "可见",
    "分布",
    "表现为",
    "发生",
    "产生",
    "无泄水孔及排水设施",
    "无泄水孔",
    "无排水设施",
    "未设置",
)
_TEXT_NEGATIVE_PHRASES = (
    "无病害",
    "无明显病害",
    "未发现明显",
    "未发现病害",
    "未见明显病害",
    "未见病害",
)

# Gold defect tables also contain a small number of formal condition/history
# rows whose type is not a damage noun. Keep this list table-only so broad
# words do not expand narrative fallback extraction.
_FORMAL_TABLE_DEFECT_TYPES = frozenset(
    (
        "修补",
        "现状",
        "加固",
        "碳纤维",
        "遮掩",
        "外观",
    )
)
_TEXT_SECTION_RE = re.compile(
    r"^\s*(?P<number>(?:第[一二三四五六七八九十百千万0-9]+章)|(?:[0-9]+(?:\.[0-9]+)*))\s*"
    r"(?:[、.．:：)）]|\s|$)"
)
_TEXT_INDEX_RE = re.compile(
    r"^\s*(?P<index>(?:[（(]\s*[0-9一二三四五六七八九十百千万]+\s*[）)]|"
    r"[⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽⑾⑿⒀⒁⒂⒃⒄⒅⒆⒇]|"
    r"[①②③④⑤⑥⑦⑧⑨⑩]|[0-9]+[、.．:：)）]))"
)
_TEXT_LEADING_LABEL_RE = re.compile(
    r"^\s*(?:主要病害(?:是|为)?|现状病害(?:主要在于|主要是|为)?|病害(?:主要是|主要为|是|为)?|"
    r"全桥|本桥|该桥|桥梁|大桥|新发现|发现|检查发现|检测发现|均有|均存在|存在|出现|有|局部|多处|少量|一处|两处|"
    r"[0-9一二三四五六七八九十百千万]+[条处个]?)\s*"
)
_TEXT_LOCATION_START_RE = re.compile(
    r"^\s*(?:左|右|全桥|第[0-9一二三四五六七八九十百千万]+跨|[0-9一二三四五六七八九十百千万]+[#＃号]|"
    + "|".join(re.escape(value) for value in _TEXT_LOCATION_WORDS)
    + r")"
)


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
    *,
    preserve_figure_refs: bool = True,
) -> DefectExtractionResult:
    """Extract individual defect rows from a parsed Word document.

    ``routes`` may be the output of :func:`route_sections`; when omitted it is
    computed.  A routed defect section is preferred.  If no such route has a
    table, tables with defect-shaped headers are consumed in document order
    and ``fallback_defect_table_routing`` is emitted.  Blank index, location,
    and type cells inherit the most recent non-blank value, including across
    repeated headers; descriptions are never merged or rewritten.  Narrative
    fallback is evaluated only after the table pass and only for a missing,
    empty, or clearly underfilled table result.
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

    records: list[DefectObservation] = []
    for table in tables:
        table_records, table_flags = _extract_table(
            table,
            section_label=_section_label_for_table(document, table),
            preserve_figure_refs=preserve_figure_refs,
        )
        records.extend(table_records)
        flags.extend(table_flags)
    if records:
        deduplicated = _deduplicate_table_records(records)
        removed = len(records) - len(deduplicated)
        if removed:
            flags.append(
                _flag(
                    "deduplicated_defect_rows",
                    "Duplicate defect observations were merged, retaining the first description and all source anchors.",
                    removed_row_count=removed,
                    table_row_count=len(records),
                )
            )
        records = list(deduplicated)
    if tables and not records:
        flags.append(
            _flag(
                "no_defect_rows",
                "A defect table was located but no concrete non-header rows were extracted.",
            )
        )

    text_records = _extract_text_defects(
        document,
        active_routes,
        include_legacy_table_sections=not records,
    )
    fallback_reason = _text_fallback_reason(
        tables=tables,
        table_records=records,
        text_records=text_records,
    )
    if fallback_reason is not None:
        before_merge = len(records)
        records = _merge_text_defects(records, text_records)
        flags.append(
            _flag(
                "fallback_defect_text",
                "Text fallback was enabled only for a missing, empty, or underfilled defect table.",
                reason=fallback_reason,
                table_row_count=before_merge,
                text_candidate_count=len(text_records),
                numbered_text_row_count=sum(1 for record in text_records if record.index),
                added_row_count=len(records) - before_merge,
            )
        )
    history_records, history_updated = _enrich_defect_history(document, records)
    if history_updated:
        records = history_records
        flags.append(
            _flag(
                "history_comparison_enriched",
                "Explicit chapter-7 comparison evidence populated defect history fields where a disease-level match was available.",
                updated_row_count=history_updated,
            )
        )
    records = list(_fill_missing_indices(records))
    return DefectExtractionResult(tuple(records), tuple(flags))



_HISTORY_TABLE_HEADERS = {
    "location": ("位置", "部位", "结构部位"),
    "previous": ("上一次检测结果", "上次检测结果", "上一次定检结果", "历史检测结果", "上次病害", "历史病害", "既往病害"),
    "current": ("本次检测结果", "本次定检结果", "当前检测结果", "本次病害", "当前病害"),
    "development": ("发展状况", "发展情况", "变化情况", "病害发展", "发展程度", "变化趋势"),
}
_HISTORY_COMPONENT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("上部结构", ("上部结构", "主梁", "梁", "板", "翼板", "腹板", "横隔", "铰缝", "索", "桥面板")),
    ("下部结构", ("下部结构", "桥墩", "墩", "桥台", "台帽", "台身", "盖梁", "支座", "基础", "挡块")),
    ("桥面系", ("桥面系", "桥面", "铺装", "路面", "伸缩缝", "护栏", "栏杆", "排水", "泄水", "人行道", "路缘")),
    ("主体结构", ("主体结构", "顶板", "侧墙", "墙体", "底板", "衬砌", "洞口", "翼墙")),
    ("附属设施", ("附属设施", "栏杆", "扶手", "照明", "标志", "排水")),
)
_HISTORY_DISEASE_TERMS = tuple(
    dict.fromkeys(
        term
        for term in _TEXT_DEFECT_WORDS
        if len(term) >= 2 and not term.startswith("无")
    )
)

def _history_table_mapping(table: TableBlock) -> tuple[int, dict[str, int]] | None:
    """Resolve history columns across up to four header rows.

    Word often splits “上一次/本次/发展” labels across a multi-level header.
    Logical ``column_index`` must be used instead of the physical ``row.cells``
    position because horizontally merged cells make those two indexes diverge.
    """

    mapping: dict[str, int] = {}
    for row in table.rows[:4]:
        for cell in row.cells:
            value = _normalise_header(cell.raw_text)
            for field, aliases in _HISTORY_TABLE_HEADERS.items():
                if field in mapping:
                    continue
                if any(_normalise_header(alias) in value for alias in aliases):
                    mapping[field] = cell.column_index
                    break
        if {"previous", "current", "development"}.issubset(mapping):
            return row.row_index, mapping
    return None

def _history_cell(row: TableRow, column: int | None) -> TableCell | None:
    if column is None or column < 0:
        return None
    for cell in row.cells:
        start = cell.column_index
        span = max(1, cell.column_span)
        if start <= column < start + span:
            return cell
    return None

def _history_cell_text(row: TableRow, column: int | None) -> str:
    cell = _history_cell(row, column)
    if cell is None:
        return ""
    return _display_text(cell.raw_text).strip("，,；;。 ")

def _history_context_before(document: DocumentModel, table: TableBlock) -> str:
    position = next(
        (index for index, block in enumerate(document.blocks) if block is table),
        -1,
    )
    if position < 0:
        return ""
    values: list[str] = []
    for block in reversed(document.blocks[max(0, position - 4):position]):
        if not isinstance(block, ParagraphBlock):
            continue
        text = _display_text(block.raw_text)
        if text:
            values.append(text)
        if len(values) == 2:
            break
    return " ".join(reversed(values))

def _history_positional_mapping(
    document: DocumentModel,
    table: TableBlock,
) -> tuple[int, dict[str, int]] | None:
    """Conservative fallback for chapter-7 comparison tables without headers.

    This is intentionally narrow: the nearby text must explicitly identify a
    historical/comparison section, the table must have a four-field comparison
    shape (optionally preceded by a serial column), and the prospective
    development column must contain change-language.
    """

    context = _normalise_header(_history_context_before(document, table))
    if not any(marker in context for marker in (
        "历次检测", "历史检测", "检测结果对比", "外观检测结果对比",
        "病害发展", "对比分析",
    )):
        return None
    if not table.rows:
        return None
    columns = sorted({
        column
        for row in table.rows[:8]
        for cell in row.cells
        for column in range(cell.column_index, cell.column_index + max(1, cell.column_span))
    })
    if len(columns) not in {4, 5}:
        return None
    offset = 0
    if len(columns) == 5:
        first_values = [
            _history_cell_text(row, columns[0])
            for row in table.rows[:8]
            if _history_cell_text(row, columns[0])
        ]
        if not first_values or sum(bool(re.fullmatch(r"[0-9一二三四五六七八九十]+", value)) for value in first_values) < max(1, len(first_values) // 2):
            return None
        offset = 1
    mapping = {
        "location": columns[offset],
        "previous": columns[offset + 1],
        "current": columns[offset + 2],
        "development": columns[offset + 3],
    }
    change_hits = 0
    evidence_rows = 0
    for row in table.rows[:10]:
        previous = _history_cell_text(row, mapping["previous"])
        current = _history_cell_text(row, mapping["current"])
        development = _history_cell_text(row, mapping["development"])
        if any((previous, current, development)):
            evidence_rows += 1
        if any(term in development for term in (
            "新增", "新出现", "发展", "加重", "扩大", "减轻", "减少",
            "修复", "消失", "无变化", "未见明显变化", "基本稳定", "稳定",
        )):
            change_hits += 1
    if evidence_rows < 2 or change_hits < 1:
        return None
    return -1, mapping

def _history_groups(document: DocumentModel) -> tuple[dict[str, object], ...]:
    groups: list[dict[str, object]] = []
    for block in document.blocks:
        if not isinstance(block, TableBlock):
            continue
        header = _history_table_mapping(block)
        mapping_kind = "header"
        if header is None:
            header = _history_positional_mapping(document, block)
            mapping_kind = "positional_history"
        if header is None:
            continue
        header_row, mapping = header
        inherited: dict[str, str] = {}
        start = header_row + 1 if header_row >= 0 else 0
        for row in block.rows[start:]:
            values: dict[str, str] = {}
            for field in ("location", "previous", "current", "development"):
                cell = _history_cell(row, mapping.get(field))
                value = _history_cell_text(row, mapping.get(field))
                if value:
                    inherited[field] = value
                elif field == "location" and inherited.get(field):
                    # Blank group labels conventionally inherit down rows.
                    value = inherited[field]
                elif cell is not None and cell.is_merge_continuation and inherited.get(field):
                    value = inherited[field]
                values[field] = value
            if not any((values["previous"], values["current"], values["development"])):
                continue
            groups.append(
                {
                    **values,
                    "block_index": block.block_index,
                    "table_index": block.table_index,
                    "row_index": row.row_index,
                    "mapping_kind": mapping_kind,
                    "source": block.source,
                }
            )
    return tuple(groups)

def _defect_component_key(record: DefectObservation) -> str:
    text = _display_text(
        " ".join((record.location, record.defect_type, record.description))
    )
    for key, terms in _HISTORY_COMPONENT_TERMS:
        if any(term in text for term in terms):
            return key
    return ""

def _disease_terms(value: str) -> tuple[str, ...]:
    compact = _normalise_header(value)
    found = [term for term in _HISTORY_DISEASE_TERMS if term in compact]
    # Composite labels such as 渗水泛碱 should beat their shorter members.
    found.sort(key=len, reverse=True)
    result: list[str] = []
    for term in found:
        if any(term in kept for kept in result):
            continue
        result.append(term)
    return tuple(result[:6])


def _history_location_tokens(value: str) -> tuple[str, ...]:
    text = _display_text(value)
    tokens = re.findall(
        r"(?:左幅|右幅)?\d+#(?:跨|墩|台|缝|支座|梁|板)|"
        r"(?:距)?\d+#(?:伸缩缝|墩|台)|K\d+(?:\+\d+)?",
        text,
        flags=re.IGNORECASE,
    )
    if tokens:
        return tuple(dict.fromkeys(tokens))
    compact = _normalise_header(text)
    generic = {"桥面", "主梁", "桥墩", "桥台", "支座", "护栏", "栏杆", "伸缩缝", "路面", "上部结构", "下部结构", "桥面系"}
    if 3 <= len(compact) <= 24 and compact not in generic:
        return (compact,)
    return ()


def _history_location_match(record: DefectObservation, text: str) -> bool:
    tokens = _history_location_tokens(record.location)
    if not tokens:
        return False
    compact = _normalise_header(text)
    return any(_normalise_header(token) in compact for token in tokens)


def _best_history_group(record: DefectObservation, groups: Sequence[dict[str, object]]) -> dict[str, object] | None:
    component = _defect_component_key(record)
    text = _display_text(" ".join((record.location, record.defect_type, record.description)))
    disease_terms = _disease_terms(text)
    ranked: list[tuple[int, int, dict[str, object]]] = []
    for order, group in enumerate(groups):
        score = 0
        location = _display_text(group.get("location", ""))
        group_text = " ".join((
            _display_text(group.get("current", "")),
            _display_text(group.get("development", "")),
            _display_text(group.get("previous", "")),
        ))
        disease_hits = sum(1 for term in disease_terms if term in group_text)
        if not disease_hits and not (record.defect_type and record.defect_type in group_text):
            continue
        if component and component in location:
            score += 8
        if _history_location_match(record, group_text):
            score += 12
        score += 3 * disease_hits
        if record.defect_type and record.defect_type in group_text:
            score += 5
        if score:
            ranked.append((score, -order, group))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2]

def _enrich_defect_history(
    document: DocumentModel,
    records: Sequence[DefectObservation],
) -> tuple[list[DefectObservation], int]:
    groups = _history_groups(document)
    if not groups:
        return list(records), 0
    enriched: list[DefectObservation] = []
    updated = 0
    for record in records:
        # Respect explicit row-level history columns if the source defect table
        # already contains them.  Chapter-7 evidence only fills defaults.
        if (
            record.previous_status not in {"", "无"}
            or record.development not in {"", "无"}
            or record.is_new not in {"", "否"}
        ):
            enriched.append(record)
            continue
        group = _best_history_group(record, groups)
        if group is None:
            enriched.append(record)
            continue

        defect_terms = _disease_terms(" ".join((record.defect_type, record.description)))
        location = _display_text(group.get("location", ""))
        previous = _display_text(group.get("previous", ""))
        current = _display_text(group.get("current", ""))
        development = _display_text(group.get("development", ""))

        # A separate location column may carry the row identity while the
        # previous/current cells contain only disease text.  Accept that layout
        # only when the location is an exact generic label (e.g. 主梁/桥面) or a
        # specific location token matches.  Do not promote a broad component
        # row such as “上部结构” to every individual girder disease.
        same_location = (
            bool(_normalise_header(record.location))
            and _normalise_header(record.location) == _normalise_header(location)
        )
        location_match = same_location or _history_location_match(record, location)
        previous_match = (
            bool(defect_terms)
            and (_history_location_match(record, previous) or location_match)
            and any(term in previous for term in defect_terms)
        )
        current_match = (
            bool(defect_terms)
            and (_history_location_match(record, current) or location_match)
            and any(term in current for term in defect_terms)
        )
        development_match = bool(defect_terms) and any(term in development for term in defect_terms)
        development_compact = _normalise_header(development)
        generic_new = development_compact in {"新增", "新出现", "本次新增", "新发", "新增病害"}
        generic_stable = development_compact in {"无", "无变化", "未见明显变化", "基本稳定", "较稳定", "稳定"}
        previous_none = _normalise_header(previous) in {"", "无", "未见", "未发现", "无病害", "未发现病害"}

        is_new = record.is_new
        previous_status = record.previous_status
        development_value = record.development
        if previous_match:
            previous_status = previous[:220]
        if "新增" in development and current_match and (development_match or generic_new):
            is_new = "是"
            if previous_none:
                previous_status = "无"
            development_value = development[:220] or "新增"
        elif development_match and current_match and any(
            term in development for term in ("发展", "加重", "扩大", "修复", "减轻", "减少", "变化")
        ):
            if previous_match:
                is_new = "否"
            development_value = development[:220]
        elif generic_stable and previous_match and current_match:
            is_new = "否"
            development_value = "无" if development_compact == "无" else development[:220]
        elif previous_match and current_match:
            # The same disease is explicitly present in both periods even when
            # the table omits a development phrase.
            is_new = "否"

        if (is_new, previous_status, development_value) != (
            record.is_new,
            record.previous_status,
            record.development,
        ):
            updated += 1
            history_source = group.get("source")
            evidence = list(record.evidence)
            if isinstance(history_source, SourceAnchor) and history_source not in evidence:
                evidence.append(history_source)
            enriched.append(
                replace(
                    record,
                    is_new=is_new,
                    previous_status=previous_status,
                    development=development_value,
                    evidence=tuple(evidence),
                )
            )
        else:
            enriched.append(record)
    return enriched, updated


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
        if marked:
            return marked, False
        # A broad heading can route an ordinary inspection table as defects.
        # Fall back to the real defect-shaped table elsewhere in the document
        # rather than extracting the misrouted table.
        structural = tuple(
            table
            for table in document.blocks
            if isinstance(table, TableBlock) and _looks_like_defect_table(table)
        )
        if structural:
            return structural, True
        return routed_tables, False

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
    *,
    preserve_figure_refs: bool = True,
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
                value = _clean_description(
                    value,
                    preserve_figure_refs=preserve_figure_refs,
                )
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

        reason = is_valid_defect_row(values, row=row, column_map=column_map)
        if reason == "non_defect_state" and analysis.ambiguous:
            # Preserve a non-empty row when header semantics are already
            # ambiguous; the ambiguity flag is the review signal and the row
            # should not be discarded a second time.
            reason = None
        if reason is not None:
            if reason == "photo_caption":
                matched_indices = _merge_caption_row(result, table, row)
                if matched_indices:
                    flags.append(
                        _flag(
                            "photo_caption_row_merged",
                            "Photo/figure caption cells were merged into the defects they document instead of creating duplicate records.",
                            **details,
                            row_index=row.row_index,
                            defect_indices=list(matched_indices),
                        )
                    )
                else:
                    flags.append(
                        _flag(
                            "unmapped_caption_row",
                            "A photo/figure caption row matched no existing defect; it is kept as a quality marker instead of a formal record.",
                            **details,
                            row_index=row.row_index,
                            cell_text=_display_text(" ".join(cell.raw_text for cell in row.cells))[:60],
                        )
                    )
            else:
                flags.append(
                    _flag(
                        "excluded_non_defect_row",
                        "A table row was excluded because it is not a concrete defect observation.",
                        **details,
                        row_index=row.row_index,
                        reason=reason,
                        cell_text=_display_text(" ".join(cell.raw_text for cell in row.cells))[:60],
                    )
                )
            continue

        _expand_lane_and_section(values, section_label)

        anchors = _row_anchors(table, row)
        for field in _INHERITED_FIELDS:
            if field in origins and origins[field] not in anchors:
                anchors.append(origins[field])
        if _caption_marker(values["description"] or ""):
            matched_indices = _merge_caption_row(
                result,
                table,
                row,
                whole_row=True,
            )
            if matched_indices:
                flags.append(
                    _flag(
                        "photo_caption_row_merged",
                        "A photo/figure caption description was merged into the defect it documents instead of creating a duplicate record.",
                        **details,
                        row_index=row.row_index,
                        defect_indices=list(matched_indices),
                    )
                )
                continue
            flags.append(
                _flag(
                    "caption_like_description_kept",
                    "A description starts with a photo/figure marker but maps to no existing defect; it is kept as a formal record.",
                    **details,
                    row_index=row.row_index,
                )
            )
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
                "Missing defect history fields remain internally missing; the official renderer displays them as 无.",
                fields=_ordered_fields(defaulted_fields),
            )
        )
    return result, flags


_CAPTION_MARKER_RE = re.compile(
    r"^(?:照片|照|图|附图|表)\s*[0-9一二三四五六七八九十百千]+(?:[.\-][0-9一二三四五六七八九十百千]+)*"
)
_FIGURE_REF_RE = re.compile(
    r"(?:照片|照|图|附图|表)\s*([0-9一二三四五六七八九十百千]+(?:[.\-][0-9一二三四五六七八九十百千]+)*)"
)
_FIGURE_REF_STRIP_RE = re.compile(
    r"(?:照片|照|图|附图|表)\s*[0-9一二三四五六七八九十百千]+(?:[.\-][0-9一二三四五六七八九十百千]+)*"
)
_UNIT_ROW_RE = re.compile(
    r"^(?:单位|备注|注|说明|数量|表注)(?:[:：][^，。；;]{0,30})?$"
)
_PLACEHOLDER_ROW_RE = re.compile(
    r"^(?:[-—–/…·．.]+|无|空白|空|无病害|无缺陷|未发现病害|未发现缺陷|"
    r"本(?:表|页)(?:无|未发现)(?:病害|缺陷)?|以下空白|（以下空白）|"
    r"本表无病害|本页无病害|无内容|暂无)$"
)
_CONTINUATION_MARKERS = frozenset(("续表", "表续", "接上表", "续上表", "上表", "续"))


def is_valid_defect_row(
    values: Mapping[str, str],
    row: TableRow | None = None,
    column_map: Mapping[str, int] | None = None,
) -> str | None:
    """Return a reason when a parsed table row is not a concrete defect row.

    ``None`` means the row is a valid defect observation.  Reasons cover
    photo/figure caption rows (``photo_caption``), repeated header rows
    (``repeated_header``), unit/label or placeholder/layout rows
    (``unit_or_placeholder``), and empty rows (``placeholder``).  Valid rows
    keep their original descriptions, including figure/photo references,
    counts, dimensions, crack widths, and locations.
    """

    if not any(_display_text(values.get(field) or "") for field in _REQUIRED_FIELDS):
        return "placeholder"
    if row is not None and _is_repeated_header_row(row):
        return "repeated_header"
    if _is_caption_row(values, row=row, column_map=column_map):
        return "photo_caption"
    if _is_unit_or_placeholder_row(row, values):
        return "unit_or_placeholder"
    if not _has_concrete_defect_meaning(values):
        # An ambiguous header map is already recorded as a quality flag. Keep
        # the non-empty row for review instead of compounding that uncertainty
        # with a false negative from the semantic gate.
        if column_map and len(set(column_map.values())) < len(column_map):
            return None
        return "non_defect_state"
    return None


def _has_concrete_defect_meaning(values: Mapping[str, str]) -> bool:
    """Keep valid formal-table defect rows out of the closed-vocabulary gate."""

    defect_type = _display_text(values.get("defect_type") or "")
    description = _display_text(values.get("description") or "")
    text = _display_text(f"{defect_type} {description}")
    if not text or _is_placeholder_text(defect_type):
        return False

    type_has_defect_marker = any(
        marker in defect_type for marker in _TEXT_DEFECT_WORDS
    )
    type_is_formal_state = defect_type in _FORMAL_TABLE_DEFECT_TYPES
    text_has_defect_marker = any(marker in text for marker in _TEXT_DEFECT_WORDS)

    if any(negative in text for negative in _TEXT_NEGATIVE_PHRASES):
        return type_has_defect_marker or type_is_formal_state
    return text_has_defect_marker or type_is_formal_state


def _is_repeated_header_row(row: TableRow) -> bool:
    cells = [cell for cell in row.cells if _display_text(cell.raw_text)]
    if not cells:
        return False
    for cell in cells:
        text = _normalise_header(cell.raw_text)
        if not text:
            continue
        if text in _CONTINUATION_MARKERS:
            continue
        if not any(
            _alias_matches(text, alias)
            for aliases in _HEADER_ALIASES.values()
            for alias in aliases
        ):
            return False
    return True


def _is_caption_row(
    values: Mapping[str, str],
    *,
    row: TableRow | None,
    column_map: Mapping[str, int] | None,
) -> bool:
    description = _display_text(values.get("description") or "")
    index = _display_text(values.get("index") or "")
    if _caption_marker(index):
        return not description or description == index
    if row is not None and column_map:
        mapped_columns = set(column_map.values())
        for cell in row.cells:
            text = _display_text(cell.raw_text)
            if not text or not _caption_marker(text):
                continue
            covered = set(
                range(cell.column_index, cell.column_index + max(1, cell.column_span))
            )
            if mapped_columns <= covered:
                return True
    return False


def _is_unit_or_placeholder_row(
    row: TableRow | None,
    values: Mapping[str, str],
) -> bool:
    if row is not None:
        cells = [cell for cell in row.cells if _display_text(cell.raw_text)]
        if cells:
            return all(
                _is_unit_text(_display_text(cell.raw_text))
                or _is_placeholder_text(_display_text(cell.raw_text))
                for cell in cells
            )
    row_text = "".join(
        _display_text(values.get(field) or "")
        for field in _FIELD_ORDER
        if field not in _DEFAULT_FIELDS
        or _display_text(values.get(field) or "") != _DEFAULT_FIELDS[field]
    )
    return bool(row_text) and (
        _is_unit_text(row_text) or _is_placeholder_text(row_text)
    )


def _is_unit_text(text: str) -> bool:
    return _UNIT_ROW_RE.fullmatch(text) is not None


def _is_placeholder_text(text: str) -> bool:
    return _PLACEHOLDER_ROW_RE.fullmatch(text) is not None


def _caption_marker(text: str) -> bool:
    return _CAPTION_MARKER_RE.match(_display_text(text)) is not None


def _figure_references(text: str) -> set[str]:
    return {match.group(1) for match in _FIGURE_REF_RE.finditer(text or "")}


def _strip_figure_refs(text: str) -> str:
    return _FIGURE_REF_STRIP_RE.sub("", text or "")


def _merge_caption_row(
    result: list[DefectObservation],
    table: TableBlock,
    row: TableRow,
    *,
    whole_row: bool = False,
) -> tuple[str, ...]:
    """Merge a photo/caption row into the defect(s) it documents.

    Each caption cell is matched to an existing record from the same table
    by a shared figure/photo reference or, failing that, by description
    containment preferring the nearest preceding defect row (adjacent-row
    evidence).  Matched records keep their original description and index;
    the caption cell's source anchor is added to the record's evidence.
    Returns the matched defect indices in row order.  With ``whole_row`` the
    entire row is treated as one caption and all of its anchors are merged
    into the single matched record.
    """

    if whole_row:
        caption_text = _display_text(" ".join(cell.raw_text for cell in row.cells))
        if not caption_text:
            return ()
        position = _match_caption_record(
            result,
            _figure_references(caption_text),
            _text_semantic(_strip_figure_refs(caption_text)),
            row.row_index,
        )
        if position is None:
            return ()
        record = result[position]
        result[position] = replace(
            record,
            evidence=_unique_anchors(record.evidence, _row_anchors(table, row)),
        )
        return (record.index,)

    matched_indices: list[str] = []
    for cell in row.cells:
        cell_text = _display_text(cell.raw_text)
        if not cell_text:
            continue
        position = _match_caption_record(
            result,
            _figure_references(cell_text),
            _text_semantic(_strip_figure_refs(cell_text)),
            row.row_index,
        )
        if position is None:
            continue
        record = result[position]
        result[position] = replace(
            record,
            evidence=_unique_anchors(record.evidence, (_cell_anchor(table, cell),)),
        )
        if record.index not in matched_indices:
            matched_indices.append(record.index)
    return tuple(matched_indices)


def _match_caption_record(
    result: Sequence[DefectObservation],
    caption_refs: set[str],
    caption_core: str,
    caption_row_index: int,
) -> int | None:
    """Find the table record documented by one caption cell.

    Figure/photo references are unique per defect, so any exact reference
    match wins.  Without a reference, the nearest preceding defect row whose
    description overlaps the caption is preferred (adjacent-row evidence).
    """

    for position in range(len(result) - 1, -1, -1):
        record = result[position]
        if caption_refs and (_figure_references(record.description) & caption_refs):
            return position
    best_position: int | None = None
    best_distance: int | None = None
    for position, record in enumerate(result):
        record_core = _text_semantic(_strip_figure_refs(record.description))
        if len(record_core) < 4 or not caption_core:
            continue
        if not (record_core in caption_core or caption_core in record_core):
            continue
        distance = caption_row_index - _record_row(record)
        if distance >= 0 and (best_distance is None or distance < best_distance):
            best_position = position
            best_distance = distance
    if best_position is not None:
        return best_position
    for position, record in enumerate(result):
        record_core = _text_semantic(_strip_figure_refs(record.description))
        if len(record_core) < 4 or not caption_core:
            continue
        if record_core in caption_core or caption_core in record_core:
            return position
    return None


def _record_row(record: DefectObservation) -> int:
    rows = [anchor.row_index for anchor in record.evidence if anchor.row_index is not None]
    return min(rows) if rows else -1


def _unique_anchors(
    *anchor_groups: Iterable[SourceAnchor],
) -> tuple[SourceAnchor, ...]:
    result: list[SourceAnchor] = []
    for group in anchor_groups:
        for anchor in group:
            if anchor not in result:
                result.append(anchor)
    return tuple(result)


def _deduplicate_table_records(
    records: Sequence[DefectObservation],
) -> tuple[DefectObservation, ...]:
    """Merge duplicate defect observations from table rows.

    Duplicates share facility/component location, defect type, and either an
    identical description, a shared figure/photo reference, or a
    containment/adjacent-row description overlap.  The first record keeps its
    original description; all source anchors from every duplicate are kept.
    """

    result: list[DefectObservation] = []
    for record in records:
        for position, existing in enumerate(result):
            if _same_defect_observation(existing, record):
                result[position] = replace(
                    existing,
                    evidence=_unique_anchors(existing.evidence, record.evidence),
                )
                break
        else:
            result.append(record)
    return tuple(result)


def _same_defect_observation(
    left: DefectObservation,
    right: DefectObservation,
) -> bool:
    if _text_semantic(left.location) != _text_semantic(right.location):
        return False
    if _text_semantic(left.defect_type) != _text_semantic(right.defect_type):
        return False
    left_description = _text_semantic(_clean_description(left.description))
    right_description = _text_semantic(_clean_description(right.description))
    if left_description and left_description == right_description:
        return True
    if _figure_references(left.description) & _figure_references(right.description):
        return True
    if (
        len(left_description) >= 4
        and len(right_description) >= 4
        and (
            left_description in right_description
            or right_description in left_description
        )
    ):
        return True
    return False


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
    if lane_match and _LANE_PREFIX_RE.match(location) is None:
        location = lane_match.group(1) + location
    if location in ("左幅", "右幅") and section_label and not location.endswith(section_label):
        location = location + section_label
    if location != (values.get("location") or ""):
        values["location"] = location


_TRAILING_FIGURE_REFERENCE_RE = re.compile(
    r"(?:[，,；;。\s]+)(?:见)?(?:照片|照|附图|图)\s*"
    r"[0-9一二三四五六七八九十百千]+(?:[.\-][0-9一二三四五六七八九十百千]+)*"
    r"\s*[。．.]?$"
)


def _clean_description(
    value: str,
    *,
    preserve_figure_refs: bool = False,
) -> str:
    """Normalize layout whitespace and optional trailing photo references."""

    cleaned = _display_text(value)
    if preserve_figure_refs:
        return cleaned
    return _TRAILING_FIGURE_REFERENCE_RE.sub("", cleaned).strip("，,；;。． ")


def _fill_missing_indices(
    records: Sequence[DefectObservation],
) -> tuple[DefectObservation, ...]:
    """Supply stable display indices only for rows whose source index is empty."""

    result: list[DefectObservation] = []
    for position, record in enumerate(records, start=1):
        result.append(
            record if record.index.strip() else replace(record, index=str(position))
        )
    return tuple(result)


def _extract_text_defects(
    document: DocumentModel,
    routes: Sequence[SectionRoute],
    *,
    include_legacy_table_sections: bool = False,
) -> tuple[DefectObservation, ...]:
    """Extract conservative defect candidates from routed narrative text.

    Text is deliberately inspected only inside the conclusion/safety routes
    and numbered ``外观检查``/``病害检查`` subsections.  A sentence must carry
    a concrete component/location and an observed defect; generic inspection
    instructions and negative ``无病害`` statements are ignored.
    """

    blocks = _text_defect_blocks(document, routes)
    candidates: list[DefectObservation] = []
    for block in blocks:
        index = _text_index(block.raw_text)
        for context, clause in _text_clauses(block.raw_text):
            candidate = _text_candidate(clause, context, index, block.source)
            if candidate is not None:
                candidates.append(candidate)
    if include_legacy_table_sections:
        candidates.extend(_extract_legacy_table_section_defects(document))
    return _deduplicate_text_defects(candidates)


def _extract_legacy_table_section_defects(
    document: DocumentModel,
) -> tuple[DefectObservation, ...]:
    """Read only bounded appearance-disease sections in flattened Word tables.

    A small set of legacy reports is parsed by Word as one non-defect table,
    so their narrative paragraphs are not represented as ``ParagraphBlock``
    instances.  When the normal table pass produced no rows, recover the
    explicit ``4.2 外观病害检查`` table text only.  This deliberately avoids
    scanning the report-wide ``TableBlock.raw_text``.
    """

    candidates: list[DefectObservation] = []
    for block in document.blocks:
        if not isinstance(block, TableBlock) or _looks_like_defect_table(block):
            continue
        section = _legacy_appearance_section(block.raw_text)
        if not section:
            continue
        candidates.extend(_extract_legacy_table_rows(block, section))
    return tuple(candidates)


def _legacy_appearance_section(raw_text: str) -> str:
    text = _display_text(raw_text)
    matches = list(re.finditer(r"外观病害检查", text))
    if not matches:
        return ""
    start = matches[-1].start()
    end_match = re.search(r"4\.3\s*", text[matches[-1].end() :])
    end = matches[-1].end() + end_match.start() if end_match is not None else len(text)
    return text[start:end]


def _extract_legacy_table_rows(
    table: TableBlock,
    section: str,
) -> tuple[DefectObservation, ...]:
    header = section.find("序号")
    if header < 0:
        return ()
    body = section[header + len("序号") :]
    starts = list(_legacy_row_starts(body))
    if not starts:
        return ()

    candidates: list[DefectObservation] = []
    for row_number, start in enumerate(starts):
        row_end = starts[row_number + 1].start() if row_number + 1 < len(starts) else len(body)
        location = _legacy_row_location(body[start.end() :])
        if not location:
            continue
        rest = body[start.end() + len(location) : row_end]
        rest = rest.strip()
        table_type, description = _legacy_row_fields(rest)
        for part_index, part in enumerate(_legacy_description_parts(description)):
            candidate = _legacy_text_candidate(
                table=table,
                index=start.group("index"),
                location=location,
                table_type=table_type,
                description=part,
                part_index=part_index,
            )
            if candidate is not None:
                candidates.append(candidate)
    return tuple(candidates)


def _legacy_row_starts(text: str) -> Iterator[re.Match[str]]:
    locations = sorted(set(_TEXT_LOCATION_WORDS), key=len, reverse=True)
    pattern = re.compile(
        r"(?<![0-9#])(?P<index>[0-9]{1,2})(?=(?:"
        + "|".join(re.escape(value) for value in locations)
        + r"))"
    )
    return iter(pattern.finditer(text))


def _legacy_row_location(text: str) -> str:
    for location in sorted(set(_TEXT_LOCATION_WORDS), key=len, reverse=True):
        if text.startswith(location):
            return location
    return ""


def _legacy_row_fields(rest: str) -> tuple[str, str]:
    if rest.startswith(("/", "／")):
        return "", rest[1:].strip()
    spans = _text_defect_spans(rest)
    if not spans or spans[0][0] > 1:
        return "", rest
    end = spans[0][1]
    while True:
        connector = re.match(r"[、，,;；/／和及与\s]*", rest[end:])
        next_start = end + (connector.end() if connector is not None else 0)
        next_span = next(
            (span for span in _text_defect_spans(rest) if span[0] == next_start),
            None,
        )
        if next_span is None:
            break
        end = next_span[1]
    return rest[spans[0][0] : end].strip("、，,;；/／ "), rest[end:].strip(" 、，,;；")


def _legacy_description_parts(description: str) -> tuple[str, ...]:
    description = description.strip(" 、，,;；")
    if not description:
        return ()
    positional = tuple(
        part.strip(" 、，,;；")
        for part in re.split(r"(?=第[一二三四五六七八九十百千万0-9]+跨)", description)
        if part.strip(" 、，,;；")
    )
    if len(positional) > 1 and all(_has_text_defect(part) for part in positional):
        return positional

    # Merged cells in flattened legacy tables can join a second member
    # without punctuation.  Split only the two concrete repeated-member
    # forms observed in the source section.
    if "梁体混凝土局部破损" in description and "梁体渗水" in description:
        split_at = description.find("梁体渗水")
        return (
            description[:split_at].strip(" 、，,;；"),
            description[split_at:].strip(" 、，,;；"),
        )
    marker = "防护网锈蚀防护网局部锈蚀"
    if marker in description:
        first = description.find(marker)
        second = first + len("防护网锈蚀")
        return (
            description[:first].strip(" 、，,;；"),
            description[second:].strip(" 、，,;；"),
        )
    return (description,)


def _legacy_text_candidate(
    *,
    table: TableBlock,
    index: str,
    location: str,
    table_type: str,
    description: str,
    part_index: int,
) -> DefectObservation | None:
    spans = _text_defect_spans(description)
    if not spans:
        return None
    if (
        ("外观状况良好" in description or "未发现可见裂缝" in description)
        and not any(term in description for term in ("无排水", "无泄水", "未设置", "缺失"))
    ):
        return None

    defect_type = table_type or _text_type(spans)
    if not table_type:
        if any(term in description for term in ("无泄水孔", "无排水设施", "未设置")):
            defect_type = "设施缺失"
    if part_index and description.startswith("梁体渗水"):
        values = [span[2] for span in spans if span[2] != "脱落"]
        defect_type = _text_type(
            [(0, 0, value) for value in values]
        ) or defect_type

    part_location = location
    if part_index and description.startswith("防护网"):
        part_location = "防护网"
    source = SourceAnchor(
        source_file=table.source.source_file,
        block_index=table.block_index,
        raw_text=description,
        table_index=table.table_index,
    )
    return DefectObservation(
        index=index,
        location=part_location,
        defect_type=defect_type,
        description=description,
        is_new=_DEFAULT_FIELDS["is_new"],
        previous_status=_DEFAULT_FIELDS["previous_status"],
        development=_DEFAULT_FIELDS["development"],
        evidence=(source,),
    )


def _text_defect_blocks(
    document: DocumentModel,
    routes: Sequence[SectionRoute],
) -> tuple[ParagraphBlock, ...]:
    allowed: set[int] = {
        block.block_index
        for route in routes
        if route.category
        in (SectionCategory.INSPECTION_CONCLUSION, SectionCategory.SAFETY_ASSESSMENT)
        for block in route.blocks
        if isinstance(block, ParagraphBlock)
    }
    active_depth: int | None = None
    for block in document.blocks:
        if not isinstance(block, ParagraphBlock):
            continue
        text = _display_text(block.raw_text)
        heading_depth = _text_heading_depth(text)
        if heading_depth is not None and active_depth is not None and heading_depth <= active_depth:
            active_depth = None
        if heading_depth is not None and _is_text_defect_heading(text):
            active_depth = heading_depth
            continue
        if active_depth is not None:
            allowed.add(block.block_index)
    return tuple(
        block
        for block in document.blocks
        if isinstance(block, ParagraphBlock) and block.block_index in allowed
    )


def _text_heading_depth(text: str) -> int | None:
    match = _TEXT_SECTION_RE.match(text)
    if match is None:
        return None
    number = match.group("number")
    if number.startswith("第"):
        return 1
    return number.count(".") + 1


def _is_text_defect_heading(text: str) -> bool:
    return "外观检查" in text or "病害检查" in text


def _text_index(text: str) -> str:
    match = _TEXT_INDEX_RE.match(text)
    if match is None:
        return ""
    value = match.group("index").strip("（()）)．.、:：")
    return value if value.isdigit() else ""


def _text_clauses(text: str) -> tuple[tuple[str, str], ...]:
    cleaned = " ".join((text or "").replace("\u00a0", " ").split())
    if not cleaned:
        return ()
    cleaned = re.split(
        r"具体(?:病害|检测)?(?:情况|结果)?见表|现场病害(?:典型)?照片?见|病害照片见",
        cleaned,
        maxsplit=1,
    )[0]
    result: list[tuple[str, str]] = []
    for sentence in re.split(r"(?<=[。！？；;])", cleaned):
        sentence = sentence.strip(" \t\r\n。！？；;，,")
        if not sentence:
            continue
        context = ""
        if "：" in sentence or ":" in sentence:
            separator = "：" if "：" in sentence else ":"
            possible_context, sentence = sentence.split(separator, 1)
            if _has_text_location(possible_context):
                context = possible_context.strip()
        for clause in _split_text_commas(sentence):
            expanded = _split_text_slashes(clause)
            result.extend((context, part) for part in expanded if part.strip())
    return tuple(result)


def _split_text_commas(text: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    for match in re.finditer(r"[，,]", text):
        right = text[match.end() :]
        if not (_looks_like_text_location_start(right) and _has_text_defect(right)):
            continue
        parts.append(text[start : match.start()])
        start = match.end()
    parts.append(text[start:])
    return tuple(part.strip(" ，,") for part in parts if part.strip(" ，,"))


def _split_text_slashes(text: str) -> tuple[str, ...]:
    shared_match = re.match(
        r"^\s*(?P<left>[^/／、，;；]+)[/／](?P<right>[^/／、，;；]+?)"
        r"(?:两处|两侧)?(?:均)?(?:存在|出现)(?P<tail>.+?)\s*$",
        text,
    )
    if shared_match is not None:
        left = shared_match.group("left").strip()
        right = shared_match.group("right").strip()
        if (
            _has_text_location(left)
            or _has_text_location(right)
            or left.endswith("部位")
            or right.endswith("部位")
        ):
            tail = shared_match.group("tail").strip()
            return (f"{left}存在{tail}", f"{right}存在{tail}")
    if "/" not in text and "／" not in text:
        return (text,)
    parts = [part.strip() for part in re.split(r"[/／]", text) if part.strip()]
    if len(parts) < 2 or not all(_has_text_defect(part) for part in parts):
        return (text,)
    if all(_looks_like_text_location_start(part) for part in parts):
        return tuple(parts)
    return (text,)


def _text_candidate(
    clause: str,
    context: str,
    index: str,
    source: SourceAnchor,
) -> DefectObservation | None:
    description = _clean_description(_TEXT_INDEX_RE.sub("", clause, count=1).strip(" ，,；;"))
    if re.match(r"^(?:照片|图|见图|附图)\s*[0-9一二三四五六七八九十.-]", description):
        return None
    if not description or any(phrase in description for phrase in _TEXT_NEGATIVE_PHRASES):
        if not any(word in description for word in _TEXT_POSITIVE_WORDS):
            return None
    spans = _text_defect_spans(description)
    if not spans:
        return None
    if not any(word in description for word in _TEXT_POSITIVE_WORDS):
        if not context or not _has_text_location(context):
            return None
    location = _text_location(description[: spans[0][0]], context)
    if not location:
        return None
    defect_type = _text_type(spans)
    if not defect_type:
        return None
    return DefectObservation(
        index=index,
        location=location,
        defect_type=defect_type,
        description=description,
        is_new=_DEFAULT_FIELDS["is_new"],
        previous_status=_DEFAULT_FIELDS["previous_status"],
        development=_DEFAULT_FIELDS["development"],
        evidence=(source,),
    )


def _text_defect_spans(text: str) -> list[tuple[int, int, str]]:
    pattern = "|".join(re.escape(word) for word in _TEXT_DEFECT_WORDS)
    matches = list(re.finditer(pattern, text))
    selected: list[tuple[int, int, str]] = []
    for match in matches:
        span = (match.start(), match.end(), match.group(0))
        if any(match.start() < end and start < match.end() for start, end, _ in selected):
            continue
        selected.append(span)
    return selected


def _text_type(spans: Sequence[tuple[int, int, str]]) -> str:
    values: list[str] = []
    for _, _, value in spans:
        if value not in values:
            values.append(value)
    return "、".join(values)


def _text_location(prefix: str, context: str) -> str:
    value = prefix.strip(" ，,；;：:") or context.strip(" ，,；;：:")
    value = _TEXT_INDEX_RE.sub("", value, count=1).strip()
    value = re.sub(r"(?:具体病害|病害情况|病害类型|现状病害|主要病害)(?:主要在于|主要是|主要为|为|是)?", "", value)
    value = re.sub(r"(?:主要集中在|主要分布在|分布在)\s*", "", value)
    for _ in range(3):
        new_value = _TEXT_LEADING_LABEL_RE.sub("", value).strip()
        if new_value == value:
            break
        value = new_value
    for _ in range(3):
        new_value = re.sub(
            r"[0-9一二三四五六七八九十百千万]+[条处个]\s*$|"
            r"(?:存在|出现|有|均有|均存在|局部|多处|少量|一处|两处|新发现|发现|位于|处于)\s*$",
            "",
            value,
        ).strip()
        if new_value == value:
            break
        value = new_value
    value = value.strip(" ，,：:的")
    if not value or not _has_text_location(value):
        return ""
    specific = re.search(r"(?:左|右)幅|第[0-9一二三四五六七八九十百千万]+跨|[0-9]+[#＃号]|[0-9一二三四五六七八九十百千万]+墩|[0-9一二三四五六七八九十百千万]+台", value)
    if specific is None:
        location_matches = list(re.finditer("|".join(re.escape(word) for word in _TEXT_LOCATION_WORDS), value))
        if location_matches:
            value = value[location_matches[-1].start() :]
    return value.strip(" ，,：:的")


def _has_text_location(text: str) -> bool:
    return (
        any(word in text for word in _TEXT_LOCATION_WORDS)
        or bool(re.search(r"(?:左|右)幅\b", text))
        or bool(re.search(r"(?:部位|构件)\s*$", text))
    )


def _looks_like_text_location_start(text: str) -> bool:
    return _TEXT_LOCATION_START_RE.match(text) is not None


def _has_text_defect(text: str) -> bool:
    return bool(_text_defect_spans(text))


def _deduplicate_text_defects(
    records: Sequence[DefectObservation],
) -> tuple[DefectObservation, ...]:
    result: list[DefectObservation] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        key = (
            _text_semantic(record.location),
            _text_semantic(record.defect_type),
            _text_semantic(record.description),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return tuple(result)


def _text_fallback_reason(
    *,
    tables: Sequence[TableBlock],
    table_records: Sequence[DefectObservation],
    text_records: Sequence[DefectObservation],
) -> str | None:
    if not tables:
        return "missing_defect_table"
    if not table_records:
        return "empty_defect_table_result"
    numbered_text_rows = sum(1 for record in text_records if record.index)
    if numbered_text_rows >= len(table_records) + 2:
        return "text_candidates_exceed_table_rows"
    return None


def _merge_text_defects(
    table_records: Sequence[DefectObservation],
    text_records: Sequence[DefectObservation],
) -> list[DefectObservation]:
    result = list(table_records)
    for candidate in text_records:
        if any(_table_row_covers_text(table, candidate) for table in table_records):
            continue
        if any(_same_text_record(existing, candidate) for existing in result):
            continue
        result.append(candidate)
    return result


def _table_row_covers_text(
    table_record: DefectObservation,
    text_record: DefectObservation,
) -> bool:
    if _text_semantic(table_record.location) != _text_semantic(text_record.location):
        return False
    if _text_semantic(table_record.defect_type) != _text_semantic(text_record.defect_type):
        return False
    table_description = _text_semantic(_clean_description(table_record.description))
    text_description = _text_semantic(_clean_description(text_record.description))
    if not table_description or not text_description:
        return False
    shorter, longer = sorted((table_description, text_description), key=len)
    return len(shorter) >= 4 and shorter in longer


def _same_text_record(left: DefectObservation, right: DefectObservation) -> bool:
    return (
        _text_semantic(left.location) == _text_semantic(right.location)
        and _text_semantic(left.defect_type) == _text_semantic(right.defect_type)
        and _text_semantic(left.description) == _text_semantic(right.description)
    )


def _text_semantic(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", value or "").casefold())


def _ordered_fields(fields: Sequence[str] | set[str] | frozenset[str] | dict[str, object]) -> list[str]:
    field_set = set(fields)
    return [field for field in _FIELD_ORDER if field in field_set]


def _flag(code: str, message: str, **details: object) -> QualityFlag:
    flag: QualityFlag = {"code": code, "message": message}
    if details:
        flag["details"] = details
    return flag
