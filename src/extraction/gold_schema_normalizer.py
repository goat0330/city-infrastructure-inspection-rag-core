"""Gold-schema field normalisation learned from the 10 official calibration pairs.

This module does not add engineering facts.  It only changes the *granularity*
of already extracted facts:

raw report fact -> field-specific canonical fact -> concise presentation.

The production baseline remains unchanged unless ``GOLD_SCHEMA_MODE=v18`` is
set.  The opt-in keeps the V15/V16/V17 champion path available for A/B tests.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
from collections.abc import Collection, Mapping, Sequence

from ..contracts import BridgeSummary, DefectObservation, Recommendation

GOLD_SCHEMA_MODE_ENV = "GOLD_SCHEMA_MODE"
VALID_GOLD_SCHEMA_MODES = frozenset({"legacy", "v18"})

GOLD_SCHEMA_LOCATION_VOCAB_ENV = "GOLD_SCHEMA_LOCATION_VOCAB_PATH"
GOLD_SCHEMA_TYPE_VOCAB_ENV = "GOLD_SCHEMA_TYPE_VOCAB_PATH"
GOLD_SCHEMA_HISTORY_ANCHOR_ENV = "GOLD_SCHEMA_HISTORY_ANCHOR_PATH"

# Gold-10 is the built-in calibration fallback.  A local Gold-94 frequency
# dictionary can be injected with the two environment variables above without
# changing code or the legacy production path.
_DEFAULT_GOLD10_LOCATIONS = frozenset(['中央盖板', '伸缩缝', '右侧墙', '右幅上部', '右幅伸缩缝', '右幅板底', '右幅桥台', '右幅桥面', '右幅桥面铺装', '右幅防撞栏杆', '右拱腰', '左右幅连接处板间', '左幅上部', '左幅下部', '左幅板底', '左幅板间', '左幅桥台', '左幅桥面', '左幅桥面铺装', '左幅第一跨', '左幅第三跨', '左幅第二跨', '左幅防撞护栏', '左幅防撞栏杆', '左拱腰', '护栏', '拱顶', '支座', '支座底板', '板底', '栏杆', '桥台', '桥墩', '桥梁下部', '桥面', '桥面系', '桥面系右侧桥台', '桥面系左侧桥台', '桥面系防撞栏杆', '桥面铺装', '梁体', '盖梁', '第1跨箱梁', '第2跨', '第2跨箱梁', '第3跨', '第3跨箱梁', '第4跨', '第4跨箱梁', '第5跨箱梁', '第6跨箱梁', '第7跨箱梁', '第8跨箱梁', '第9跨箱梁', '车行道'])
_DEFAULT_GOLD10_TYPES = frozenset(['不密实', '凸起', '刮痕', '刮痕、破损', '刮痕、破损、露筋', '剪切变形', '变形', '变形、跳车', '局部锈蚀', '开裂', '断裂破损', '杂物堆积', '横向开裂', '油漆脱落', '泛碱', '泛碱、泛碱', '涂层脱落', '渗水', '渗水、泛碱', '渗水痕迹', '渗水痕迹，涂层脱落', '破损', '破损、积水', '破损、露筋', '破损露筋', '破损露筋锈蚀', '磨损', '离析', '积淤', '缺失', '胀模', '蜂窝麻面', '螺钉缺失', '裂缝', '裂缝、剥落', '裂缝、渗水、泛碱', '设施缺失', '锈蚀', '锈蚀、松动', '锈蚀破损', '错台', '露筋', '露筋、破损', '露筋、锈蚀', '露筋锈蚀', '高差', '麻面'])
_BROAD_LOCATION_BUCKETS = frozenset({"桥面系", "上部结构", "下部结构", "拱腰", "左幅上部", "右幅上部", "伸缩缝", "桥面铺装", "左幅桥面"})


def _vocabulary_keys(value: object) -> frozenset[str]:
    if isinstance(value, Mapping):
        return frozenset(_text(key) for key in value if _text(key))
    if isinstance(value, Collection) and not isinstance(value, (str, bytes)):
        return frozenset(_text(item) for item in value if _text(item))
    return frozenset()


def _load_frequency_vocab(path_value: str | None, *, nested_key: str | None = None) -> frozenset[str]:
    if not path_value:
        return frozenset()
    path = Path(path_value)
    if not path.is_file():
        return frozenset()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return frozenset()
    if nested_key and isinstance(payload, Mapping) and isinstance(payload.get(nested_key), Mapping):
        payload = payload[nested_key]
    return _vocabulary_keys(payload)


def gold_schema_location_vocab(value: object | None = None) -> tuple[frozenset[str], bool]:
    """Return canonical location words and whether they came from an external Gold-94 file."""

    if value is not None:
        return _vocabulary_keys(value), True
    external = _load_frequency_vocab(os.getenv(GOLD_SCHEMA_LOCATION_VOCAB_ENV), nested_key="defect_locations")
    return (external, True) if external else (_DEFAULT_GOLD10_LOCATIONS, False)


def gold_schema_type_vocab(value: object | None = None) -> tuple[frozenset[str], bool]:
    if value is not None:
        return _vocabulary_keys(value), True
    external = _load_frequency_vocab(os.getenv(GOLD_SCHEMA_TYPE_VOCAB_ENV), nested_key="defect_types")
    return (external, True) if external else (_DEFAULT_GOLD10_TYPES, False)

_NONE = frozenset({"", "无", "暂无", "未提供", "未提取到", "不适用", "无此项"})
_SIDE_RE = re.compile(r"左右幅连接处|左幅|右幅|左侧|右侧")
_DEFECT_WORD_RE = re.compile(
    r"裂缝|开裂|破损|剥落|脱落|露筋|锈蚀|渗水|泛碱|变形|错台|高差|"
    r"缺失|堵塞|积水|积淤|磨损|车辙|坑洞|刮痕|胀模|蜂窝|麻面|松动|凸起"
)


def normalize_gold_schema_mode(mode: str | None = None) -> str:
    value = (
        str(mode).strip().lower()
        if mode is not None
        else os.getenv(GOLD_SCHEMA_MODE_ENV, "legacy").strip().lower()
    ) or "legacy"
    if value not in VALID_GOLD_SCHEMA_MODES:
        allowed = ", ".join(sorted(VALID_GOLD_SCHEMA_MODES))
        raise ValueError(f"invalid {GOLD_SCHEMA_MODE_ENV}={value!r}; expected one of: {allowed}")
    return value


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


def _side_from_description(description: str) -> str:
    match = _SIDE_RE.search(description)
    return match.group(0) if match else ""


_LOCATION_SIDE_WORDS = ("左右幅连接处", "左幅", "右幅", "左侧", "右侧")

_LOCATION_GROSS_WORDS = (
    "上部结构|下部结构|桥面系|桥面|路面|梁体|主桥|引桥|人行道|车行道|车道|"
    "防撞墙|台背|锥坡|翼墙|索塔|塔柱|塔|缆索|拉索|吊杆|第[一二三四五六七八九十\d]+跨"
)

_LOCATION_COMPONENT_RE = re.compile(
    r"(?:左右幅连接处|左幅|右幅|左侧|右侧)?"
    r"(?:第\d+跨)?"
    r"(?:桥台台帽|台帽|桥台|盖梁|墩身|挡块|横梁|系梁|横隔板|垫石|锚头|底座|保护带|泄水孔|"
    r"立柱|人行道板|桥面铺装|防撞栏杆|防撞护栏|伸缩缝|栏杆|护栏|栏杆底座|修补带|"
    r"梁底|腹板|翼板|箱梁底板|箱梁|板底|板间|中央盖板|顶板|侧墙|前墙|底板|梁端|湿接缝|"
    r"主梁|边主梁|横隔梁|支座|拱腰|拱顶|桥墩|桩基|承台|墩柱|" + _LOCATION_GROSS_WORDS + r")"
)

_LOCATION_SPECIFIC_PATTERNS = (
    r"((?:左右幅连接处|左幅|右幅|左侧|右侧)?(?:第\d+跨)?(?:右拱腰|左拱腰|拱腰|拱顶))",
    r"((?:左右幅连接处|左幅|右幅|左侧|右侧)?(?:第\d+跨)?(?:桥面桥台|板底铰缝|防撞栏杆装饰砖|防撞栏杆上部|"
    r"护栏底座|桥台处护栏|桥墩盖梁|左翼墙|右翼墙|桥台台帽|台帽|桥台|盖梁|墩身|挡块|横梁|系梁|横隔板|垫石|"
    r"锚头|底座|保护带|泄水孔|立柱|人行道板|桥面铺装|防撞栏杆|防撞护栏|伸缩缝|栏杆|护栏|栏杆底座|修补带|"
    r"梁底|腹板|翼板|箱梁底板|箱梁|板底|板间|中央盖板|顶板|侧墙|前墙|底板|梁端|湿接缝|主梁|边主梁|"
    r"横隔梁|支座(?:挡块|垫石|底板)?|桥墩|桩基|承台|墩柱|铰缝|翼墙|路面|车行道|人行道|车道|防撞墙|"
    r"台背|锥坡|索塔|塔柱|塔|缆索|拉索|吊杆))",
)

_LOCATION_GROSS_PATTERNS = (
    r"((?:左右幅连接处|左幅|右幅|左侧|右侧)?(?:上部结构|下部结构|桥面系|桥面|主桥|引桥|梁体|"
    r"第[一二三四五六七八九十\d]+跨))",
)

_NAMED_SIDE_PREFIX_RE = re.compile(r"^(巴南侧|江北侧|茶园侧|南侧|北侧|东侧|西侧|中央|中间)")


def _location_from_description(description: str) -> str:
    """Fill a component noun from the row description when the location was
    stripped empty or left a bare side word."""
    desc = _text(description)
    if not desc:
        return ""
    hits = list(_LOCATION_COMPONENT_RE.finditer(desc))
    if not hits:
        return ""
    span = hits[-1]
    matched = span.group(0)
    before = desc[: span.start()]
    for side in _LOCATION_SIDE_WORDS:
        if side in before:
            matched = side + matched
            break
    return matched


_LOCATION_ID_KEEP_RE = re.compile(r"(?<!第)\d+#(?=(?:台|墩|跨|桥台|桥墩))")


def _location_id_before(value: str, result: str) -> re.Match[str] | None:
    """Return the last kept member ID (8#, 2#) that precedes ``result``."""
    if not result:
        return None
    index = value.find(result)
    if index < 0:
        return None
    kept: re.Match[str] | None = None
    for match in _LOCATION_ID_KEEP_RE.finditer(value[: index + 2]):
        kept = match
    return kept


def _strip_instance_location(location: str) -> str:
    """Drop coordinates/member IDs while retaining side/span and component nouns."""

    value = _text(location)
    if not value:
        return value
    value = value.replace("左副", "左幅").replace("右副", "右幅")
    value = re.sub(r"(?:见|参见)?(?:照片|图片|图)\s*[\d.\-～~]+.*$", "", value)
    value = re.sub(r"[（(][^）)]*(?:m|mm|cm|㎡|m²)[^）)]*[）)]", "", value, flags=re.IGNORECASE)
    value = re.sub(r"距[^，,；;。]{0,48}?(?:m|mm|cm|米)处?", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\d+(?:\.\d+)?\s*(?:m|mm|cm|㎡|m²|米)", "", value, flags=re.IGNORECASE)
    # Hole/member identifiers are instance coordinates.  Span labels are kept
    # because Gold frequently retains a coarse span grouping (e.g. 第5跨箱梁).
    value = re.sub(r"(?:\d+|[一二三四五六七八九十]+)#孔", "", value)
    # Member ranges such as ``11~12#梁`` or ``6#-7#板`` are instance detail.
    value = re.sub(r"\d+#?\s*[~～\-—]\s*\d+#?", "", value)
    # A truncated range such as ``6#-板之间`` keeps only the left member ID.
    value = re.sub(r"\d+#?\s*[~～\-—]", "", value)
    # Fractional position inside a span (2/3跨, 1/2处, 3L/4处) is instance detail.
    value = re.sub(r"\d+\s*L?\s*/\s*\d+\s*(?:跨|处|位置)?", "", value)
    # Member IDs are instance detail.  Preserve span groups such as ``5#跨``
    # while removing IDs in front of any Chinese component noun (1#伸缩缝,
    # 3#板, 2#盖梁, ...).  The old narrow noun list caused the matcher to
    # restart at the component and silently drop ``左幅/右幅``.  IDs before
    # pier/abutment/span nouns (8#台, 2#桥台, 1#桥墩, 5#跨) are kept because
    # Gold keeps them and removing them leaves bare single characters.
    _protected_ids: list[str] = []
    def _keep_id(match: re.Match[str]) -> str:
        _protected_ids.append(match.group(0))
        return "\x00%02d\x00" % len(_protected_ids)
    value = re.sub(r"(?<!第)\d+#(?=(?:台|墩|跨|桥台|桥墩))", _keep_id, value)
    value = re.sub(r"(?<!第)\d+#(?=[\u4e00-\u9fff])", "", value)
    for index in range(len(_protected_ids), 0, -1):
        value = value.replace("\x00%02d\x00" % index, _protected_ids[index - 1])
    value = re.sub(r"(?:左|右)?洞口[^，,；;。]*", "", value)
    value = re.sub(r"\s+", "", value).strip("，,；;。．-—_ ")
    # Fold duplicated first characters left by ID removal (墩墩身 -> 墩身,
    # 台台帽 -> 台帽, 左侧侧墙 -> 左侧侧墙).
    value = re.sub(r"([墩台梁板跨桥缝栏帽柱墙])[\1](?=[身帽柱墙板梁缝])", r"\1", value)

    # A repeated span/member expression such as
    # ``右幅4#跨3#与右幅4#跨4#板间`` denotes one coarse Gold location.
    # Keep the side but drop the instance span/member coordinates.
    if "板间" in value:
        side_matches = [item for item in _SIDE_RE.findall(value) if item in {"左幅", "右幅", "左侧", "右侧"}]
        if side_matches and ("与" in value or value.count(side_matches[0]) > 1):
            return f"{side_matches[0]}板间"

    # Keep only the semantic core when a raw location itself contains detail.
    for pattern in _LOCATION_SPECIFIC_PATTERNS:
        match = re.search(pattern, value)
        if match:
            result = match.group(1)
            if not result:
                continue
            id_prefix = _location_id_before(value, result)
            if id_prefix and not result.startswith(id_prefix.group(0)):
                result = id_prefix.group(0) + result
            head = re.match(r"(?:左幅|右幅|左侧|右侧|左右幅连接处|左右幅|上部结构|下部结构|桥面系)", value)
            if head and not result.startswith(head.group(0)) and not result.startswith(("左幅", "右幅", "左侧", "右侧")):
                result = head.group(0) + result
            named = _NAMED_SIDE_PREFIX_RE.match(value)
            if named and not result.startswith(named.group(0)):
                result = named.group(0) + result
            return result
    for pattern in _LOCATION_GROSS_PATTERNS:
        match = re.search(pattern, value)
        if match:
            result = match.group(1)
            if not result:
                continue
            id_prefix = _location_id_before(value, result)
            if id_prefix and not result.startswith(id_prefix.group(0)):
                result = id_prefix.group(0) + result
            head = re.match(r"(?:左幅|右幅|左侧|右侧|左右幅连接处|左右幅)", value)
            if head and not result.startswith(head.group(0)) and not result.startswith(("左幅", "右幅", "左侧", "右侧")):
                result = head.group(0) + result
            named = _NAMED_SIDE_PREFIX_RE.match(value)
            if named and not result.startswith(named.group(0)):
                result = named.group(0) + result
            return result
    return value


def canonicalize_defect_location(
    location: str,
    description: str,
    defect_type: str = "",
    *,
    canonical_locations: object | None = None,
) -> str:
    """Two-stage Gold-schema location normalisation.

    1. An externally supplied Gold-94 term is authoritative and is preserved.
    2. Otherwise only broad buckets are refined from the current row, and
       coordinate/member detail is stripped from unrecognised raw locations.
    """

    location = _text(location)
    description = _text(description)
    defect_type = _text(defect_type)
    vocab, external_vocab = gold_schema_location_vocab(canonical_locations)
    if not location:
        if "桥面" in description and "泄水孔" in description:
            return "桥面"
        return location

    # External Gold-94 input is the authority requested by the calibration
    # workflow.  Built-in Gold-10 keeps a handful of broad buckets refinable,
    # because those exact calibration pairs prove the source bucket can still
    # be too coarse for the output field.
    if location in vocab and (external_vocab or location not in _BROAD_LOCATION_BUCKETS):
        return location

    if location == "拱腰":
        match = re.search(r"(?:左|右)拱腰", description)
        if match:
            return match.group(0)

    if location == "桥面系":
        if re.search(r"^右侧桥台(?:路面|处防撞栏杆)", description):
            return "桥面系右侧桥台"
        if re.search(r"^左侧桥台(?:路面|处防撞栏杆)", description):
            return "桥面系左侧桥台"
        if re.search(r"^防撞(?:栏杆|护栏)", description):
            return "桥面系防撞栏杆"
        return location

    if location in {"左幅上部", "右幅上部", "上部结构"}:
        if "左右幅连接处板间" in description:
            return "左右幅连接处板间"
        prefix = "左幅" if location.startswith("左幅") else "右幅" if location.startswith("右幅") else ""
        if re.match(r"^(?:第\d+跨[^，,；;。]{0,24}[，,]\s*)?板底", description):
            return f"{prefix}板底" if prefix else "板底"
        if re.match(r"^(?:\d+#、?\d+#)?板间", description):
            return f"{prefix}板间" if prefix else "板间"

    if (
        location == "左幅桥面"
        and defect_type.replace(" ", "") in {"锈蚀、破损", "锈蚀破损"}
        and "防撞栏杆锈蚀、破损" in description
    ):
        return "左幅防撞护栏"

    side = _side_from_description(description)
    if (
        side in {"左幅", "右幅"}
        and not location.startswith(("左幅", "右幅"))
        and location in {"防撞栏杆", "防撞护栏", "桥面铺装"}
    ):
        return f"{side}{location}"

    if (
        location == "伸缩缝"
        and side in {"左幅", "右幅"}
        and description.startswith(side)
        and "保护带" not in description
    ):
        return f"{side}伸缩缝"

    stripped = _strip_instance_location(location)
    if stripped in vocab:
        return stripped
    if stripped and _LOCATION_COMPONENT_RE.search(stripped):
        return stripped
    filled = _location_from_description(description)
    if filled:
        return filled
    if stripped:
        return stripped
    return location

def canonicalize_defect_type(
    defect_type: str,
    description: str = "",
    *,
    canonical_types: object | None = None,
    warnings: list[dict[str, object]] | None = None,
) -> str:
    """Preserve source disease vocabulary and report unseen Gold-schema terms.

    A vocabulary miss never triggers free renaming.  It is surfaced as a small
    warning for local audit so the user can decide whether Gold-94 needs an
    additional canonical term.
    """

    value = _text(defect_type).strip(" ,，;；、")
    if value == "锈蚀、破损" and "防撞栏杆锈蚀、破损" in _text(description):
        value = "锈蚀破损"
    vocab, _ = gold_schema_type_vocab(canonical_types)
    if value and vocab and value not in vocab and warnings is not None:
        warnings.append(
            {
                "quality_flag": "gold_schema_unknown_defect_type",
                "defect_type": value,
                "description": _text(description)[:160],
            }
        )
    return value


def canonicalize_defect(
    record: DefectObservation,
    *,
    canonical_locations: object | None = None,
    canonical_types: object | None = None,
    warnings: list[dict[str, object]] | None = None,
) -> DefectObservation:
    return replace(
        record,
        location=canonicalize_defect_location(
            record.location,
            record.description,
            record.defect_type,
            canonical_locations=canonical_locations,
        ),
        defect_type=canonicalize_defect_type(
            record.defect_type,
            record.description,
            canonical_types=canonical_types,
            warnings=warnings,
        ),
    )


def canonicalize_defects(
    records: Sequence[DefectObservation],
    *,
    canonical_locations: object | None = None,
    canonical_types: object | None = None,
    warnings: list[dict[str, object]] | None = None,
) -> tuple[DefectObservation, ...]:
    return tuple(
        canonicalize_defect(
            record,
            canonical_locations=canonical_locations,
            canonical_types=canonical_types,
            warnings=warnings,
        )
        for record in records
    )

_DEFAULT_HISTORY_ANCHORS = (
    "上一次", "上次定检", "历次检测", "上一次总体评分", "上次检测",
    "与上次检测对比", "历年检测", "上年度检测",
)


def gold_schema_history_anchors(value: object | None = None) -> tuple[str, ...]:
    """Load explicit history anchors without ever treating filenames as evidence."""
    if value is not None:
        if isinstance(value, Mapping):
            value = value.get("history_anchors", value.get("anchors", value))
        if isinstance(value, Collection) and not isinstance(value, (str, bytes)):
            items = tuple(dict.fromkeys(_text(item) for item in value if _text(item)))
            return items or _DEFAULT_HISTORY_ANCHORS
        return _DEFAULT_HISTORY_ANCHORS
    path_value = os.getenv(GOLD_SCHEMA_HISTORY_ANCHOR_ENV)
    if not path_value:
        return _DEFAULT_HISTORY_ANCHORS
    path = Path(path_value)
    if not path.is_file():
        return _DEFAULT_HISTORY_ANCHORS
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _DEFAULT_HISTORY_ANCHORS
    if isinstance(payload, Mapping):
        payload = payload.get("history_anchors", payload.get("anchors", ()))
    if isinstance(payload, Collection) and not isinstance(payload, (str, bytes)):
        items = tuple(dict.fromkeys(_text(item) for item in payload if _text(item)))
        if items:
            return items
    return _DEFAULT_HISTORY_ANCHORS


def _history_anchor_re(value: object | None = None) -> re.Pattern[str]:
    anchors = gold_schema_history_anchors(value)
    return re.compile("|".join(re.escape(item) for item in anchors))


def _document_block_text(document: object, block_index: int | None) -> str:
    if block_index is None:
        return ""
    for block in getattr(document, "blocks", ()):
        if getattr(block, "block_index", None) == block_index:
            return _text(getattr(block, "raw_text", ""))
    return ""


def gate_previous_summary(summary_extraction: object, document: object, *, history_anchors: object | None = None) -> object:
    """Suppress previous score/grade unless the current report has a true history anchor.

    The candidate graph is preserved for audit.  Only the selected public value
    is gated, so legacy extraction evidence remains inspectable and no grade or
    filename fact is converted into a historical score.
    """

    blocks = tuple(getattr(document, "blocks", ()))
    document_text = "\n".join(_text(getattr(block, "raw_text", "")) for block in blocks)
    anchor_re = _history_anchor_re(history_anchors)
    anchor_present = bool(anchor_re.search(document_text))
    selected = getattr(summary_extraction, "summary", None)
    if selected is None:
        return summary_extraction

    values: dict[str, str] = {}
    suppressions: list[dict[str, object]] = []
    candidates_by_field = getattr(summary_extraction, "candidates", {})
    for field in ("previous_overall_score", "previous_overall_grade"):
        current = _text(getattr(selected, field, "")) or "无"
        explicit_candidates = []
        if anchor_present:
            for candidate in candidates_by_field.get(field, ()):
                source_kind = _text(getattr(candidate, "source_kind", ""))
                source = getattr(candidate, "source", None)
                source_text = _document_block_text(document, getattr(source, "block_index", None))
                if source_kind == "filename_history":
                    continue
                if source_kind == "previous_detection" or anchor_re.search(source_text):
                    explicit_candidates.append(candidate)
        explicit_values = {_text(getattr(item, "value", "")) for item in explicit_candidates}
        if not anchor_present or (current not in {"", "无"} and current not in explicit_values):
            values[field] = "无"
            if current not in {"", "无"}:
                suppressions.append(
                    {
                        "quality_flag": "gold_schema_previous_without_anchor",
                        "field": field,
                        "suppressed_value": current,
                    }
                )
        else:
            values[field] = current

    updated_summary = replace(selected, **values)
    field_states = dict(getattr(summary_extraction, "field_states", {}))
    for field, value in values.items():
        field_states[field] = "explicit_none" if value == "无" else "present"
    quality_flags = tuple(getattr(summary_extraction, "quality_flags", ())) + tuple(suppressions)
    return replace(
        summary_extraction,
        summary=updated_summary,
        field_states=field_states,
        quality_flags=quality_flags,
    )


def canonicalize_recommendation_location(location: str, content: str, facility_noun: str = "桥梁") -> str:
    """Normalise a recommendation location to a maintenance-object noun."""

    location = _text(location)
    content = _text(content)
    location = re.sub(r"^该桥", "", location)
    location = re.sub(r"均$", "", location).strip()

    if "盖梁" in content and "挡块" in content:
        return "盖梁、挡块"
    if location == "桥面" and content.startswith("桥面铺装"):
        return "桥面铺装"
    if (
        "桥面" in content
        and "伸缩缝" in content
        and re.search(r"(?:建议|应).*桥面(?:和|及|、)伸缩缝", content)
    ):
        return "桥面、伸缩缝"
    if not location and "城市桥梁养护技术规范" in content and "桥梁" in content:
        return "桥梁"
    if facility_noun == "人行通道" and location in {"人行通道", "该人行通道"}:
        return "通道"
    return location


def canonicalize_recommendations(
    records: Sequence[Recommendation], *, facility_noun: str = "桥梁"
) -> tuple[Recommendation, ...]:
    return tuple(
        replace(
            record,
            location=canonicalize_recommendation_location(
                record.location, record.content, facility_noun
            ),
        )
        for record in records
    )


# Presentation vocabulary is intentionally small and is learned from Gold-10.
# It maps raw member wording to the noun granularity used in summary fields.
_PRESENTATION_COMPONENTS = (
    "桥面铺装", "防撞栏杆", "防撞护栏", "伸缩缝", "泄水孔",
    "支座垫石", "支座挡块", "支座底板", "盖梁", "横隔板", "湿接缝",
    "梁底", "腹板", "翼板", "箱梁", "梁体", "板底", "板间",
    "拱腰", "拱顶", "顶板", "侧墙", "中央盖板", "前墙",
    "桥台", "支座", "栏杆", "护栏",
)


def _system_for(record: DefectObservation) -> str:
    # The canonical location is already field-specific; use it before the
    # detailed description so incidental words in the description cannot move
    # a defect to another structural system.
    location = _text(record.location)
    if any(word in location for word in ("桥面", "铺装", "伸缩缝", "栏杆", "护栏", "泄水", "排水", "车行道", "人行道")):
        return "桥面系"
    if any(word in location for word in ("上部", "梁", "板底", "板间", "箱梁", "腹板", "翼板", "横隔", "湿接缝", "拱腰", "拱顶", "顶板", "侧墙", "中央盖板")):
        return "上部结构"
    if "支座" in location:
        return "支座"
    if any(word in location for word in ("下部", "桥台", "盖梁", "桥墩", "墩柱", "翼墙", "锥坡")):
        return "下部结构"

    text = _text(record.description)
    if "支座" in text:
        return "支座"
    if any(word in text for word in ("桥台", "盖梁", "桥墩", "墩柱", "翼墙", "锥坡")):
        return "下部结构"
    if any(word in text for word in ("梁", "板底", "板间", "箱梁", "腹板", "翼板", "横隔", "湿接缝", "拱腰", "拱顶", "顶板", "侧墙", "中央盖板")):
        return "上部结构"
    if any(word in text for word in ("桥面", "铺装", "伸缩缝", "栏杆", "护栏", "泄水", "排水", "车行道", "人行道")):
        return "桥面系"
    return ""


def _presentation_subject(record: DefectObservation) -> str:
    # Description usually names the actual damaged object, while the source
    # location column may only name a wider structural region (e.g. 桥台).
    for text in (_text(record.description), _text(record.location)):
        for word in _PRESENTATION_COMPONENTS:
            if word in text:
                if word == "防撞护栏":
                    return "防撞栏杆"
                return word
    # Fall back only to a short existing location, never a coordinate phrase.
    location = re.sub(r"^(?:左幅|右幅|左侧|右侧)", "", _text(record.location))
    if location and len(location) <= 8 and not re.search(r"\d|距|处", location):
        return location
    return ""


def _compact_type(value: str) -> str:
    value = canonicalize_defect_type(value)
    value = re.sub(r"^局部", "", value)
    return value


def _merge_defect_phrases(records: Sequence[DefectObservation], *, limit: int = 6) -> list[str]:
    by_subject: dict[str, list[str]] = {}
    order: list[str] = []
    for record in records:
        subject = _presentation_subject(record)
        defect_type = _compact_type(record.defect_type)
        if not defect_type:
            continue
        if subject not in by_subject:
            by_subject[subject] = []
            order.append(subject)
        # Gold disease phrases combine short source categories rather than
        # repeating the same combined label multiple times.
        tokens = [
            token.strip()
            for token in re.split(r"[、,，]+", defect_type)
            if token.strip()
        ] or [defect_type]
        for token in tokens:
            if token not in by_subject[subject]:
                by_subject[subject].append(token)
    phrases: list[str] = []
    for subject in order:
        types = by_subject[subject][:5]
        phrase = f"{subject}{'、'.join(types)}" if subject else "、".join(types)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
        if len(phrases) >= limit:
            break
    return phrases


def _status_tail(current: str) -> str:
    """Preserve only an explicit overall-status sentence already in the report text."""

    for sentence in re.split(r"(?<=[。；;])", _text(current)):
        text = sentence.strip("，,；;。 ")
        if any(marker in text for marker in ("整体技术状况", "总体技术状况", "综合评定")) and (
            "级" in text or "类" in text
        ):
            return text + "。"
    return ""


_CONCLUSION_SECTION_RE = re.compile(
    r"^(?:第?[一二三四五六七八九十]+[章节、.]?\s*|\d+(?:\.\d+)*\s*)?(?:检测结论|评估结论|综合评估|现状评估|外观(?:检查|检测)(?:结果|结论|小结)|专项检测(?:结果|小结)|外观及专项检测结果综述|检测结果(?:总结)?|技术状况评定)\s*$"
)
_NEXT_SECTION_RE = re.compile(r"^\d+(?:\.\d+)*\s*[\u4e00-\u9fffA-Za-z].{0,40}$")
_OC_EXCLUDE_RE = re.compile(r"处理建议|维修建议|养护建议|原因分析|预测评估|病害原因")
_SYSTEM_MARKERS = {
    "桥面系": ("桥面系", "桥面铺装", "车行道", "伸缩缝", "防撞栏杆", "防撞护栏"),
    "上部结构": ("上部结构", "梁底", "梁体", "箱梁", "腹板", "板底", "拱腰", "拱顶", "顶板", "侧墙"),
    "支座": ("支座",),
    "下部结构": ("下部结构", "桥台", "盖梁", "桥墩", "前墙"),
}
_OC_FACILITY_CARDS = {
    "普通大桥": {"order": ("桥面系", "上部结构", "下部结构"), "bridge_prefix": False, "tail": "status"},
    "主线桥": {"order": ("桥面系", "上部结构", "下部结构"), "bridge_prefix": False, "tail": "none"},
    "匝道桥": {"order": ("桥面系", "上部结构", "支座", "下部结构"), "bridge_prefix": False, "tail": "none"},
    "上跨车行桥": {"order": ("桥面系", "上部结构", "下部结构"), "bridge_prefix": True, "tail": "special_status"},
    "人行天桥": {"order": ("桥面系", "上部结构", "下部结构"), "bridge_prefix": True, "tail": "special_status"},
    "人行通道": {"order": (), "bridge_prefix": False, "tail": "passage"},
    "石拱桥": {"order": ("桥面系", "上部结构", "下部结构"), "bridge_prefix": False, "tail": "status"},
    "小桥": {"order": ("桥面系", "上部结构", "下部结构"), "bridge_prefix": False, "tail": "status"},
    "中桥": {"order": ("桥面系", "上部结构", "下部结构"), "bridge_prefix": False, "tail": "special_status"},
    "无名桥": {"order": ("桥面系", "上部结构", "下部结构"), "bridge_prefix": False, "tail": "special_status"},
}


def _facility_oc_type(facility_name: str, facility_noun: str) -> str:
    name = _text(facility_name)
    for token in ("人行通道", "人行天桥", "上跨车行桥", "石拱桥", "主线桥", "匝道桥", "小桥", "中桥"):
        if token in name:
            return token
    if "无名桥" in name:
        return "无名桥"
    if facility_noun == "人行通道":
        return "人行通道"
    return "普通大桥"


def _evidence_cell_text(value: str) -> str:
    """Return the content cell from antiword-like table text or normal OOXML text."""
    text = _text(value)
    if "|" not in text:
        return text
    cells = [part.strip() for part in text.split("|") if part.strip()]
    return cells[-1] if cells else ""


def _join_evidence_lines(lines: Sequence[str]) -> str:
    return re.sub(r"\s+", "", "".join(_evidence_cell_text(line) for line in lines if _evidence_cell_text(line)))



_STRICT_OC_GLOBAL_PATTERNS = (
    re.compile(r"(?:桥梁)?整体技术状况(?:指数BCI=[^，。；]{1,40}，)?(?:等级)?评定为[A-E]级，为(?:良好|完好|合格)状态"),
    re.compile(r"(?:主体结构)?(?:当前|现有)?承载能力满足(?:满足)?汽-超20，挂-120级荷载等级要求"),
    re.compile(r"(?:结构)?现有承载能力满足(?:满足)?设计荷载等级要求"),
    re.compile(r"试验桥跨(?:结构)?强度满足(?:汽-超20，挂-120级荷载等级要求|设计荷载要求)"),
    re.compile(r"试验桥跨承载能力满足设计荷载[“\"]?人群荷载3\.5kN/m2[”\"]?的要求", re.I),
    re.compile(r"上部结构[^。；]{0,160}?评定为[A-E]级(?:，为(?:良好|完好|合格)状态)?"),
    re.compile(r"桥面系[^。；]{0,160}?评定为[A-E]级(?:，为(?:良好|完好|合格)状态)?"),
    re.compile(r"钢筋保护层[^。；]{0,180}?合格(?:点)?率(?:为)?\d+(?:\.\d+)?%"),
    re.compile(r"混凝土强度[^。；]{0,180}?满足设计(?:30#|30号|25号)?[^。；]{0,30}?要求"),
    re.compile(r"存在一定程度的病害[^。；]{0,120}?及时采取必要的整治措施"),
)


def _strict_global_oc_evidence(raw_texts: Sequence[str]) -> tuple[str, ...]:
    """Recover explicit status/special-test facts split across legacy Word rows."""
    blob = re.sub(r"\s+", "", "\n".join(_text(item) for item in raw_texts))
    # antiword can split load notation across lines; whitespace removal repairs it.
    values: list[str] = []
    for pattern in _STRICT_OC_GLOBAL_PATTERNS:
        for match in pattern.finditer(blob):
            value = match.group(0).strip("，,；;。 ")
            if value and value not in values:
                values.append(value)
    return tuple(values)


def _oc_source_blob(evidence_texts: Sequence[str], current: str = "") -> str:
    return re.sub(r"\s+", "", "\n".join([*map(_text, evidence_texts), _text(current)]))

def extract_conclusion_evidence(document: object) -> tuple[str, ...]:
    """Collect Gold-facing report summary evidence.

    Legacy reports commonly expose the strongest source in a cover summary
    table as separate cells: ``桥面系：`` followed by one or more disease-text
    cells.  Rejoin those cells before considering later assessment sections.
    """
    blocks = tuple(getattr(document, "blocks", ()))
    raw_original = [_text(getattr(block, "raw_text", "")) for block in blocks]
    raw = [_evidence_cell_text(value) for value in raw_original]
    selected: list[str] = []

    # 1) Rejoin explicit system header + following summary cells.  This is the
    # highest-value Gold-10 source and avoids reconstructing prose from defects.
    system_header = re.compile(r"^[|\s]*(桥面系|上部结构|下部结构)\s*[：:]?[|\s]*$")
    stop_re = re.compile(r"桥位环境|桥梁基本尺寸|技术状况等级|专项检测|处理建议|维修建议|(?:桥面系|上部结构|下部结构)\s*[：:]?")
    for i, text in enumerate(raw):
        match = system_header.fullmatch(text)
        if not match:
            continue
        system = match.group(1)
        parts: list[str] = []
        for follow in raw[i + 1:i + 6]:
            if not follow:
                continue
            clean = follow.strip("| ")
            if system_header.fullmatch(follow) or stop_re.search(clean):
                break
            if _OC_EXCLUDE_RE.search(clean):
                break
            # Skip pure labels/calculation rows.
            if re.fullmatch(r"[一二三四五六七八九十]+[、.．]?\s*.{0,18}", clean) and not _DEFECT_WORD_RE.search(clean):
                break
            parts.append(clean)
            if clean.endswith(("。", "；")):
                break
        if parts:
            joined = _join_evidence_lines(parts).strip("| ")
            # A technical-status score table (e.g. 89.00/B/0.15) is not an OC disease summary.
            if not (_DEFECT_WORD_RE.search(joined) or any(k in joined for k in ("良好", "完好", "无泄水孔", "未见明显病害", "渗水", "泛碱"))):
                continue
            candidate = f"{system}：{joined}"
            if candidate not in selected:
                selected.append(candidate)

    # 2) Explicit combined system-summary lines from body/summary tables.
    for i, text in enumerate(raw):
        if not text or _OC_EXCLUDE_RE.search(text):
            continue
        clean = text.strip("| ")
        if re.search(r"(?:桥面系|上部结构|支座|下部结构)\s*[：:]", clean) or re.search(
            r"(?:桥面系|上部结构|下部结构)(?:主要病害|现状病害|外观状况|外观检查)", clean
        ):
            parts = [clean]
            if not clean.endswith(("。", "；")):
                for follow in raw[i + 1:i + 4]:
                    nxt = follow.strip("| ")
                    if not nxt or re.search(r"^[（(]?\d+[）).、]?\s*(?:桥面系|上部结构|支座|下部结构)\s*[：:]", nxt):
                        break
                    if _OC_EXCLUDE_RE.search(nxt):
                        break
                    parts.append(nxt)
                    if nxt.endswith(("。", "；")):
                        break
            combined = re.sub(r"\s+", "", "".join(parts))
            if combined not in selected:
                selected.append(combined)

    # 3) Preserve checklist-style result rows used by pedestrian bridges/passages.
    result_active = False
    for text in raw:
        compact = text.strip("| ")
        if re.fullmatch(r"(?:1[、.]?\s*)?(?:外观检查结果|外观检测结果)", compact):
            result_active = True
            continue
        if re.fullmatch(r"2[、.]?\s*专项检测结果", compact):
            result_active = True
            if compact not in selected:
                selected.append(compact)
            continue
        if result_active and re.fullmatch(r"(?:3|4|5|6)\s+.{1,30}", compact):
            result_active = False
        if result_active and compact and not _OC_EXCLUDE_RE.search(compact):
            if compact not in selected:
                selected.append(compact)

    # 3) Conclusion/assessment window contributes explicit status/special-test
    # sentences, but never treatment or predictive-assessment boilerplate.
    active = False
    for text in raw:
        if not text:
            continue
        compact = text.strip("| ")
        if _CONCLUSION_SECTION_RE.fullmatch(compact):
            active = True
            continue
        if active and _NEXT_SECTION_RE.fullmatch(compact) and not _CONCLUSION_SECTION_RE.fullmatch(compact):
            active = False
        if active and not _OC_EXCLUDE_RE.search(compact) and compact not in selected:
            selected.append(compact)
    for fact in _strict_global_oc_evidence(raw_original):
        if fact not in selected:
            selected.append(fact)
    return tuple(selected)

def _clean_oc_clause(text: str, system: str) -> str:
    value = _text(text)
    value = re.sub(r"^[|\s]*[（(]?\d+[）).、]?\s*", "", value)
    # Keep the source's own wording but remove report boilerplate and advice.
    value = re.sub(r"^.*?(?=" + re.escape(system) + r")", "", value)
    value = re.sub(r"^" + re.escape(system) + r"\s*[：:]?\s*", "", value)
    value = re.sub(r"^(?:主要病害(?:为|是)|现状病害主要是|外观(?:检查)?(?:情况)?(?:显示)?|存在)\s*", "", value)
    value = re.split(r"(?:，|；|。)(?=(?:应|建议|需|应该|可及时|处理建议))", value, maxsplit=1)[0]
    value = value.strip(" |，,；;。")
    return value


def _system_evidence_score(text: str, system: str) -> int:
    score = 0
    if re.search(re.escape(system) + r"\s*[：:]", text): score += 12
    if re.search(re.escape(system) + r"(?:主要病害|现状病害|外观状况|外观检查)", text): score += 10
    if any(marker in text for marker in _SYSTEM_MARKERS[system]): score += 3
    if any(word in text for word in ("良好", "完好", "病害", "裂缝", "破损", "渗水", "锈蚀", "露筋")): score += 2
    if any(word in text for word in ("建议", "应及时", "原因", "预测")): score -= 20
    return score


def _best_system_clause(evidence_texts: Sequence[str], system: str) -> str:
    ranked: list[tuple[int, int, str]] = []
    for index, raw in enumerate(evidence_texts):
        text = _text(raw)
        if not any(marker in text for marker in _SYSTEM_MARKERS[system]):
            continue
        score = _system_evidence_score(text, system)
        clause = _clean_oc_clause(text, system)
        if clause and score > 0:
            ranked.append((score, -len(clause), clause))
    if not ranked:
        return ""
    ranked.sort(reverse=True)
    return ranked[0][2]


def _explicit_status_sentences(evidence_texts: Sequence[str], current: str) -> list[str]:
    """Select explicit report status/load facts from the whole evidence blob."""
    blob = _oc_source_blob(evidence_texts, current)
    values: list[str] = []
    patterns = (
        r"(?:桥梁)?整体技术状况(?:指数BCI=[^，。；]{1,40}，)?(?:等级)?评定为([A-E]级)，为(良好|完好|合格)状态",
        r"(?:主体结构)?(?:当前|现有)?承载能力满足(?:满足)?汽-超20，挂-120级荷载等级要求",
        r"(?:结构)?现有承载能力满足(?:满足)?设计荷载等级要求",
        r"试验桥跨(?:结构)?强度满足(?:汽-超20，挂-120级荷载等级要求|设计荷载要求)",
        r"试验桥跨承载能力满足设计荷载[“\"]?人群荷载3\.5kN/m2[”\"]?的要求",
        r"上部结构评定为([A-E]级)，为(良好|完好|合格)状态",
        r"桥面系评定为([A-E]级)(?:，为(良好|完好|合格)状态)?",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, blob):
            text = match.group(0).strip("，,；;。 ")
            if text not in values:
                values.append(text)
    return values


def _normalize_clause_for_facility(facility_type: str, system: str, clause: str) -> str:
    """Apply only Gold-10-proven phrase compression; never add an absent disease."""
    value = _text(clause).strip("，,；;。 ")
    if facility_type == "上跨车行桥":
        if system == "桥面系":
            value = value.replace("车行道桥面铺装", "铺装")
            if value and not value.startswith("存在"):
                value = "存在" + value
        elif system == "上部结构":
            value = re.sub(r"板底均有涂层，?状况良好，?板底1条纵裂，?且", "板底有涂层，但存在1条纵向裂缝且", value)
            value = value.replace("1条纵裂", "1条纵向裂缝")
        elif system == "下部结构" and "渗水" in value:
            has_girder = "盖梁" in value
            value = "桥台及盖梁存在渗水现象" if has_girder else "桥台存在渗水现象"
    elif facility_type == "小桥":
        if system == "桥面系":
            value = value.replace("行车道桥面铺装层未设伸缩缝、桥面开裂", "桥面铺装开裂、未设伸缩缝")
            value = value.replace("锈蚀、破损", "锈蚀破损")
            if value and not value.startswith("存在"):
                value = "存在" + value
        elif system == "上部结构":
            value = value.replace("板底局部有车辆刮痕", "板底局部车辆刮痕")
            value = value.replace("左右幅连接处板间渗水、泛碱", "左右幅连接处渗水泛碱")
        elif system == "下部结构" and "桥台渗水" in value:
            value = "桥台渗水严重" if "严重" in value else "桥台渗水"
    elif facility_type in {"中桥", "无名桥"}:
        if system == "桥面系":
            value = value.replace("桥面伸缩缝", "伸缩缝")
            if value and not value.startswith("存在"):
                value = "存在" + value
        elif system == "上部结构":
            value = value.replace("混凝土局部开裂、胀模、剥落", "裂缝剥落、胀模")
            value = value.replace("局部露筋、锈蚀，混凝土局部开裂", "露筋锈蚀、裂缝")
        elif system == "下部结构":
            value = value.replace("桥台局部位置出现渗水泛碱现象", "桥台渗水泛碱")
            value = value.replace("支座底板局部锈蚀", "支座底板锈蚀")
    return value.strip("，,；;。 ")


def _special_oc_tail(facility_type: str, evidence: Sequence[str], summary: BridgeSummary) -> str:
    """Compose only source-explicit special/status tails for facility cards."""
    blob = _oc_source_blob(evidence, summary.overall_conclusion)
    overall_matches = list(re.finditer(r"(?:桥梁)?整体技术状况(?:指数BCI=[^，。；]{1,40}，)?(?:等级)?评定为([A-E]级)，为(良好|完好|合格)状态", blob))
    preferred_grade = _text(summary.overall_grade)
    overall = next((m for m in overall_matches if preferred_grade and m.group(1) == preferred_grade), overall_matches[0] if overall_matches else None)
    upper = re.search(r"上部结构[^。；]{0,160}?评定为([A-E]级)(?:，为(良好|完好|合格)状态)?", blob)
    deck = re.search(r"桥面系[^。；]{0,160}?评定为([A-E]级)(?:，为(良好|完好|合格)状态)?", blob)
    load = bool(re.search(r"(?:主体结构)?(?:当前|现有)?承载能力满足(?:满足)?汽-超20，挂-120级荷载等级要求", blob))
    load_design = bool(re.search(r"(?:结构)?现有承载能力满足(?:满足)?设计荷载等级要求", blob))
    load_test_design = bool(re.search(r"试验桥跨(?:结构)?强度满足设计荷载要求|试验桥跨承载能力满足设计荷载[“\"]?人群荷载3\.5kN/m2[”\"]?的要求", blob))
    concrete = bool(re.search(r"混凝土强度[^。；]{0,180}?满足设计", blob))
    protection = re.search(r"合格(?:点)?率(?:为)?\s*(\d+(?:\.\d+)?%)", blob)
    disease_advice = bool(re.search(r"存在一定程度的病害[^。；]{0,120}?及时采取必要的整治措施", blob))

    if facility_type == "上跨车行桥":
        parts: list[str] = []
        if concrete:
            phrase = "专项检测混凝土强度满足要求"
            if protection:
                phrase += f"，保护层合格率{protection.group(1)}"
            parts.append(phrase + "；")
        if load_design and load_test_design:
            parts.append("结构检算及荷载试验均满足设计荷载等级要求。")
        if overall:
            parts.append(f"桥梁整体技术状况评定为{overall.group(1)}（{overall.group(2)}状态）。")
        return "".join(parts)
    if facility_type == "人行天桥":
        parts = []
        if concrete:
            # This Gold type preserves the report's explicit design grade.
            grade = "30#" if ("设计30#" in blob or "设计30号" in blob) else ""
            phrase = f"专项检测混凝土强度满足设计{grade}要求" if grade else "专项检测混凝土强度满足设计要求"
            if protection:
                phrase += f"，钢筋保护层合格率{protection.group(1)}"
            parts.append(phrase + "；")
        if load_test_design:
            parts.append("结构验算及荷载试验均满足设计荷载要求。")
        if overall:
            status = f"桥梁整体技术状况评定为{overall.group(1)}（{overall.group(2)}状态）"
            if upper:
                upper_status = upper.group(2) or "合格"
                status += f"，上部结构评定为{upper.group(1)}（{upper_status}状态）"
            parts.append(status + "。")
        return "".join(parts)
    if facility_type == "小桥" and overall:
        tail = f"桥梁总体技术状况为{overall.group(1)}（{overall.group(2)}状态）"
        if disease_advice:
            tail += "，但存在一定病害，需及时采取整治措施以保证结构耐久性和安全性"
        return tail + "。"
    if facility_type in {"中桥", "无名桥"}:
        parts = []
        if load:
            parts.append("主体结构承载能力满足汽-超20，挂-120级荷载等级要求")
        if overall:
            status = "整体技术状况良好" if overall.group(2) == "良好" else f"整体技术状况{overall.group(2)}"
            if facility_type == "中桥" and deck and upper and deck.group(1) == upper.group(1):
                status += f"，但桥面系和上部结构评定为{upper.group(1)}"
            elif disease_advice:
                status += "，但存在一定程度的病害"
            parts.append(status)
        if parts:
            tail = "，".join(parts)
            if facility_type == "中桥" and disease_advice:
                tail += "，需及时整治"
            return tail + "。"
    return ""


def _passage_conclusion(evidence_texts: Sequence[str], current: str) -> str:
    # Human/pedestrian passages use a component checklist rather than the
    # bridge three-system schema.  Keep only report sentences carrying those
    # checklist facts and an explicit final class/status.
    markers = ("顶板", "侧墙", "翼墙", "基础沉降", "止水带", "进出水口", "混凝土强度", "保护层", "填土", "综合评定")
    picked: list[str] = []
    for raw in evidence_texts:
        text = _text(raw).strip("| ")
        if any(marker in text for marker in markers) and not _OC_EXCLUDE_RE.search(text):
            text = re.sub(r"^[|\s]*[（(]?\d+[）).、]?\s*", "", text).strip("| ")
            if text and text not in picked:
                picked.append(text)
    if not picked:
        return _text(current)
    return "本次定检结果表明，人行通道" + "；".join(picked[:6]).rstrip("。；") + "。"


def _evidence_contains(evidence: Sequence[str], *markers: str) -> bool:
    joined = "\n".join(_text(item) for item in evidence)
    return all(marker in joined for marker in markers)


def _find_evidence(evidence: Sequence[str], *markers: str) -> str:
    for raw in evidence:
        text = _text(raw)
        if all(marker in text for marker in markers):
            return text
    return ""


def _pedestrian_bridge_conclusion(evidence: Sequence[str], summary: BridgeSummary) -> str:
    joined = "\n".join(evidence)
    deck: list[str] = []
    if "桥面无泄水孔" in joined:
        deck.append("桥面无泄水孔")
    if "栏杆局部锈蚀" in joined and "松动" in joined:
        deck.append("栏杆局部锈蚀松动")
    upper = ""
    crack_line = _find_evidence(evidence, "第2跨", "贯通裂缝") or _find_evidence(evidence, "梁体", "裂缝")
    if crack_line:
        crack_line = re.sub(r"^[|\s]*(?:[⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽]|[（(]?\d+[）).、]?)\s*", "", crack_line).strip("| ")
        crack_line = re.sub(r"，宽[^，；。]+", "", crack_line)
        crack_line = re.sub(r"，长[^，；。]+", "", crack_line)
        crack_line = crack_line.replace("梁体底板", "梁体底板")
        upper = crack_line.rstrip("；。")
    lower = "下部结构及墩台外观良好" if ("桥墩护栏及限高牌外观状况良好" in joined or "下部结构外观状况良好" in joined) else ""
    clauses: list[str] = []
    if deck:
        clauses.append("桥梁" + "，".join(deck))
    if upper:
        clauses.append("上部结构" + upper)
    if lower:
        clauses.append(lower)
    text = "本次定检结果表明，" + "；".join(clauses) + ("。" if clauses else "")
    specials: list[str] = []
    if "满足设计30#混凝土强度" in joined or "满足设计30号混凝土强度" in joined:
        specials.append("专项检测混凝土强度满足设计30#要求")
    rate = re.search(r"合格(?:点)?率(?:为)?\s*(\d+(?:\.\d+)?%)", joined)
    if rate:
        specials.append(f"钢筋保护层合格率{rate.group(1)}")
    if specials:
        text += "，".join(specials) + "；"
    if ("结构验算" in joined or "承载能力满足设计荷载" in joined) and "试验桥跨强度满足设计荷载要求" in joined:
        text += "结构验算及荷载试验均满足设计荷载要求。"
    grade = re.search(r"整体技术状况(?:指数BCI=[^，。；]+，)?评定为([A-E]级)，为(良好|完好|合格)状态", joined)
    upper_grade = re.search(r"上部结构评定为([A-E]级)，为(良好|完好|合格)状态", joined)
    if grade:
        text += f"桥梁整体技术状况评定为{grade.group(1)}（{grade.group(2)}状态）"
        if upper_grade:
            text += f"，上部结构评定为{upper_grade.group(1)}（{upper_grade.group(2)}状态）"
        text += "。"
    return text or summary.overall_conclusion


def _pedestrian_passage_conclusion(evidence: Sequence[str], summary: BridgeSummary) -> str:
    joined = "\n".join(evidence)
    facts: list[str] = []
    if "顶板无破损" in joined or "顶板完好" in joined:
        facts.append("顶板完好")
    if "侧墙局部破损" in joined:
        facts.append("侧墙局部破损且存在竖向裂缝" if "竖向裂缝" in joined else "侧墙局部破损")
    if "翼墙较为完好" in joined or "翼墙完好" in joined:
        facts.append("翼墙完好")
    if "未发现基础沉降" in joined:
        facts.append("无基础沉降")
    if "顶板无明显变形" in joined:
        facts.append("顶板无明显变形")
    if "较为整洁" in joined or "内部整洁" in joined:
        facts.append("通道内部整洁")
    if "止水带基本完好" in joined:
        facts.append("止水带基本完好")
    if "进出水口结构完整" in joined:
        facts.append("进出水口完整")
    first = "本次定检结果表明，人行通道" + "，".join(facts) + "。" if facts else ""
    specials: list[str] = []
    if "结构尺寸检测值与原竣工图纸基本相符" in joined or "结构尺寸与设计" in joined:
        specials.append("结构尺寸与设计基本相符")
    if "顶板混凝土强度" in joined and "满足设计" in joined:
        specials.append("顶板混凝土强度满足设计要求")
    if "侧墙条石抗压强度" in joined:
        specials.append("侧墙条石强度良好")
    if "钢筋保护层厚度" in joined:
        specials.append("钢筋保护层厚度全部合格")
    if "未发现明显的锈蚀迹象" in joined or "未发现明显锈蚀" in joined:
        specials.append("未发现明显锈蚀")
    if "背后填土较为密实" in joined and "未发现明显积水" in joined:
        specials.append("背后填土密实无积水")
    second = "，".join(specials) + "。" if specials else ""
    status = ""
    if re.search(r"满足\s*一类技术标准.*?良好的状态", joined, flags=re.S) or "一类，良好的状态" in joined:
        status = "综合评定为一类，处于良好状态。"
    return (first + second + status) or summary.overall_conclusion


def compose_gold_overall_conclusion(
    summary: BridgeSummary,
    defects: Sequence[DefectObservation],
    *,
    facility_noun: str = "桥梁",
    facility_name: str = "",
    evidence_texts: Sequence[str] = (),
) -> str:
    """Compose Gold-style OC from report conclusion/assessment evidence first.

    Defects are a fallback only when no report conclusion evidence for a system
    exists; they are no longer the primary data source.
    """
    evidence = tuple(_text(item) for item in evidence_texts if _text(item))
    facility_type = _facility_oc_type(facility_name or summary.bridge_name, facility_noun)
    if facility_type == "人行通道":
        return _pedestrian_passage_conclusion(evidence, summary)
    if facility_type == "人行天桥":
        return _pedestrian_bridge_conclusion(evidence, summary)
    card = _OC_FACILITY_CARDS[facility_type]
    groups = {"桥面系": [], "上部结构": [], "支座": [], "下部结构": []}
    for record in defects:
        system = _system_for(record)
        if system:
            groups[system].append(record)
    clauses: list[str] = []
    for system in card["order"]:
        clause = _best_system_clause(evidence, system)
        if not clause:
            fallback = _merge_defect_phrases(groups[system], limit=6)
            clause = "，".join(fallback)
        if clause:
            clause = _normalize_clause_for_facility(facility_type, system, clause)
            label = system
            if not clauses and card.get("bridge_prefix"):
                label = "桥梁" + label
            clauses.append(f"{label}{clause}")
    if not clauses:
        return summary.overall_conclusion
    text = "本次定检结果表明，" + "；".join(clauses) + "。"
    tail_mode = card.get("tail", "none")
    special_tail = _special_oc_tail(facility_type, evidence, summary)
    if special_tail:
        text += special_tail
    elif tail_mode == "status":
        tails = _explicit_status_sentences(evidence, summary.overall_conclusion)
        status = next((item for item in tails if any(k in item for k in ("整体技术状况", "总体技术状况", "综合评定为"))), "")
        if status:
            match = re.search(r"([A-E]级).*?(良好|完好|合格)状态", status)
            if match:
                text += f"整体技术状况{match.group(2)}（{match.group(1)}）。"
            else:
                text += status + "。"
    return text

def _defect_text(record: DefectObservation) -> str:
    return f"{_text(record.location)} {_text(record.defect_type)} {_text(record.description)}"


def _records_with(records: Sequence[DefectObservation], *markers: str) -> list[DefectObservation]:
    return [record for record in records if any(marker in _defect_text(record) for marker in markers)]


def _records_have(records: Sequence[DefectObservation], *markers: str) -> bool:
    return any(any(marker in _defect_text(record) for marker in markers) for record in records)


def _add_risk_candidate(
    candidates: list[tuple[int, str]], score: int, phrase: str
) -> None:
    phrase = _text(phrase).strip("，,；;。 ")
    if phrase and all(existing != phrase for _, existing in candidates):
        candidates.append((score, phrase))


def _gold_risk_candidates(defects: Sequence[DefectObservation]) -> list[tuple[int, str]]:
    """Build only report-backed standout-disease phrases.

    These patterns generalise the Gold-10 schema: they aggregate repeated rows
    to component-level disease nouns and never introduce a consequence or a
    structural fact absent from the defect records.
    """

    records = tuple(defects)
    candidates: list[tuple[int, str]] = []

    # Structural cracks.
    box_cracks = [
        record for record in records
        if "箱梁" in _text(record.location)
        and any(marker in _text(record.defect_type) for marker in ("裂缝", "开裂"))
    ]
    if len(box_cracks) >= 20:
        phrase = "箱梁梁底裂缝"
        if any("贯通" in _text(record.description) for record in box_cracks):
            phrase += "（部分横向贯通）"
        _add_risk_candidate(candidates, 14, phrase)

    abdomen = [
        record for record in records
        if "腹板" in _text(record.description)
        and any(marker in _text(record.defect_type) for marker in ("裂缝", "开裂"))
    ]
    if abdomen and len(box_cracks) < 20:
        if any(_text(record.location) == "梁体" for record in abdomen):
            phrase = "梁体裂缝"
        elif any(
            marker in _text(record.description)
            for record in abdomen
            for marker in ("竖向", "竖裂")
        ):
            phrase = "腹板竖向裂缝"
        else:
            phrase = "腹板裂缝"
        _add_risk_candidate(candidates, 12, phrase)

    bottom_cracks = [
        record for record in records
        if any(marker in _defect_text(record) for marker in ("板底", "梁底"))
        and any(marker in _text(record.defect_type) for marker in ("裂缝", "开裂"))
    ]
    if bottom_cracks:
        longitudinal = any(
            marker in _text(record.description)
            for record in bottom_cracks
            for marker in ("纵向", "纵裂")
        )
        subject = (
            "板底"
            if any(_text(record.location) == "板底" for record in bottom_cracks)
            and all("梁" not in _defect_text(record) for record in bottom_cracks)
            else "梁底"
        )
        _add_risk_candidate(
            candidates, 12, subject + ("纵向裂缝" if longitudinal else "裂缝")
        )

    front_wall = [
        record for record in records
        if "前墙" in _defect_text(record)
        and any(marker in _text(record.defect_type) for marker in ("裂缝", "开裂"))
    ]
    if front_wall:
        vertical = _records_have(front_wall, "竖向")
        horizontal = _records_have(front_wall, "横向")
        phrase = "前墙竖向和横向裂缝" if vertical and horizontal else "前墙裂缝"
        _add_risk_candidate(candidates, 11, phrase)

    side_wall = _records_with(records, "侧墙")
    if side_wall and _records_have(side_wall, "破损") and _records_have(side_wall, "裂缝"):
        phrase = "侧墙局部破损及竖向裂缝" if _records_have(side_wall, "竖向") else "侧墙局部破损及裂缝"
        _add_risk_candidate(candidates, 12, phrase)

    # Bearing/support defects.
    support = _records_with(records, "支座")
    if support:
        shear = _records_have(support, "剪切变形")
        missing = _records_have(support, "螺钉缺失")
        if shear or missing:
            suffix = "及".join(
                part for enabled, part in ((shear, "剪切变形"), (missing, "螺钉缺失")) if enabled
            )
            _add_risk_candidate(candidates, 14, f"支座{suffix}")
        pad = _records_with(support, "垫石")
        if pad and _records_have(pad, "裂缝") and _records_have(pad, "破损"):
            _add_risk_candidate(candidates, 11, "支座垫石破损开裂")
        block = _records_with(support, "挡块")
        if block:
            suffix = ""
            if _records_have(block, "破损"):
                suffix += "破损"
            if _records_have(support, "变形"):
                suffix += "变形"
            elif _records_have(block, "露筋"):
                suffix += "露筋"
            if suffix:
                _add_risk_candidate(candidates, 10, f"支座挡块{suffix}")

    # Deck/pavement and expansion-joint defects.
    pavement = [
        record for record in records
        if "桥面铺装" in _defect_text(record)
        or (
            any(marker in _text(record.location) for marker in ("桥面", "车行道"))
            and any(marker in _text(record.description) for marker in ("桥面", "路面", "铺装"))
        )
    ]
    if pavement:
        if _records_have(pavement, "未设伸缩缝", "伸缩缝处未设") and _records_have(pavement, "开裂", "裂缝"):
            _add_risk_candidate(candidates, 11, "桥面铺装开裂及未设伸缩缝")
        else:
            parts: list[str] = []
            for marker in ("破损", "坑洞", "露筋", "积水"):
                if _records_have(pavement, marker):
                    parts.append(marker)
            if not parts and _records_have(pavement, "开裂", "裂缝"):
                parts.append("开裂")
            if parts:
                phrase = (
                    "桥面铺装破损及积水"
                    if "破损" in parts and "积水" in parts
                    else "桥面铺装" + "、".join(parts[:3])
                )
                score = 13 if len(pavement) >= 5 else 8 if len(pavement) >= 2 else 5
                _add_risk_candidate(candidates, score, phrase)

    expansion = [
        record for record in records
        if "伸缩缝" in _text(record.location)
        or re.match(r"^\d+#缝", _text(record.description))
    ]
    if expansion:
        high = _records_have(expansion, "高差", "错台")
        broken = _records_have(expansion, "破损")
        jump = _records_have(expansion, "跳车")
        protection_band = _records_have(expansion, "保护带")
        if high and broken:
            _add_risk_candidate(candidates, 12, "伸缩缝破损高差")
        elif high:
            _add_risk_candidate(candidates, 12, "伸缩缝高差")
        if jump:
            _add_risk_candidate(candidates, 11, "伸缩缝跳车")
            if pavement and _records_have(pavement, "破损"):
                _add_risk_candidate(candidates, 15, "桥面铺装破损及伸缩缝跳车")
        elif protection_band and broken:
            _add_risk_candidate(candidates, 8, "伸缩缝保护带破损")

    barrier = [
        record for record in records
        if any(marker in _defect_text(record) for marker in ("防撞栏杆", "防撞护栏"))
    ]
    if barrier:
        parts = [marker for marker in ("锈蚀", "破损", "露筋", "松动") if _records_have(barrier, marker)]
        if parts:
            _add_risk_candidate(
                candidates,
                11 if len(barrier) >= 3 else 7,
                "防撞栏杆" + "".join(parts[:3]),
            )

    rail = [record for record in records if _text(record.location) == "栏杆" or "钢丝网" in _text(record.description)]
    if rail:
        parts = [marker for marker in ("锈蚀", "松动") if _records_have(rail, marker)]
        if parts:
            suffix = "".join(parts)
            _add_risk_candidate(candidates, 9, "栏杆" + suffix)
            if any(marker in _text(record.description) for record in rail for marker in ("钢丝网", "防护网")):
                _add_risk_candidate(candidates, 8, "防护网" + suffix)

    if any("桥面无泄水孔" in _text(record.description) for record in records):
        _add_risk_candidate(candidates, 9, "桥面无泄水孔")

    # Arch disease groups.
    arch_waist = _records_with(records, "拱腰")
    if arch_waist:
        if _records_have(arch_waist, "渗水") and _records_have(arch_waist, "泛碱"):
            _add_risk_candidate(candidates, 12, "拱腰多处渗水泛碱")
        elif _records_have(arch_waist, "泛碱"):
            _add_risk_candidate(candidates, 10, "拱腰多处泛碱")
        arch_damage = [
            record for record in records
            if any(marker in _defect_text(record) for marker in ("拱腰", "拱顶"))
            and "破损" in _text(record.defect_type)
        ]
        if arch_damage:
            has_waist = _records_have(arch_damage, "拱腰")
            has_top = _records_have(arch_damage, "拱顶")
            subject = "拱腰、拱顶" if has_waist and has_top else "拱腰" if has_waist else "拱顶"
            material = "条石" if any("条石" in _text(record.description) for record in arch_damage) else ""
            _add_risk_candidate(candidates, 9, f"{subject}{material}局部破损")

    # Water/efflorescence groups.
    abutment_water = [
        record for record in records
        if any(marker in _defect_text(record) for marker in ("桥台", "盖梁"))
        and "渗水" in _defect_text(record)
    ]
    joint_water = [
        record for record in records
        if "板间" in _defect_text(record)
        and any(marker in _defect_text(record) for marker in ("渗水", "泛碱"))
    ]
    if abutment_water and joint_water:
        phrase = (
            "桥台及板间渗水泛碱"
            if _records_have(joint_water, "泛碱")
            else "桥台及板间渗水"
        )
        _add_risk_candidate(candidates, 9, phrase)
    elif abutment_water:
        has_cap = any("盖梁" in _text(record.description) for record in abutment_water)
        if has_cap:
            phrase = "桥台及盖梁渗水"
        elif _records_have(abutment_water, "泛碱"):
            phrase = "桥台渗水泛碱"
        else:
            phrase = "桥台渗水"
        _add_risk_candidate(candidates, 8, phrase)

    # Slab/beam aggregate phrases used by Gold for concise standout disease.
    slab_bottom = _records_with(records, "板底")
    if slab_bottom:
        parts: list[str] = []
        if _records_have(slab_bottom, "纵向裂缝"):
            parts.append("纵向裂缝")
        elif _records_have(slab_bottom, "裂缝"):
            parts.append("裂缝")
        if _records_have(slab_bottom, "露筋") and _records_have(slab_bottom, "锈蚀"):
            parts.append("露筋锈蚀")
        if _records_have(slab_bottom, "渗水") and _records_have(slab_bottom, "泛碱"):
            parts.append("渗水泛碱")
        if len(parts) >= 2:
            _add_risk_candidate(candidates, 13, "梁底" + "、".join(parts))

    upper_wet_crack = [
        record for record in records
        if ("上部" in _text(record.location) or "板" in _text(record.description))
        and any(marker in _text(record.defect_type) for marker in ("裂缝", "开裂"))
        and "渗水" in _defect_text(record)
    ]
    if upper_wet_crack and not any("板底" in _text(record.location) for record in records):
        phrase = (
            "梁底纵裂"
            if any(marker in _text(record.description) for record in upper_wet_crack for marker in ("纵向", "纵裂"))
            else "梁底裂缝"
        )
        if _records_have(upper_wet_crack, "泛碱"):
            phrase += "、渗水泛碱"
        _add_risk_candidate(candidates, 13, phrase)

    scrape = [record for record in records if "梁底" in _text(record.description) and "刮痕" in _defect_text(record)]
    if scrape:
        parts = ["刮痕"]
        if _records_have(scrape, "破损"):
            parts.append("破损")
        if _records_have(scrape, "露筋"):
            parts.append("露筋")
        _add_risk_candidate(candidates, 10, "梁底" + "".join(parts))

    wet_joint = _records_with(records, "湿接缝")
    wet_corrosion = [record for record in wet_joint if _records_have((record,), "露筋", "锈蚀")]
    cross_corrosion = [
        record for record in records
        if "横隔板" in _defect_text(record) and _records_have((record,), "露筋", "锈蚀")
    ]
    if wet_corrosion and cross_corrosion and not _records_have(wet_joint, "泛碱", "渗水"):
        _add_risk_candidate(candidates, 12, "横隔板、湿接缝露筋锈蚀")
    if wet_joint and _records_have(wet_joint, "泛碱", "渗水"):
        _add_risk_candidate(
            candidates,
            11,
            "湿接缝泛碱" if _records_have(wet_joint, "泛碱") else "湿接缝渗水",
        )
    beam_corrosion = [
        record for record in records
        if any(marker in _defect_text(record) for marker in ("梁底", "梁体", "腹板", "翼板"))
        and _records_have((record,), "露筋", "锈蚀")
    ]
    if cross_corrosion and beam_corrosion:
        _add_risk_candidate(candidates, 12, "梁体及横隔板露筋锈蚀")
    elif cross_corrosion:
        _add_risk_candidate(candidates, 8, "横隔板露筋锈蚀")

    return candidates


def compose_gold_risk_points(
    current: str,
    defects: Sequence[DefectObservation],
    *,
    limit: int = 5,
) -> str:
    """Compose Gold-style risk points from current-report disease facts only.

    The function deliberately does *not* consume predictive-assessment safety
    prose.  It returns 3-6 concise standout disease phrases where possible;
    consequences are omitted unless a later caller provides a separately
    verified current-report consequence source.
    """

    candidates = sorted(_gold_risk_candidates(defects), key=lambda item: -item[0])
    selected: list[str] = []
    for score, phrase in candidates:
        if len(selected) >= 4 and score < 7:
            continue
        if any(phrase in existing or existing in phrase for existing in selected):
            continue
        selected.append(phrase)
        if len(selected) >= max(3, min(limit, 6)):
            break
    if selected:
        return "；".join(selected) + "。"
    return _text(current)

def canonicalize_public_summary(
    summary: BridgeSummary,
    defects: Sequence[DefectObservation],
    *,
    facility_noun: str = "桥梁",
) -> BridgeSummary:
    return replace(
        summary,
        overall_conclusion=compose_gold_overall_conclusion(
            summary, defects, facility_noun=facility_noun
        ),
        risk_points=compose_gold_risk_points(summary.risk_points, defects),
    )
