"""LangGraph subgraph for evidence-grounded narrative enhancement.

The graph lets the model propose only three narrative fields: detailed
conclusion, causes, and safety impact.  Treatments, overall conclusion, risk
points, and every structured fact remain deterministic baseline values.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ImportError as exc:  # pragma: no cover - exercised only without optional dependency
    raise ImportError(
        "Narrative enhancement requires the optional 'langgraph' dependency."
    ) from exc

if TYPE_CHECKING:
    from src.llm.client import ModelCallResult, OpenAIModelClient


ENHANCED_FIELDS = (
    "detailed_conclusion",
    "causes",
    "treatments",
    "safety_impact",
)
# The model only owns the three fields that showed real upside in review runs.
# Treatments are already stable in the deterministic baseline and are retained
# verbatim, which removes one retrieval task and prevents needless rewrites.
MODEL_GENERATED_FIELDS = (
    "detailed_conclusion",
    "causes",
    "safety_impact",
)
RETRIEVAL_TASK_FIELDS = (
    "overall_conclusion",
    "risk_points",
    "detailed_conclusion",
    "causes",
    "treatments",
    "safety_impact",
)
_MODEL_RETRIEVAL_FIELDS = MODEL_GENERATED_FIELDS
_MERGE_RETRIEVAL_TASK_FIELDS = MODEL_GENERATED_FIELDS
_CONTEXT_KEYS = {
    "text",
    "content",
    "raw_text",
    "source_file",
    "block_index",
    "table_index",
    "row_index",
    "column_index",
    "paragraph_index",
    "metadata",
    "source",
    "snippet",
    "title",
    "score",
    "distance",
}
_ID_KEYS = {"evidence_id", "evidence_ids", "id"}
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
_GRADE_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-F](?:级|类)?|[一二三四五六七八九十]+(?:级|类))(?![A-Za-z])"
)
_DATE_RE = re.compile(r"(?<![A-Za-z])[零〇一二两三四五六七八九十百千万亿]{2,8}年")
_QUANTITY_RE = re.compile(
    r"(?<![A-Za-z])[零〇一二两三四五六七八九十百千万亿]+"
    r"(?:处|项|个|件|条|座|孔|跨|片|块|道|段|次|名|栋|层|米|厘米|毫米|公里|公顷|平方米)"
)
_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "narrative_enhancement.md"
_PROMPT_IDENTITY_KEYS = (
    "sample_id",
    "source_file",
    "source",
    "schema_version",
    "report_id",
    "bridge_id",
    "bridge_name",
    "inspection_id",
    "history",
)
_MAX_DEFECT_DESCRIPTIONS = 1
_MAX_DEFECT_DESCRIPTION_CHARS = 100
_MAX_PROMPT_DEFECT_GROUPS = 12
_PROMPT_CONTEXT_MAX_ITEMS = 8
_PROMPT_CONTEXT_MAX_CHARS = 3200
_PROMPT_CONTEXT_ITEM_CHARS = 420
_PROMPT_CONTEXT_KEYS = (
    "evidence_id",
    "id",
    "kind",
    "source_bucket",
    "source_type",
    "section",
    "sample_id",
    "split",
    "score",
    "embedding_score",
    "rerank_score",
    "retrieval_mode",
    "title",
)
_PROMPT_SOURCE_KEYS = (
    "block_index",
    "table_index",
    "row_index",
    "column_index",
    "paragraph_index",
)
_MAX_EVIDENCE_TEXT_CHARS = 900

# Facility semantics stay in this adapter so old baselines remain valid while
# the merged extraction layer supplies a real FacilityContext.
_RETRIEVAL_SOURCE_QUOTA = {"report_evidence": 3, "knowledge_card": 2, "gold_label": 1}
_VISIBLE_SOURCE_QUOTA = {"report_evidence": 3, "domain_knowledge": 2, "label_example": 1}
FACILITY_COMPONENT_VOCABULARY: dict[str, tuple[str, ...]] = {
    "bridge": ("桥面系", "上部结构", "下部结构", "主梁", "支座", "桥墩", "桥台", "伸缩缝", "栏杆", "排水设施", "附属设施"),
    "pedestrian_underpass": ("顶板", "侧墙", "翼墙", "洞口", "沉降缝", "止水带", "排水设施", "附属设施"),
    "pedestrian_overpass": ("桥面板", "梯道", "栏杆", "扶手", "墩柱", "盖梁", "伸缩缝", "排水设施", "附属设施"),
    "vehicle_underpass": ("顶板", "侧墙", "翼墙", "洞口", "沉降缝", "止水带", "排水设施", "附属设施"),
    "tunnel": ("洞身", "洞口", "衬砌", "仰拱", "防水层", "沉降缝", "止水带", "排水设施", "附属设施"),
    "culvert": ("顶板", "侧墙", "翼墙", "洞口", "沉降缝", "止水带", "排水设施", "附属设施"),
    "road": ("路面", "路基", "边沟", "排水设施", "防护设施", "附属设施"),
    "other": ("洞口", "排水设施", "附属设施"),
}
_FACILITY_NOUNS = {"bridge": "桥梁", "pedestrian_underpass": "人行通道", "pedestrian_overpass": "人行天桥", "vehicle_underpass": "车行下穿道", "tunnel": "隧道", "culvert": "涵洞", "road": "道路", "other": "设施"}
_FACILITY_SUFFIXES = (
    ("人行过街天桥", "pedestrian_overpass", "人行天桥"), ("人行天桥", "pedestrian_overpass", "人行天桥"),
    ("人行地通道", "pedestrian_underpass", "人行通道"), ("人行地道", "pedestrian_underpass", "人行通道"),
    ("地下通道", "pedestrian_underpass", "人行通道"), ("人行通道", "pedestrian_underpass", "人行通道"),
    ("车行下穿道", "vehicle_underpass", "车行下穿道"), ("下穿道", "vehicle_underpass", "下穿道"),
    ("涵洞", "culvert", "涵洞"), ("道路", "road", "道路"),
    ("隧道", "tunnel", "隧道"), ("大桥", "bridge", "桥梁"), ("中桥", "bridge", "桥梁"),
    ("小桥", "bridge", "桥梁"), ("桥梁", "bridge", "桥梁"), ("天桥", "bridge", "天桥"),
    ("桥", "bridge", "桥梁"), ("通道", "other", "通道"),
)
_FACILITY_TYPE_ALIASES = {"bridge": "bridge", "桥": "bridge", "桥梁": "bridge", "pedestrian_underpass": "pedestrian_underpass", "人行通道": "pedestrian_underpass", "人行地通道": "pedestrian_underpass", "人行地道": "pedestrian_underpass", "地下通道": "pedestrian_underpass", "pedestrian_overpass": "pedestrian_overpass", "人行天桥": "pedestrian_overpass", "vehicle_underpass": "vehicle_underpass", "车行下穿道": "vehicle_underpass", "下穿道": "vehicle_underpass", "tunnel": "tunnel", "隧道": "tunnel", "culvert": "culvert", "涵洞": "culvert", "road": "road", "道路": "road", "other": "other"}
_BRIDGE_TERMS = ("该桥", "全桥", "桥面系", "上部结构", "下部结构")
_ALL_COMPONENT_TERMS = frozenset(term for values in FACILITY_COMPONENT_VOCABULARY.values() for term in values) | frozenset({"墙体", "中隔墙", "边墙", "路面", "人行道", "照明设施", "通风设施"})
_SEVERE_SAFETY_TERMS = ("严重承载风险", "承载能力不足", "重大安全隐患", "严重影响", "高风险", "失稳", "坍塌", "危及生命")
_LOW_IMPACT_RE = re.compile(r"影响较小|影响不大|影响有限|不影响(?:整体)?(?:使用|安全|承载)|未见明显(?:安全)?风险|满足.*(?:标准|要求)|良好状态")
_NEGATION_PREFIXES = ("不", "未", "无", "暂无", "不会", "并非", "没有", "未见")
_OFFICIAL_FORBIDDEN_TERMS = (
    "未提取到",
    "结构化病害记录",
    "典型部位为",
    "按结构部位归纳病害",
    "检测结果见表",
)
_OFFICIAL_PARAGRAPH_PREFIXES = (
    "经综合评定",
    ("本次为", "本次报告"),
    "目前",
    "综上",
)
_OFFICIAL_INTERNAL_RE = re.compile(
    r"(?:记录\s*(?:[0-9一二三四五六七八九十百千万]+|[Nn])\s*条|"
    r"第\s*[0-9一二三四五六七八九十百千万]+\s*章|"
    r"章节号|章节\s*[0-9一二三四五六七八九十百千万]+|表号|图号|"
    r"第\s*[0-9一二三四五六七八九十百千万]+\s*[表图]|"
    r"[表图]\s*[0-9一二三四五六七八九十百千万]+|"
    r"[0-9一二三四五六七八九十百千万]+\s*[表图]|"
    r"见\s*[表图])"
)
_FIRST_DETECTION_RE = re.compile(r"首次(?:定期)?检测|首次检测|第一次(?:定期)?检测")


class NarrativeState(TypedDict, total=False):
    """State passed between the five graph nodes."""

    baseline_prediction: dict[str, Any]
    prompt_baseline: dict[str, Any]
    sample_id: str
    source_file: str
    split: str
    report_facts: Any
    retrieval_results: Any
    retrieval_by_task: dict[str, Any]
    facility_context: Any
    field_states: Any
    locked_facts: Any
    generated_sections: dict[str, Any]
    validation_errors: list[str]
    field_validation_errors: dict[str, list[str]]
    field_results: dict[str, str]
    retry_count: int
    enhanced_prediction: dict[str, Any]
    used_fallback: bool
    call_metrics: dict[str, Any]
    max_retries: int
    generation_attempts: int
    generation_errors: list[str]
    validation_passed: bool
    global_validation_errors: list[str]
    field_fallbacks: list[str]
    context: dict[str, Any]


def _load_llm_types() -> tuple[type[Any], type[Any]]:
    """Load the shared client types without owning the client implementation."""

    try:
        from src.llm.client import ModelCallResult, OpenAIModelClient
    except ImportError as exc:  # pragma: no cover - the parent worker supplies this module
        raise ImportError(
            "Narrative enhancement requires src.llm.client.OpenAIModelClient "
            "and src.llm.client.ModelCallResult."
        ) from exc
    return OpenAIModelClient, ModelCallResult


def _jsonable(value: Any) -> Any:
    """Convert common project objects to JSON-compatible values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return str(value)


def _prediction_dict(prediction: Any) -> dict[str, Any]:
    normalized = _jsonable(prediction)
    if not isinstance(normalized, dict):
        raise TypeError("baseline_prediction must be a mapping or prediction object")
    return copy.deepcopy(normalized)


def _compact_prompt_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.7))
    tail = max(1, limit - head - 1)
    return text[:head] + "…" + text[-tail:]


def _compact_defects(defects: Any) -> list[dict[str, Any]]:
    """Group repeated defects while retaining a few useful descriptions."""

    if not isinstance(defects, Sequence) or isinstance(defects, (str, bytes, bytearray)):
        return []
    grouped: dict[tuple[str, str], list[str]] = {}
    for defect in defects:
        if not isinstance(defect, Mapping):
            continue
        location = str(defect.get("location") or "").strip()
        defect_type = str(defect.get("defect_type") or "").strip()
        description = _compact_prompt_text(
            defect.get("description") or defect.get("text") or "",
            _MAX_DEFECT_DESCRIPTION_CHARS,
        )
        key = (location, defect_type)
        descriptions = grouped.setdefault(key, [])
        if description and description not in descriptions and len(descriptions) < _MAX_DEFECT_DESCRIPTIONS:
            descriptions.append(description)
    result = [
        {
            "location": location,
            "defect_type": defect_type,
            "representative_descriptions": descriptions,
        }
        for (location, defect_type), descriptions in grouped.items()
    ]
    if len(result) <= _MAX_PROMPT_DEFECT_GROUPS:
        return result
    return [
        {
            **item,
            "representative_descriptions": item["representative_descriptions"][:2],
        }
        for item in result[:_MAX_PROMPT_DEFECT_GROUPS]
    ]


def _compact_recommendations(value: Any, *, max_items: int = 8) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, str]] = []
    for position, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping) or len(result) >= max_items:
            break
        item = {
            "index": str(raw.get("index") or position),
            "category": _compact_prompt_text(raw.get("category", ""), 24),
            "location": _compact_prompt_text(raw.get("location", ""), 48),
            "content": _compact_prompt_text(raw.get("content", raw.get("text", "")), 140),
        }
        result.append({key: value for key, value in item.items() if value})
    return result


def _compact_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    scalar_fields = (
        "bridge_name", "facility_name", "report_date", "inspection_date",
        "overall_score", "overall_grade", "superstructure_score",
        "superstructure_grade", "substructure_score", "substructure_grade",
        "deck_score", "deck_grade", "previous_overall_score",
        "previous_overall_grade", "trend", "recommendations_summary",
    )
    result = {key: copy.deepcopy(value[key]) for key in scalar_fields if value.get(key) not in (None, "")}
    for key in ("overall_conclusion", "risk_points"):
        if value.get(key):
            result[key] = _compact_prompt_text(value[key], 280)
    return result


def _prompt_locked_facts(value: Mapping[str, Any]) -> dict[str, Any]:
    keep = (
        "facility_name", "facility_type", "facility_noun", "report_date",
        "inspection_date", "overall_score", "overall_grade",
        "previous_overall_score", "previous_overall_grade",
        "recommendation_count",
    )
    compact = {key: copy.deepcopy(value[key]) for key in keep if value.get(key) not in (None, "")}
    defects = value.get("defects", [])
    compact["defect_count"] = len(defects) if isinstance(defects, Sequence) and not isinstance(defects, (str, bytes, bytearray)) else 0
    compact["defect_types"] = list(dict.fromkeys(
        str(item.get("defect_type", "")).strip()
        for item in defects
        if isinstance(item, Mapping) and str(item.get("defect_type", "")).strip()
    ))[:12]
    return compact


def _observed_components(
    facility_context: Mapping[str, Any],
    baseline: Mapping[str, Any],
    report_facts: Any,
) -> list[str]:
    evidence_text = _json_dump([baseline.get("defects", []), report_facts or []])
    return [
        term
        for term in dict.fromkeys(facility_context.get("components", []))
        if term and term in evidence_text
    ]


def _prompt_facility_context(
    facility_context: Mapping[str, Any],
    baseline: Mapping[str, Any],
    report_facts: Any,
) -> dict[str, Any]:
    return {
        "facility_name": facility_context.get("facility_name", ""),
        "facility_type": facility_context.get("facility_type", "other"),
        "facility_noun": facility_context.get("facility_noun", "设施"),
        # Only expose components actually present in the current report.  The
        # full facility vocabulary remains internal and must not seed invented
        # components such as a non-existent waterstop or waterproof layer.
        "observed_components": _observed_components(facility_context, baseline, report_facts),
        "forbidden_terms": list(facility_context.get("forbidden_terms", [])),
    }


def _prompt_baseline(
    baseline: Mapping[str, Any],
    *,
    sample_id: str = "",
    source_file: str = "",
) -> dict[str, Any]:
    """Build a compact, generation-only baseline view.

    The complete prediction remains in graph state for validation and fallback;
    it is not duplicated in the model prompt.
    """

    return {
        "sample_id": str(sample_id or baseline.get("sample_id", "") or ""),
        "source_file": str(source_file or baseline.get("source_file", "") or ""),
        "schema_version": str(baseline.get("schema_version", "") or ""),
        "summary": _compact_summary(baseline.get("summary", {})),
        "defects": _compact_defects(baseline.get("defects", [])),
        "recommendations": _compact_recommendations(baseline.get("recommendations", [])),
    }


def _compact_name(value: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(value or "")).strip()


def _facility_semantics(value: Any) -> tuple[str, str, str]:
    compact = _compact_name(value)
    explicit = _FACILITY_TYPE_ALIASES.get(compact.casefold())
    if explicit:
        return "", explicit, _FACILITY_NOUNS.get(explicit, "设施")
    for suffix, facility_type, noun in _FACILITY_SUFFIXES:
        if compact.endswith(suffix):
            return suffix, facility_type, noun
    return "", "", ""


def _string_items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，、;；]", value) if item.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalise_facility_context(value: Any, baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Accept the merged FacilityContext dataclass, a mapping, or old baselines."""

    baseline_context = _jsonable(baseline.get("facility_context", {}))
    raw: dict[str, Any] = dict(baseline_context) if isinstance(baseline_context, Mapping) else {}
    if isinstance(value, str):
        raw["facility_name"] = value
    else:
        normalised = _jsonable(value)
        if isinstance(normalised, Mapping):
            raw.update(dict(normalised))

    summary = baseline.get("summary")
    summary_map = summary if isinstance(summary, Mapping) else {}
    facility_name = str(
        raw.get("facility_name")
        or raw.get("name")
        or baseline.get("facility_name")
        or summary_map.get("facility_name")
        or summary_map.get("bridge_name")
        or ""
    ).strip()
    raw_type = str(raw.get("facility_type_raw") or raw.get("type_raw") or "").strip()
    explicit_type = str(raw.get("facility_type") or raw.get("type") or "").strip()
    suffix, inferred_type, inferred_noun = _facility_semantics(explicit_type or raw_type or facility_name)
    facility_type = _FACILITY_TYPE_ALIASES.get(explicit_type.casefold(), explicit_type)
    if facility_type not in FACILITY_COMPONENT_VOCABULARY:
        facility_type = inferred_type or "other"
    if not raw_type:
        raw_type = suffix or explicit_type or facility_type
    facility_noun = str(raw.get("facility_noun") or inferred_noun or _FACILITY_NOUNS.get(facility_type, "设施"))

    supplied_components = _string_items(raw.get("components") or raw.get("component_vocabulary"))
    components = list(dict.fromkeys(supplied_components + list(FACILITY_COMPONENT_VOCABULARY[facility_type])))
    supplied_forbidden = _string_items(raw.get("forbidden_terms"))
    forbidden = list(dict.fromkeys(supplied_forbidden))
    if facility_type not in {"bridge", "pedestrian_overpass"}:
        forbidden = list(dict.fromkeys(forbidden + list(_BRIDGE_TERMS)))

    context = dict(raw)
    context.update(
        {
            "facility_name": facility_name,
            "facility_type_raw": raw_type,
            "facility_type": facility_type,
            "facility_noun": facility_noun,
            "components": components,
            "forbidden_terms": forbidden,
        }
    )
    return context


def _default_locked_facts(
    baseline: Mapping[str, Any], facility_context: Mapping[str, Any]
) -> dict[str, Any]:
    summary = baseline.get("summary")
    summary_map = summary if isinstance(summary, Mapping) else {}
    locked: dict[str, Any] = {
        "facility_name": facility_context.get("facility_name", ""),
        "facility_type": facility_context.get("facility_type", ""),
        "facility_noun": facility_context.get("facility_noun", ""),
        "report_date": summary_map.get("report_date", ""),
        "inspection_date": facility_context.get("inspection_date", ""),
        "overall_score": summary_map.get("overall_score", ""),
        "overall_grade": summary_map.get("overall_grade", ""),
        "previous_overall_score": summary_map.get("previous_overall_score", ""),
        "previous_overall_grade": summary_map.get("previous_overall_grade", ""),
        "defects": copy.deepcopy(baseline.get("defects", [])),
        "recommendations": copy.deepcopy(baseline.get("recommendations", [])),
        "recommendation_count": _baseline_recommendation_count(baseline),
    }
    return {key: value for key, value in locked.items() if value not in (None, "") or key in {"defects", "recommendations", "recommendation_count"}}


def _normalise_locked_facts(
    value: Any,
    baseline: Mapping[str, Any],
    facility_context: Mapping[str, Any],
) -> dict[str, Any]:
    locked = _default_locked_facts(baseline, facility_context)
    normalised = _jsonable(value)
    if isinstance(normalised, Mapping):
        locked.update(dict(normalised))
    elif isinstance(normalised, Sequence) and not isinstance(normalised, (str, bytes, bytearray)):
        for item in normalised:
            if not isinstance(item, Mapping):
                continue
            key = item.get("field") or item.get("name") or item.get("key")
            if key:
                locked[str(key)] = item.get("value", item.get("text", ""))
    return locked


def _task_queries(
    baseline: Mapping[str, Any],
    facility_context: Any = None,
    report_facts: Any = None,
) -> dict[str, str]:
    """Build independent, facility-aware queries for every narrative task.

    ``overall_conclusion`` and ``risk_points`` are included as read-only
    preparation tasks.  They are never accepted as model output, but keeping
    their queries independent prevents a single broad query from erasing the
    evidence boundary between report-level conclusion and safety risk.
    """

    context = _normalise_facility_context(facility_context, baseline)
    summary = baseline.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    facility_name = str(context.get("facility_name") or baseline.get("sample_id", ""))
    facility_type = str(context.get("facility_type", "other"))
    facility_noun = str(context.get("facility_noun", "设施"))
    report_values = _jsonable(report_facts or [])
    evidence_text = _json_dump(
        [baseline.get("defects", []), report_values]
    )
    observed_components = [
        term
        for term in dict.fromkeys(context.get("components", []))
        if term and term in evidence_text
    ]
    components = "、".join(observed_components) or "按当前报告已出现构件"

    defect_pieces: list[str] = []
    for defect in baseline.get("defects", []):
        if isinstance(defect, Mapping):
            piece = " ".join(
                str(defect.get(key, ""))
                for key in ("location", "defect_type", "description")
                if defect.get(key)
            )
            if piece:
                defect_pieces.append(piece)
    safety_pieces: list[str] = []
    if isinstance(report_values, Sequence) and not isinstance(report_values, (str, bytes, bytearray)):
        for fact in report_values:
            if not isinstance(fact, Mapping):
                continue
            section = str(fact.get("section", "")).casefold()
            text = str(fact.get("text", "")).strip()
            if text and ("safety" in section or "安全" in section or "安全评估" in text):
                safety_pieces.append(text)
            if text and ("defect" in section or "病害" in section):
                defect_pieces.append(text)
    defect_context = "；".join(dict.fromkeys(piece for piece in defect_pieces if piece)) or "病害事实"
    safety_context = "；".join(dict.fromkeys(safety_pieces)) or str(summary.get("risk_points", "")) or "安全影响"
    overall = str(summary.get("overall_conclusion", ""))
    recommendations: list[str] = []
    for recommendation in baseline.get("recommendations", []):
        if isinstance(recommendation, Mapping):
            piece = " ".join(
                str(recommendation.get(key, ""))
                for key in ("index", "location", "category", "content")
                if recommendation.get(key)
            )
            if piece:
                recommendations.append(piece)
    recommendation_context = "；".join(recommendations) or "既有建议"
    common = (
        f"facility_name={facility_name}; facility_type={facility_type}; "
        f"facility_noun={facility_noun}; components={components or '按报告构件'}; "
        f"defects={defect_context}"
    )

    def make(task: str, *pieces: str) -> str:
        context_text = " ".join(piece.strip() for piece in pieces if piece and piece.strip())
        return _compact_prompt_text(f"task={task}; {common}; {context_text}", 3000)

    return {
        "overall_conclusion": make("overall_conclusion", overall, defect_context, safety_context),
        "risk_points": make("risk_points", str(summary.get("risk_points", "")), safety_context, defect_context),
        "detailed_conclusion": make("detailed_conclusion", overall, safety_context),
        "causes": make("causes", defect_context, components),
        "treatments": make("treatments", recommendation_context, defect_context, components),
        "safety_impact": make("safety_impact", safety_context, defect_context),
    }


def _compact_evidence(value: Any, max_chars: int = _MAX_EVIDENCE_TEXT_CHARS) -> Any:
    """Keep evidence anchors while bounding long report blocks in the prompt."""

    if isinstance(value, Mapping):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) == "text" and isinstance(item, str) and len(item) > max_chars:
                compact[str(key)] = item[:max_chars]
            else:
                compact[str(key)] = _compact_evidence(item, max_chars)
        return compact
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_compact_evidence(item, max_chars) for item in value]
    return value


def _json_dump(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)


def _compact_prompt_context(
    value: Any,
    *,
    max_items: int = _PROMPT_CONTEXT_MAX_ITEMS,
    max_chars: int = _PROMPT_CONTEXT_MAX_CHARS,
    max_item_chars: int = _PROMPT_CONTEXT_ITEM_CHARS,
) -> list[dict[str, Any]]:
    """Keep stable evidence IDs while bounding source text in the model prompt."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    used_chars = 0
    for raw_item in value:
        if not isinstance(raw_item, Mapping) or len(result) >= max_items:
            break
        raw_text = raw_item.get("text", raw_item.get("content", raw_item.get("snippet", "")))
        remaining = max_chars - used_chars
        if remaining <= 0 or not str(raw_text or "").strip():
            continue
        item: dict[str, Any] = {
            key: copy.deepcopy(raw_item[key])
            for key in _PROMPT_CONTEXT_KEYS
            if key in raw_item and raw_item[key] is not None
        }
        source = raw_item.get("source")
        if isinstance(source, Mapping):
            compact_source = {
                key: copy.deepcopy(source[key])
                for key in _PROMPT_SOURCE_KEYS
                if key in source and source[key] is not None
            }
            if compact_source:
                item["source"] = compact_source
        item["text"] = _compact_prompt_text(raw_text, min(max_item_chars, remaining))
        result.append(item)
        used_chars += len(item["text"])
    return result


def _task_hits(value: Any) -> list[dict[str, Any]]:
    """Normalize a task retrieval record to JSON-compatible hit mappings."""

    if isinstance(value, Mapping):
        value = value.get("hits", [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _source_counts(hits: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {bucket: 0 for bucket in _VISIBLE_SOURCE_QUOTA}
    for hit in hits:
        bucket = _retrieval_source_bucket(hit)
        public_bucket = {
            "report_evidence": "report_evidence",
            "knowledge_card": "domain_knowledge",
            "gold_label": "label_example",
        }.get(bucket)
        if public_bucket:
            counts[public_bucket] += 1
    return counts


def _limit_task_hits(hits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Apply the visible per-task 3/2/1 source contract locally.

    The LightRagIndex already supports source-aware retrieval.  This small
    adapter keeps the contract visible and bounded when a test double or an
    older retriever returns more records than requested.
    """

    selected: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    counts = {bucket: 0 for bucket in _RETRIEVAL_SOURCE_QUOTA}
    for raw_hit in hits:
        if not isinstance(raw_hit, Mapping):
            continue
        hit = dict(raw_hit)
        bucket = _retrieval_source_bucket(hit)
        if bucket not in _RETRIEVAL_SOURCE_QUOTA:
            unknown.append(hit)
            continue
        if counts[bucket] >= _RETRIEVAL_SOURCE_QUOTA[bucket]:
            continue
        counts[bucket] += 1
        public_bucket = {
            "report_evidence": "report_evidence",
            "knowledge_card": "domain_knowledge",
            "gold_label": "label_example",
        }[bucket]
        hit.setdefault("source_bucket", public_bucket)
        selected.append(hit)
    selected.extend(unknown[: max(0, sum(_RETRIEVAL_SOURCE_QUOTA.values()) - len(selected))])
    return selected


def _retrieval_task_records(
    task_queries: Mapping[str, str],
    task_hits: Mapping[str, Sequence[Mapping[str, Any]]],
    task_errors: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build an auditable, source-quota-visible retrieval-by-task envelope."""

    errors = task_errors if isinstance(task_errors, Mapping) else {}
    records: dict[str, dict[str, Any]] = {}
    for task in _MODEL_RETRIEVAL_FIELDS:
        hits = _limit_task_hits(task_hits.get(task, []))
        record: dict[str, Any] = {
            "query": str(task_queries.get(task, "")),
            "source_quota": dict(_VISIBLE_SOURCE_QUOTA),
            "source_counts": _source_counts(hits),
            "hits": hits,
        }
        if errors.get(task):
            record["error"] = str(errors[task])
        records[task] = record
    return records


def _compact_retrieval_by_task(value: Any, task_queries: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Expose only model-owned task evidence within a strict prompt budget."""

    queries = task_queries if isinstance(task_queries, Mapping) else {}
    if not isinstance(value, Mapping):
        value = {}
    compact: dict[str, Any] = {}
    for task in MODEL_GENERATED_FIELDS:
        raw = value.get(task, [])
        raw_map = raw if isinstance(raw, Mapping) else {}
        hits = _task_hits(raw)
        compact[task] = {
            "query": _compact_prompt_text(raw_map.get("query", queries.get(task, "")), 280),
            "source_counts": dict(raw_map.get("source_counts", _source_counts(hits))),
            "hits": _compact_prompt_context(
                hits,
                max_items=3,
                max_chars=800,
                max_item_chars=220,
            ),
        }
        if raw_map.get("error"):
            compact[task]["error"] = str(raw_map["error"])
    return compact


def _empty_metrics() -> dict[str, Any]:
    return {
        "call_count": 0,
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
        "duration_ms": 0.0,
        "model": None,
    }


def _value_from(value: Any, names: Sequence[str]) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _record_metrics(metrics: Mapping[str, Any], result: Any, elapsed_ms: float) -> dict[str, Any]:
    updated = dict(metrics)
    calls = int(updated.get("call_count", 0)) + 1
    updated["call_count"] = calls
    updated["calls"] = calls
    duration_ms = _value_from(result, ("duration_ms",))
    if isinstance(duration_ms, (int, float)):
        measured_ms = float(duration_ms)
    else:
        measured_ms = elapsed_ms
    updated["latency_ms"] = round(float(updated.get("latency_ms", 0.0)) + measured_ms, 3)
    updated["duration_ms"] = round(float(updated.get("duration_ms", 0.0)) + measured_ms, 3)

    direct_prompt = _value_from(result, ("prompt_tokens",))
    direct_completion = _value_from(result, ("completion_tokens",))
    direct_total = _value_from(result, ("total_tokens",))
    has_direct_usage = any(
        isinstance(value, (int, float)) for value in (direct_prompt, direct_completion, direct_total)
    )
    if has_direct_usage:
        if isinstance(direct_prompt, (int, float)):
            updated["prompt_tokens"] += int(direct_prompt)
        if isinstance(direct_completion, (int, float)):
            updated["completion_tokens"] += int(direct_completion)
        if isinstance(direct_total, (int, float)):
            updated["total_tokens"] += int(direct_total)
        else:
            updated["total_tokens"] = updated["prompt_tokens"] + updated["completion_tokens"]
    else:
        usage = _value_from(result, ("usage", "token_usage"))
        prompt_tokens = _value_from(usage, ("prompt_tokens", "input_tokens"))
        completion_tokens = _value_from(usage, ("completion_tokens", "output_tokens"))
        total_tokens = _value_from(usage, ("total_tokens",))
        if isinstance(prompt_tokens, (int, float)):
            updated["prompt_tokens"] += int(prompt_tokens)
        if isinstance(completion_tokens, (int, float)):
            updated["completion_tokens"] += int(completion_tokens)
        if isinstance(total_tokens, (int, float)):
            updated["total_tokens"] += int(total_tokens)
        else:
            updated["total_tokens"] = updated["prompt_tokens"] + updated["completion_tokens"]
    model = _value_from(result, ("model",))
    if model is not None:
        updated["model"] = str(model)
    return updated


def _client_call(client: Any, prompt: str) -> Any:
    """Call the shared client while accepting its small public method variants."""

    chat_json = getattr(client, "chat_json", None)
    if callable(chat_json):
        messages = [
            {
                "role": "system",
                "content": "Return only the evidence-grounded narrative JSON requested by the user.",
            },
            {"role": "user", "content": prompt},
        ]
        result = chat_json(messages)
        if inspect.isawaitable(result):
            raise RuntimeError("async model clients are not supported by the sync graph")
        return result

    for method_name in ("generate", "generate_json", "complete", "call", "invoke"):
        method = getattr(client, method_name, None)
        if callable(method):
            result = method(prompt)
            if inspect.isawaitable(result):
                raise RuntimeError("async model clients are not supported by the sync graph")
            return result
    if callable(client):
        result = client(prompt)
        if inspect.isawaitable(result):
            raise RuntimeError("async model clients are not supported by the sync graph")
        return result
    raise TypeError("client does not expose a supported synchronous call method")


def _response_payload(result: Any) -> Any:
    """Extract the response payload from ModelCallResult or a test double."""

    if isinstance(result, Mapping):
        if any(field in result for field in ENHANCED_FIELDS):
            return result
        for key in ("value", "parsed", "json", "output", "content", "text", "message", "response", "raw"):
            if key in result:
                return _response_payload(result[key])
        return result
    if isinstance(result, (str, bytes, bytearray)):
        text = result.decode("utf-8") if isinstance(result, (bytes, bytearray)) else result
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("model response is not valid JSON") from None
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                raise ValueError("model response is not valid JSON") from None

    for name in ("value", "parsed", "json", "output", "content", "text", "message", "response", "raw"):
        value = getattr(result, name, None)
        if value is not None:
            return _response_payload(value)
    raise ValueError("model response did not contain JSON")


def _render_prompt(state: NarrativeState) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    baseline = state.get("baseline_prediction", {})
    baseline = baseline if isinstance(baseline, Mapping) else {}
    prompt_baseline = state.get("prompt_baseline")
    if not isinstance(prompt_baseline, Mapping):
        prompt_baseline = _prompt_baseline(
            baseline,
            sample_id=str(state.get("sample_id", "")),
            source_file=str(state.get("source_file", "")),
        )
    facility_context = _normalise_facility_context(state.get("facility_context"), baseline)
    prompt_facility = _prompt_facility_context(
        facility_context, baseline, state.get("report_facts", [])
    )
    locked_facts = _normalise_locked_facts(state.get("locked_facts"), baseline, facility_context)
    context = state.get("context", {})
    context = context if isinstance(context, Mapping) else {}
    task_queries = context.get("task_queries", {})
    retrieval_by_task = state.get("retrieval_by_task")
    if not isinstance(retrieval_by_task, Mapping):
        retrieval_by_task = context.get("retrieval_by_task", {})
    replacements = {
        "{{SAMPLE_ID}}": str(state.get("sample_id", "")),
        "{{SOURCE_FILE}}": str(state.get("source_file", "")),
        "{{BASELINE_PREDICTION}}": _json_dump(prompt_baseline),
        "{{FACILITY_CONTEXT}}": _json_dump(prompt_facility),
        "{{FACILITY_NOUN}}": str(facility_context.get("facility_noun", "设施")),
        "{{FIELD_STATES}}": _json_dump(state.get("field_states", baseline.get("field_states", {}))),
        "{{LOCKED_FACTS}}": _json_dump(_prompt_locked_facts(locked_facts)),
        "{{REPORT_FACTS}}": _json_dump(
            _compact_prompt_context(
                state.get("report_facts", []),
                max_items=6,
                max_chars=1800,
                max_item_chars=300,
            )
        ),
        "{{SAFETY_EVIDENCE}}": _json_dump(_safety_prompt_context(state)),
        "{{RETRIEVAL_BY_TASK}}": _json_dump(
            _compact_retrieval_by_task(retrieval_by_task, task_queries)
        ),
        "{{VALIDATION_ERRORS}}": _json_dump(state.get("validation_errors", [])),
    }
    for marker, replacement in replacements.items():
        template = template.replace(marker, replacement)
    return template


def _query_from_baseline(
    baseline: Mapping[str, Any], facility_context: Any = None, report_facts: Any = None
) -> str:
    return _task_queries(baseline, facility_context, report_facts)["detailed_conclusion"]


def _prepare_context(state: NarrativeState, max_retries: int) -> dict[str, Any]:
    baseline = _prediction_dict(state.get("baseline_prediction", {}))
    report_facts = _jsonable(state.get("report_facts", []))
    facility_context = _normalise_facility_context(state.get("facility_context"), baseline)
    field_states = _jsonable(state.get("field_states", {}))
    locked_facts = _normalise_locked_facts(state.get("locked_facts"), baseline, facility_context)
    task_queries = _task_queries(baseline, facility_context, report_facts)
    return {
        "baseline_prediction": baseline,
        "prompt_baseline": _prompt_baseline(
            baseline,
            sample_id=str(state.get("sample_id", "")),
            source_file=str(state.get("source_file", "")),
        ),
        "report_facts": report_facts,
        "retrieval_results": [],
        "retrieval_by_task": _retrieval_task_records(task_queries, {}),
        "facility_context": facility_context,
        "field_states": field_states if isinstance(field_states, Mapping) else {},
        "locked_facts": locked_facts,
        "generated_sections": {},
        "validation_errors": [],
        "retry_count": 0,
        "enhanced_prediction": copy.deepcopy(baseline),
        "used_fallback": False,
        "call_metrics": _empty_metrics(),
        "max_retries": max_retries,
        "generation_attempts": 0,
        "generation_errors": [],
        "validation_passed": False,
        "context": {
            "query": task_queries["detailed_conclusion"],
            "task_queries": task_queries,
            "retrieval_by_task": _retrieval_task_records(task_queries, {}),
        },
    }


def _retrieval_source_bucket(hit: Mapping[str, Any]) -> str | None:
    values: list[str] = []
    for key in ("source_bucket", "source_type", "source_kind", "kind", "source", "type"):
        value = hit.get(key)
        if isinstance(value, Mapping):
            values.extend(str(value.get(name, "")) for name in ("source_bucket", "source_type", "source_kind", "kind", "type", "name"))
        elif value is not None:
            values.append(str(value))
    text = " ".join(values).casefold().replace("-", "_").replace(" ", "_")
    if any(alias in text for alias in ("gold_label", "label_example", "gold", "label")):
        return "gold_label"
    if any(alias in text for alias in ("knowledge_card", "domain_knowledge", "knowledge")):
        return "knowledge_card"
    if any(alias in text for alias in ("report_evidence", "report_fact", "current_report", "evidence")):
        return "report_evidence"
    return None


def _retrieval_hit_key(hit: Mapping[str, Any]) -> str:
    for key in ("evidence_id", "id"):
        value = hit.get(key)
        if value is not None and str(value):
            return f"{key}:{value}"
    return _json_dump({key: hit.get(key) for key in ("kind", "source", "text")})


def _merge_retrieval_results(task_hits: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task in _MERGE_RETRIEVAL_TASK_FIELDS:
        for raw_hit in _limit_task_hits(task_hits.get(task, [])):
            if not isinstance(raw_hit, Mapping):
                continue
            key = _retrieval_hit_key(raw_hit)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(raw_hit))

    selected: list[dict[str, Any]] = []
    counts = {bucket: 0 for bucket in _RETRIEVAL_SOURCE_QUOTA}
    unknown: list[dict[str, Any]] = []
    for hit in merged:
        bucket = _retrieval_source_bucket(hit)
        if bucket not in _RETRIEVAL_SOURCE_QUOTA:
            unknown.append(hit)
            continue
        if counts[bucket] >= _RETRIEVAL_SOURCE_QUOTA[bucket]:
            continue
        counts[bucket] += 1
        selected.append(hit)
    selected.extend(unknown[: max(0, sum(_RETRIEVAL_SOURCE_QUOTA.values()) - len(selected))])
    return selected


def _retrieve_knowledge(state: NarrativeState, retriever: Any) -> dict[str, Any]:
    if retriever is None:
        context = state.get("context", {})
        context = context if isinstance(context, Mapping) else {}
        task_queries = context.get("task_queries", {})
        task_queries = task_queries if isinstance(task_queries, Mapping) else {}
        empty_records = _retrieval_task_records(task_queries, {})
        return {
            "retrieval_results": [],
            "retrieval_by_task": empty_records,
            "context": {
                "query": str(task_queries.get("detailed_conclusion", "")),
                "task_queries": dict(task_queries),
                "task_hits": {task: [] for task in RETRIEVAL_TASK_FIELDS},
                "retrieval_by_task": empty_records,
            },
        }
    context = state.get("context", {})
    context = context if isinstance(context, Mapping) else {}
    task_queries = context.get("task_queries")
    if not isinstance(task_queries, Mapping):
        baseline = state.get("baseline_prediction", {})
        baseline = baseline if isinstance(baseline, Mapping) else {}
        task_queries = _task_queries(
            baseline,
            state.get("facility_context"),
            state.get("report_facts", []),
        )
    task_hits: dict[str, list[dict[str, Any]]] = {}
    task_errors: dict[str, str] = {}
    for task in _MODEL_RETRIEVAL_FIELDS:
        query = str(task_queries.get(task, ""))
        try:
            kwargs = {
                "sample_id": state.get("sample_id", ""),
                "split": state.get("split", "fit"),
                "top_k": sum(_RETRIEVAL_SOURCE_QUOTA.values()),
                "source_quota": dict(_RETRIEVAL_SOURCE_QUOTA),
            }
            try:
                results = retriever.retrieve(query, **kwargs)
            except TypeError as error:
                if "source_quota" not in str(error):
                    raise
                kwargs.pop("source_quota")
                results = retriever.retrieve(query, **kwargs)
        except Exception as error:
            # Retrieval is optional context.  A retrieval outage must not change
            # the deterministic baseline or leak an external exception into validation.
            results = []
            task_errors[task] = f"{type(error).__name__}: {str(error)[:200]}"
        if results is None:
            results = []
        task_hits[task] = [dict(item) for item in _jsonable(results) if isinstance(item, Mapping)]
    retrieval_results = _merge_retrieval_results(task_hits)
    retrieval_by_task = _retrieval_task_records(task_queries, task_hits, task_errors)
    return {
        "retrieval_results": retrieval_results,
        "retrieval_by_task": retrieval_by_task,
        "context": {
            "query": str(task_queries.get("detailed_conclusion", "")),
            "task_queries": dict(task_queries),
            "task_hits": task_hits,
            "retrieval_by_task": retrieval_by_task,
        },
    }


def _generate_narrative(state: NarrativeState, client: Any) -> dict[str, Any]:
    attempts = int(state.get("generation_attempts", 0))
    retry_count = max(0, attempts)
    prompt = _render_prompt(state)
    started = perf_counter()
    try:
        result = _client_call(client, prompt)
    except Exception:
        elapsed_ms = (perf_counter() - started) * 1000
        metrics = _record_metrics(state.get("call_metrics", _empty_metrics()), None, elapsed_ms)
        metrics["prompt_chars"] = int(metrics.get("prompt_chars", 0)) + len(prompt)
        return {
            "generation_attempts": attempts + 1,
            "retry_count": retry_count,
            "generated_sections": {},
            "generation_errors": ["model call failed"],
            "validation_errors": ["model call failed"],
            "call_metrics": metrics,
        }

    elapsed_ms = (perf_counter() - started) * 1000
    metrics = _record_metrics(state.get("call_metrics", _empty_metrics()), result, elapsed_ms)
    metrics["prompt_chars"] = int(metrics.get("prompt_chars", 0)) + len(prompt)
    try:
        payload = _response_payload(result)
        if not isinstance(payload, Mapping):
            raise ValueError("model response must be a JSON object")
        generated = _jsonable(payload)
        if not isinstance(generated, dict):
            raise ValueError("model response must be a JSON object")
        generated = _normalise_official_none(generated)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {
            "generation_attempts": attempts + 1,
            "retry_count": retry_count,
            "generated_sections": {},
            "generation_errors": ["model response is not valid JSON"],
            "validation_errors": ["model response is not valid JSON"],
            "call_metrics": metrics,
        }
    return {
        "generation_attempts": attempts + 1,
        "retry_count": retry_count,
        "generated_sections": generated,
        "generation_errors": [],
        "validation_errors": [],
        "call_metrics": metrics,
    }


def _normalise_official_none(value: Any) -> Any:
    """Keep the official output vocabulary when the model says a field is absent."""

    if isinstance(value, str):
        return value.replace("未提取到", "无")
    if isinstance(value, list):
        return [_normalise_official_none(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _normalise_official_none(item) for key, item in value.items()}
    return value


def _collect_evidence_ids(value: Any, depth: int = 0) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text == "evidence_id" or key_text == "id":
                if isinstance(item, (str, int)) and not isinstance(item, bool):
                    found.add(str(item))
            elif key_text == "evidence_ids" and isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                found.update(str(identifier) for identifier in item if isinstance(identifier, (str, int)))
            elif depth == 0 and key_text not in _CONTEXT_KEYS and key_text not in _ID_KEYS:
                # Also support a mapping keyed by stable evidence id.
                found.add(key_text)
            found.update(_collect_evidence_ids(item, depth + 1))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found.update(_collect_evidence_ids(item, depth + 1))
    return found


def _baseline_recommendation_count(baseline: Mapping[str, Any]) -> int:
    recommendations = baseline.get("recommendations", [])
    if isinstance(recommendations, Sequence) and not isinstance(recommendations, (str, bytes, bytearray)):
        return len(recommendations)
    return 0


def _candidate_texts(candidate: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    detailed = candidate.get("detailed_conclusion", [])
    if isinstance(detailed, list):
        texts.extend(item for item in detailed if isinstance(item, str))
    for field in ("causes", "safety_impact"):
        values = candidate.get(field, [])
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                texts.append(item["text"])
    return texts


def _candidate_field_texts(candidate: Mapping[str, Any], field: str) -> list[str]:
    """Return only the generated prose belonging to one field."""

    value = candidate.get(field, [])
    if field == "detailed_conclusion":
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
    if not isinstance(value, list):
        return []
    return [
        str(item.get("text"))
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("text"), str)
    ]


def _context_tokens(value: Any) -> tuple[set[str], set[str]]:
    text = _json_dump(value)
    number_tokens = set(_NUMBER_RE.findall(text))
    number_tokens.update(_DATE_RE.findall(text))
    number_tokens.update(_QUANTITY_RE.findall(text))
    return number_tokens, set(_GRADE_RE.findall(text))


def _record_identifier(record: Mapping[str, Any]) -> str | None:
    for key in ("evidence_id", "id"):
        value = record.get(key)
        if value is not None and str(value):
            return str(value)
    return None


def _report_evidence_sets(state: NarrativeState) -> tuple[set[str], set[str], str]:
    safety_ids: set[str] = set()
    defect_ids: set[str] = set()
    safety_texts: list[str] = []
    facts = state.get("report_facts", [])
    if not isinstance(facts, Sequence) or isinstance(facts, (str, bytes, bytearray)):
        return safety_ids, defect_ids, ""
    for fact in facts:
        if not isinstance(fact, Mapping):
            continue
        identifier = _record_identifier(fact)
        if not identifier:
            continue
        text = str(fact.get("text", ""))
        section = str(fact.get("section", "")).casefold()
        is_safety = "safety" in section or "安全" in section or "安全评估" in text or "安全影响" in text
        is_defect = "defect" in section or "病害" in section or any(
            term in text for term in ("裂缝", "破损", "渗水", "锈蚀", "沉降", "变形", "脱落", "剥落", "堵塞")
        )
        if is_safety:
            safety_ids.add(identifier)
            safety_texts.append(text)
        elif is_defect:
            defect_ids.add(identifier)
    return safety_ids, defect_ids, " ".join(safety_texts)


def _safety_priority(state: NarrativeState) -> tuple[set[str], str, str]:
    safety_ids, defect_ids, safety_text = _report_evidence_sets(state)
    if safety_ids:
        return safety_ids, "current report safety assessment", safety_text
    if defect_ids:
        return defect_ids, "current report defect facts", ""
    return set(), "current report evidence", ""


def _safety_prompt_context(state: NarrativeState) -> dict[str, Any]:
    safety_ids, defect_ids, safety_text = _report_evidence_sets(state)
    return {
        "safety_assessment_ids": sorted(safety_ids),
        "defect_evidence_ids": sorted(defect_ids)[:12],
        "safety_assessment": _compact_prompt_text(safety_text, 720),
        "low_impact": bool(_LOW_IMPACT_RE.search(safety_text)),
    }


def _normalise_facility_name(value: Any) -> str:
    compact = _compact_name(value)
    compact = re.sub(r"^(?:该|本|此)(?:处|设施)?", "", compact)
    return compact.replace("人行地通道", "人行通道").replace("人行地道", "人行通道").replace("地下通道", "人行通道")


def _facility_semantic_errors_by_field(
    state: NarrativeState, candidate: Mapping[str, Any]
) -> dict[str, list[str]]:
    baseline = state.get("baseline_prediction", {})
    baseline = baseline if isinstance(baseline, Mapping) else {}
    facility_context = _normalise_facility_context(state.get("facility_context"), baseline)
    facility_type = str(facility_context.get("facility_type", "other"))
    facility_name = _normalise_facility_name(facility_context.get("facility_name", ""))
    facility_noun = str(facility_context.get("facility_noun", "设施"))
    evidence_text = _json_dump(
        {
            "baseline_defects": baseline.get("defects", []),
            "report_facts": state.get("report_facts", []),
            "retrieval_results": state.get("retrieval_results", []),
        }
    )
    errors: dict[str, list[str]] = {field: [] for field in ENHANCED_FIELDS}

    def add(field: str, message: str) -> None:
        if message not in errors[field]:
            errors[field].append(message)

    facility_nouns = ("人行通道", "人行天桥", "隧道", "桥梁", "涵洞", "下穿通道", "道路")
    allowed_nouns = {facility_noun, "设施", "通道"}
    # Competition Gold and the official writing example describe pedestrian
    # overpasses with bridge wording ("该桥"/"桥梁").
    if facility_type == "pedestrian_overpass":
        allowed_nouns.add("桥梁")
    for field in MODEL_GENERATED_FIELDS:
        narrative_text = " ".join(_candidate_field_texts(candidate, field))
        if not narrative_text:
            continue
        if facility_type not in {"bridge", "pedestrian_overpass"}:
            unsupported_bridge_terms = [term for term in _BRIDGE_TERMS if term in narrative_text]
            if unsupported_bridge_terms:
                add(field, "generated narrative uses an unsupported bridge term: " + "、".join(unsupported_bridge_terms))

        unsupported_components = [
            term
            for term in sorted(_ALL_COMPONENT_TERMS, key=len, reverse=True)
            if term in narrative_text and term not in evidence_text
        ]
        if unsupported_components:
            add(field, "generated narrative uses a component absent from supplied evidence: " + "、".join(dict.fromkeys(unsupported_components)))

        # A normative title may legitimately name another facility type, e.g.
        # 《公路隧道养护技术规范》 cited by a pedestrian-underpass report.
        facility_noun_text = re.sub(r"《[^》]*》", "", narrative_text)
        other_nouns = [noun for noun in facility_nouns if noun in facility_noun_text and noun not in allowed_nouns]
        if other_nouns:
            add(field, "generated narrative uses another facility noun: " + "、".join(other_nouns))

        # A shared site prefix catches the common A/EC interchange mix-up
        # without treating ordinary Chinese prose before a facility suffix as
        # a name.
        if facility_name:
            prefix = next((facility_name[: -len(suffix)] for suffix, _, _ in _FACILITY_SUFFIXES if facility_name.endswith(suffix)), facility_name)
            anchor = re.split(r"[A-Za-z0-9#]", prefix, maxsplit=1)[0] or prefix[:2]
            if len(anchor) >= 2:
                for suffix, _, _ in _FACILITY_SUFFIXES:
                    match = re.search(re.escape(anchor) + r"[\u4e00-\u9fffA-Za-z0-9#]{0,20}" + suffix, narrative_text)
                    if not match:
                        continue
                    observed = _normalise_facility_name(match.group(0))
                    if observed != facility_name and facility_name not in observed:
                        add(field, "generated narrative uses another facility name")
                        break

    safety_ids, defect_ids, priority_text = _report_evidence_sets(state)
    current_report_ids = safety_ids | defect_ids
    safety_values = candidate.get("safety_impact")
    cited_safety_assessment = False
    if isinstance(safety_values, list):
        for index, item in enumerate(safety_values):
            if not isinstance(item, Mapping):
                continue
            item_ids = {
                str(identifier)
                for identifier in item.get("evidence_ids", [])
                if isinstance(identifier, (str, int)) and not isinstance(identifier, bool)
            }
            if current_report_ids and not item_ids.intersection(current_report_ids):
                add("safety_impact", f"safety_impact[{index}] must cite current report safety or defect evidence")
            if item_ids.intersection(safety_ids):
                cited_safety_assessment = True
            text = str(item.get("text", ""))
            if _LOW_IMPACT_RE.search(priority_text):
                for severe_term in _SEVERE_SAFETY_TERMS:
                    position = text.find(severe_term)
                    if position < 0:
                        continue
                    prefix = text[max(0, position - 8) : position]
                    if not any(prefix.endswith(negation) for negation in _NEGATION_PREFIXES):
                        add("safety_impact", "safety_impact exaggerates a report assessment marked as low impact")
                        break
        if safety_ids and safety_values and not cited_safety_assessment:
            add("safety_impact", "safety_impact must preserve the current report safety assessment")
    return errors


def _facility_semantic_errors(state: NarrativeState, candidate: Mapping[str, Any]) -> list[str]:
    """Return facility/safety errors while retaining the field association."""

    errors: list[str] = []
    for field_errors in _facility_semantic_errors_by_field(state, candidate).values():
        errors.extend(field_errors)
    return list(dict.fromkeys(errors))


def _has_locked_change(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
    locked_facts: Any = None,
    facility_context: Any = None,
) -> bool:
    """Detect explicit attempts to change known locked fields in model JSON."""

    def walk(current: Any, original: Any, at_top: bool = False) -> bool:
        if not isinstance(current, Mapping) or not isinstance(original, Mapping):
            return False
        for key, value in current.items():
            if at_top and key in ENHANCED_FIELDS:
                continue
            if key not in original:
                continue
            original_value = original[key]
            if isinstance(value, Mapping) and isinstance(original_value, Mapping):
                if walk(value, original_value, False):
                    return True
            elif value != original_value:
                return True
        return False

    if walk(candidate, baseline, True):
        return True

    facts = locked_facts if isinstance(locked_facts, Mapping) else {}
    if not facts:
        context = _normalise_facility_context(facility_context, baseline)
        facts = _default_locked_facts(baseline, context)

    def values_for(key: str) -> list[Any]:
        values: list[Any] = []
        aliases = {
            "facility_name": ("facility_name", "name", "bridge_name"),
            "report_date": ("report_date",),
            "inspection_date": ("inspection_date",),
            "overall_score": ("overall_score",),
            "overall_grade": ("overall_grade",),
            "previous_overall_score": ("previous_overall_score",),
            "previous_overall_grade": ("previous_overall_grade",),
            "facility_type": ("facility_type",),
            "facility_noun": ("facility_noun",),
            "defects": ("defects",),
            "recommendations": ("recommendations",),
            "recommendation_count": ("recommendation_count",),
        }.get(key, (key,))
        for mapping in (candidate, candidate.get("summary"), candidate.get("facility_context")):
            if not isinstance(mapping, Mapping):
                continue
            for alias in aliases:
                if alias in mapping:
                    values.append(mapping[alias])
            if key == "recommendation_count" and "recommendations" in mapping:
                value = mapping["recommendations"]
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                    values.append(len(value))
        return values

    for key, expected in facts.items():
        if key in ENHANCED_FIELDS or expected in (None, ""):
            continue
        for actual in values_for(str(key)):
            if key in {"facility_name", "facility_type", "facility_noun", "report_date", "inspection_date"}:
                same = _compact_name(actual) == _compact_name(expected)
            elif key == "recommendation_count":
                same = actual == expected
            else:
                same = actual == expected
            if not same:
                return True
    return False


def _validate_output(state: NarrativeState) -> dict[str, Any]:
    candidate = state.get("generated_sections", {})
    baseline = state.get("baseline_prediction", {})
    errors: list[str] = []
    field_errors: dict[str, list[str]] = {field: [] for field in ENHANCED_FIELDS}
    global_errors: list[str] = []

    def add(message: str, field: str | None = None, *, global_error: bool = False) -> None:
        if message not in errors:
            errors.append(message)
        if field in field_errors and message not in field_errors[field]:
            field_errors[field].append(message)
        elif global_error or field is None:
            if message not in global_errors:
                global_errors.append(message)

    for message in state.get("generation_errors", []):
        add(message, global_error=True)
        for field in ENHANCED_FIELDS:
            if message not in field_errors[field]:
                field_errors[field].append(message)
    if not isinstance(candidate, Mapping):
        add("generated sections must be a JSON object", global_error=True)
        for field in ENHANCED_FIELDS:
            if "generated sections must be a JSON object" not in field_errors[field]:
                field_errors[field].append("generated sections must be a JSON object")
        return {
            "validation_errors": errors,
            "validation_passed": False,
            "field_validation_errors": field_errors,
            "global_validation_errors": global_errors,
        }

    for field in MODEL_GENERATED_FIELDS:
        if field not in candidate:
            add(f"missing generated field: {field}", field)

    report_text = _json_dump(state.get("report_facts", []))
    detailed = candidate.get("detailed_conclusion")
    if not isinstance(detailed, list):
        add("detailed_conclusion must be an array", "detailed_conclusion")
    else:
        if len(detailed) != 4:
            add("detailed_conclusion must contain exactly four official paragraphs", "detailed_conclusion")
        if any(not isinstance(item, str) for item in detailed):
            add("detailed_conclusion items must be strings", "detailed_conclusion")
        elif len(detailed) == 4:
            for index, (paragraph, expected) in enumerate(
                zip(detailed, _OFFICIAL_PARAGRAPH_PREFIXES)
            ):
                allowed = expected if isinstance(expected, tuple) else (expected,)
                if not paragraph.strip().startswith(allowed):
                    add(
                        f"detailed_conclusion[{index}] does not follow official paragraph structure",
                        "detailed_conclusion",
                    )
            if any(_FIRST_DETECTION_RE.search(paragraph) for paragraph in detailed) and not _FIRST_DETECTION_RE.search(report_text):
                add(
                    "first-detection wording requires explicit report evidence",
                    "detailed_conclusion",
                )

    allowed_ids = _collect_evidence_ids(state.get("report_facts", []))
    allowed_ids.update(_collect_evidence_ids(state.get("retrieval_results", [])))
    task_records = state.get("retrieval_by_task", {})
    if isinstance(task_records, Mapping):
        for record in task_records.values():
            if isinstance(record, Mapping):
                allowed_ids.update(_collect_evidence_ids(record.get("hits", [])))
    for field in ("causes", "safety_impact"):
        values = candidate.get(field)
        if not isinstance(values, list):
            add(f"{field} must be an array", field)
            continue
        required = {"text", "evidence_ids"}
        for index, item in enumerate(values):
            if not isinstance(item, Mapping):
                add(f"{field}[{index}] must be an object", field)
                continue
            if not required.issubset(item):
                add(f"{field}[{index}] is missing a required field", field)
            if not isinstance(item.get("text"), str):
                add(f"{field}[{index}].text must be a string", field)
            evidence_ids = item.get("evidence_ids")
            if not isinstance(evidence_ids, list) or any(
                not isinstance(identifier, (str, int)) or isinstance(identifier, bool) for identifier in evidence_ids
            ):
                add(f"{field}[{index}].evidence_ids must be an array of identifiers", field)
            elif any(str(identifier) not in allowed_ids for identifier in evidence_ids):
                add(f"{field}[{index}] contains an unknown evidence_id", field)

    if isinstance(baseline, Mapping) and _has_locked_change(
        candidate,
        baseline,
        state.get("locked_facts"),
        state.get("facility_context"),
    ):
        add("generated output attempts to change a locked deterministic field", global_error=True)

    for field, messages in _facility_semantic_errors_by_field(state, candidate).items():
        for message in messages:
            add(message, field)

    allowed_numbers, allowed_grades = _context_tokens(
        {
            "baseline_prediction": baseline,
            "report_facts": state.get("report_facts", []),
            "retrieval_results": state.get("retrieval_results", []),
        }
    )
    for field in MODEL_GENERATED_FIELDS:
        for text in _candidate_field_texts(candidate, field):
            blocked = [term for term in _OFFICIAL_FORBIDDEN_TERMS if term in text]
            internal = _OFFICIAL_INTERNAL_RE.search(text)
            if blocked or internal:
                markers = blocked[:2]
                if internal:
                    markers.append(internal.group(0))
                detail = "、".join(dict.fromkeys(markers))
                add(
                    "generated narrative contains forbidden internal extraction language"
                    + (f": {detail}" if detail else ""),
                    field,
                )
            if _FIRST_DETECTION_RE.search(text) and not _FIRST_DETECTION_RE.search(report_text):
                add("first-detection wording requires explicit report evidence", field)
            number_tokens = set(_NUMBER_RE.findall(text))
            number_tokens.update(_DATE_RE.findall(text))
            number_tokens.update(_QUANTITY_RE.findall(text))
            if any(token not in allowed_numbers for token in number_tokens):
                add("generated narrative introduces an unsupported number, grade, or date", field)
            if any(token not in allowed_grades for token in _GRADE_RE.findall(text)):
                add("generated narrative introduces an unsupported number, grade, or date", field)

    summary = baseline.get("summary") if isinstance(baseline, Mapping) else None
    if isinstance(summary, Mapping):
        locked_summary = {
            key: summary.get(key)
            for key in (
                "overall_score",
                "overall_grade",
                "superstructure_score",
                "superstructure_grade",
                "substructure_score",
                "substructure_grade",
                "deck_score",
                "deck_grade",
            )
            if summary.get(key) not in (None, "", "无")
        }
        summary_numbers, summary_grades = _context_tokens(locked_summary)
        baseline_numbers, baseline_grades = _context_tokens(baseline.get("detailed_conclusion", []))
        required_numbers = summary_numbers.intersection(baseline_numbers)
        required_grades = summary_grades.intersection(baseline_grades)
        detailed_text = " ".join(_candidate_field_texts(candidate, "detailed_conclusion"))
        observed_numbers, observed_grades = _context_tokens(detailed_text)
        if required_numbers - observed_numbers or required_grades - observed_grades:
            add("detailed_conclusion omits a locked summary score or grade", "detailed_conclusion")

    return {
        "validation_errors": errors,
        "validation_passed": not errors,
        "field_validation_errors": field_errors,
        "global_validation_errors": global_errors,
    }


def _canonical_field(candidate: Mapping[str, Any], field: str) -> Any:
    if field == "detailed_conclusion":
        return list(candidate[field])
    if field in {"causes", "safety_impact"}:
        return [
            {"text": item["text"], "evidence_ids": list(item["evidence_ids"])}
            for item in candidate[field]
        ]
    return [
        {
            "recommendation_index": item["recommendation_index"],
            "text": item["text"],
            "evidence_ids": list(item["evidence_ids"]),
        }
        for item in candidate[field]
    ]


def _official_baseline_detailed_conclusion(value: Any) -> list[str] | None:
    """Keep a complete bridge baseline while applying the official four prefixes."""
    if not isinstance(value, list) or len(value) != 4 or any(not isinstance(item, str) for item in value):
        return None
    result: list[str] = []
    for index, paragraph in enumerate(value):
        allowed = _OFFICIAL_PARAGRAPH_PREFIXES[index]
        allowed = allowed if isinstance(allowed, tuple) else (allowed,)
        if paragraph.strip().startswith(allowed):
            result.append(paragraph)
        elif index == 0:
            result.append("经综合评定，" + paragraph)
        elif index == 1:
            result.append("本次报告" + paragraph)
        else:
            result.append(("目前" if index == 2 else "综上") + "，" + paragraph)
    return result


def _plain_text_items(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [
        str(item.get("text", "")) if isinstance(item, Mapping) else str(item)
        for item in value
        if str(item.get("text", "") if isinstance(item, Mapping) else item).strip()
    ]


def _baseline_needs_enhancement(field: str, value: Any) -> bool:
    items = _plain_text_items(value)
    text = " ".join(items)
    if not text.strip():
        return True
    if any(term in text for term in _OFFICIAL_FORBIDDEN_TERMS) or _OFFICIAL_INTERNAL_RE.search(text):
        return True
    if field == "detailed_conclusion":
        if len(items) != 4:
            return True
        for paragraph, expected in zip(items, _OFFICIAL_PARAGRAPH_PREFIXES):
            allowed = expected if isinstance(expected, tuple) else (expected,)
            if not paragraph.strip().startswith(allowed):
                return True
        return len(text) > 2400
    if field == "causes":
        garbage = ("目前能够满足", "主要结论", "外观检测结果", "已有证据为")
        cause_markers = ("由于", "可能与", "有关", "所致", "导致", "造成")
        return any(term in text for term in garbage) or not any(term in text for term in cause_markers)
    if field == "safety_impact":
        garbage = ("已有证据为", "报告未明确该类病害", "其他部位", "结构化病害")
        contradiction = "不影响" in text and any(term in text for term in ("影响桥梁安全", "危及", "重大安全隐患"))
        return len(text) < 30 or any(term in text for term in garbage) or contradiction
    return False


def _should_accept_generated_field(
    field: str,
    baseline: Mapping[str, Any],
    facility_context: Mapping[str, Any],
) -> bool:
    if field == "treatments":
        return False
    facility_type = str(facility_context.get("facility_type", "other"))
    # Review evidence showed the largest, repeatable gains on pedestrian
    # underpasses, while already-polished bridge prose usually regressed.
    if facility_type not in {"bridge", "pedestrian_overpass"}:
        return True
    return _baseline_needs_enhancement(field, baseline.get(field, []))


def _canonical_sections(
    candidate: Mapping[str, Any],
    *,
    fallback: Mapping[str, Any] | None = None,
    valid_fields: Sequence[str] = MODEL_GENERATED_FIELDS,
) -> dict[str, Any]:
    """Canonicalize model-owned fields and always preserve treatments."""

    valid = set(valid_fields)
    canonical: dict[str, Any] = {}
    for field in ENHANCED_FIELDS:
        if field in valid and field in candidate:
            canonical[field] = _canonical_field(candidate, field)
        elif isinstance(fallback, Mapping) and field in fallback:
            canonical[field] = copy.deepcopy(fallback[field])
        else:
            canonical[field] = []
    return canonical


def _finalize(state: NarrativeState) -> dict[str, Any]:
    baseline = copy.deepcopy(state.get("baseline_prediction", {}))
    candidate = state.get("generated_sections", {})
    candidate = candidate if isinstance(candidate, Mapping) else {}
    field_errors = state.get("field_validation_errors", {})
    field_errors = field_errors if isinstance(field_errors, Mapping) else {}
    global_errors = state.get("global_validation_errors", [])
    global_failure = bool(global_errors)
    facility_context = _normalise_facility_context(state.get("facility_context"), baseline)
    validation_fallbacks: list[str] = []
    baseline_kept: list[str] = []
    valid_fields: list[str] = []
    selection_reasons: dict[str, str] = {}
    for field in MODEL_GENERATED_FIELDS:
        failed = global_failure or bool(field_errors.get(field)) or field not in candidate
        if failed:
            validation_fallbacks.append(field)
            selection_reasons[field] = "validation_fallback"
        elif not _should_accept_generated_field(field, baseline, facility_context):
            baseline_kept.append(field)
            selection_reasons[field] = "baseline_quality_gate"
        else:
            valid_fields.append(field)
            selection_reasons[field] = "enhanced"

    enhanced = copy.deepcopy(baseline)
    canonical = _canonical_sections(candidate, fallback=baseline, valid_fields=valid_fields)
    for field in valid_fields:
        enhanced[field] = canonical[field]
    field_results = {
        field: (
            "baseline" if field == "treatments" or field in baseline_kept
            else "enhanced" if field in valid_fields
            else "fallback"
        )
        for field in ENHANCED_FIELDS
    }
    selection_reasons["treatments"] = "deterministic_baseline"
    return {
        "enhanced_prediction": enhanced,
        "generated_sections": canonical,
        "used_fallback": bool(validation_fallbacks),
        "field_results": field_results,
        "selection_reasons": selection_reasons,
        "validation_errors": list(state.get("validation_errors", [])),
        "field_fallbacks": validation_fallbacks,
        "baseline_kept_fields": baseline_kept + ["treatments"],
    }


def _after_validation(state: NarrativeState) -> str:
    # A second full prompt previously doubled token use while usually repeating
    # the same semantic error.  Invalid fields now fall back immediately.
    return "finalize"


def build_narrative_graph(
    client: OpenAIModelClient,
    retriever: Any = None,
    max_retries: int = 1,
):
    """Build and compile the five-node narrative enhancement subgraph."""

    _load_llm_types()
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")

    graph = StateGraph(NarrativeState)
    graph.add_node("prepare_context", lambda state: _prepare_context(state, max_retries))
    graph.add_node("retrieve_knowledge", lambda state: _retrieve_knowledge(state, retriever))
    graph.add_node("generate_narrative", lambda state: _generate_narrative(state, client))
    graph.add_node("validate_output", _validate_output)
    graph.add_node("finalize", _finalize)
    graph.add_edge(START, "prepare_context")
    graph.add_edge("prepare_context", "retrieve_knowledge")
    graph.add_edge("retrieve_knowledge", "generate_narrative")
    graph.add_edge("generate_narrative", "validate_output")
    graph.add_conditional_edges(
        "validate_output",
        _after_validation,
        {"generate_narrative": "generate_narrative", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile()


def run_narrative_enhancement(
    baseline_prediction: Any,
    sample_id: str,
    source_file: str,
    report_facts: Any,
    client: OpenAIModelClient,
    retriever: Any = None,
    split: str = "fit",
    *,
    facility_context: Any = None,
    field_states: Any = None,
    locked_facts: Any = None,
) -> dict[str, Any]:
    """Run narrative enhancement and return the stable public result envelope."""

    graph = build_narrative_graph(client, retriever=retriever, max_retries=0)
    baseline = _prediction_dict(baseline_prediction)
    state: NarrativeState = {
        "baseline_prediction": baseline,
        "prompt_baseline": _prompt_baseline(
            baseline,
            sample_id=sample_id,
            source_file=source_file,
        ),
        "sample_id": sample_id,
        "source_file": source_file,
        "report_facts": report_facts,
        "retrieval_results": [],
        "facility_context": facility_context if facility_context is not None else baseline.get("facility_context"),
        "field_states": field_states if field_states is not None else baseline.get("field_states", {}),
        "locked_facts": locked_facts if locked_facts is not None else baseline.get("locked_facts"),
        "generated_sections": {},
        "validation_errors": [],
        "retry_count": 0,
        "enhanced_prediction": {},
        "used_fallback": False,
        "call_metrics": _empty_metrics(),
        "split": split,
    }
    result = graph.invoke(state)
    return {
        "enhanced_prediction": result.get("enhanced_prediction", state["baseline_prediction"]),
        "retrieval_results": result.get("retrieval_results", []),
        "retrieval_by_task": result.get("retrieval_by_task", {}),
        "generated_sections": result.get("generated_sections", {}),
        "validation_errors": result.get("validation_errors", []),
        "field_results": result.get(
            "field_results",
            {field: ("fallback" if result.get("used_fallback") else "enhanced") for field in ENHANCED_FIELDS},
        ),
        "retry_count": result.get("retry_count", 0),
        "used_fallback": result.get("used_fallback", False),
        "field_fallbacks": result.get("field_fallbacks", []),
        "selection_reasons": result.get("selection_reasons", {}),
        "baseline_kept_fields": result.get("baseline_kept_fields", []),
        "call_metrics": result.get("call_metrics", _empty_metrics()),
    }
