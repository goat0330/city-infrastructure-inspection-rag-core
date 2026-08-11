"""Restricted recommendation-location mapping for Gold-schema output.

Only the ``Recommendation.location`` field may change.  Recommendation content,
category, defect description and all summary scores remain untouched.  A live
LLM client is optional; invalid model output falls back to the deterministic
location already extracted from the report.
"""

from __future__ import annotations

from dataclasses import replace
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...contracts import DefectObservation, Recommendation

_ALLOWED_BASE_LOCATIONS = (
    "桥面", "桥面铺装", "伸缩缝", "主梁", "梁底", "梁体", "腹板", "翼板",
    "桥台", "桥墩", "盖梁", "挡块", "支座", "护栏", "防撞护栏", "栏杆",
    "排水孔", "泄水孔", "泄水管", "排水设施", "防抛网", "桩基", "基础",
    "顶板", "侧墙", "前墙", "翼墙", "拱腰", "拱顶", "通道", "桥梁",
)
_ALLOWED_COMPOSITES = frozenset({"桥台、盖梁", "盖梁、挡块", "桥面、伸缩缝"})
_SIDE_PREFIXES = ("左幅", "右幅", "左侧", "右侧")
_FORBIDDEN_OUTPUT_RE = re.compile(
    r"\d|(?:m|mm|cm|㎡|m²)|距|处|建议|应|及时|修补|维修|处理|裂缝|破损|"
    r"露筋|锈蚀|渗水|泛碱|堵塞|缺失|变形|面积|宽度|长度|由于|针对|对于",
    flags=re.IGNORECASE,
)
_SUSPECT_RE = re.compile(
    r"\d|(?:m|mm|cm|㎡|m²)|距|处|由于|针对|对于|建议|应及时|可|"
    r"裂缝|破损|露筋|锈蚀|渗水|泛碱|堵塞|缺失|变形|面积|宽度|长度",
    flags=re.IGNORECASE,
)

# Oracle-10 examples are formatting/field-granularity demonstrations only.
# They do not provide facts for the current sample.
_ORACLE10_EXAMPLES = (
    ("针对主梁多处破损露筋进行修补", "主梁"),
    ("及时疏通排水孔，并加强日常养护", "排水孔"),
    ("及时清除伸缩缝内泥沙", "伸缩缝"),
    ("对其他常规缺陷加强日常巡查和养护维修", "桥梁"),
)


def legal_recommendation_locations() -> tuple[str, ...]:
    values = list(_ALLOWED_BASE_LOCATIONS)
    values.extend(sorted(_ALLOWED_COMPOSITES))
    for prefix in _SIDE_PREFIXES:
        values.extend(f"{prefix}{base}" for base in _ALLOWED_BASE_LOCATIONS if base != "桥梁")
    return tuple(dict.fromkeys(values))


def is_valid_recommendation_location(value: object) -> bool:
    text = " ".join(str(value or "").split()).strip("，,；;。 ")
    if not text or len(text) > 14 or _FORBIDDEN_OUTPUT_RE.search(text):
        return False
    return text in set(legal_recommendation_locations())


def is_suspect_recommendation_location(value: object) -> bool:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return True
    if len(text) > 14 or _SUSPECT_RE.search(text):
        return True
    if text.count("、") >= 2 or text.count("，") >= 1:
        return True
    return not is_valid_recommendation_location(text)


def _content_component(content: str) -> str:
    """Return a concise component explicitly named by the recommendation."""

    compact = "".join(str(content or "").split())
    rules = (
        (r"桥面铺装|铺装层", "桥面铺装"),
        (r"排水孔", "排水孔"),
        (r"泄水孔", "泄水孔"),
        (r"泄水管", "泄水管"),
        (r"防抛网", "防抛网"),
        (r"支座", "支座"),
        (r"盖梁.*挡块|挡块.*盖梁", "盖梁、挡块"),
        (r"盖梁", "盖梁"),
        # Slab/box-girder wording still belongs to the upper main load-bearing
        # member.  Keep the output at Gold's concise component granularity.
        (r"上部结构[^；。]{0,100}(?:\d+#(?:、\d+#)*板|板跨中|主梁|箱梁|梁)", "主梁"),
        (r"主梁|箱梁|梁体|(?<!桥)梁(?:左侧|右侧|表面)", "主梁"),
        (r"板底|底板|梁底", "梁底"),
        (r"腹板", "腹板"),
        (r"翼板", "翼板"),
        (r"防撞护栏", "防撞护栏"),
        (r"防撞栏杆|护栏", "护栏"),
        (r"栏杆", "栏杆"),
        (r"桥台|台前墙|台帽|(?:\d+(?:-\d+)?#)?台(?:顶|顶部)?", "桥台"),
        (r"桥墩|墩柱|墩身|(?:\d+(?:-\d+)?#)?墩(?:顶|顶部)?", "桥墩"),
        (r"伸缩缝", "伸缩缝"),
        (r"桩基", "桩基"),
        (r"拱腰", "拱腰"),
        (r"拱顶", "拱顶"),
        (r"顶板", "顶板"),
        (r"侧墙", "侧墙"),
        (r"翼墙", "翼墙"),
        (r"桥面", "桥面"),
        (r"桥梁|该桥|本桥", "桥梁"),
    )
    for pattern, value in rules:
        if re.search(pattern, compact):
            return value
    return ""


def deterministic_recommendation_location(
    recommendation: Recommendation,
    *,
    facility_noun: str = "桥梁",
) -> str:
    """Repair only obviously over-captured locations using source wording."""

    current = " ".join(str(recommendation.location or "").split()).strip()
    if not is_suspect_recommendation_location(current):
        return current
    candidate = _content_component(recommendation.content)
    if candidate and is_valid_recommendation_location(candidate):
        return candidate
    fallback = facility_noun if is_valid_recommendation_location(facility_noun) else "桥梁"
    return current if current else fallback


def _tokens(value: str) -> set[str]:
    compact = "".join(str(value or "").split())
    return {
        token
        for token in (
            "桥面铺装", "伸缩缝", "排水孔", "泄水孔", "泄水管", "防抛网",
            "支座", "盖梁", "桥台", "桥墩", "梁底", "腹板", "翼板", "主梁",
            "箱梁", "护栏", "栏杆", "桩基", "拱腰", "拱顶", "顶板", "侧墙",
            "裂缝", "破损", "露筋", "锈蚀", "渗水", "泛碱", "堵塞", "缺失",
        )
        if token in compact
    }


def _related_defects(
    recommendation: Recommendation,
    defects: Sequence[DefectObservation],
    *,
    limit: int = 3,
) -> list[dict[str, str]]:
    rec_tokens = _tokens(recommendation.content)
    ranked: list[tuple[int, int, DefectObservation]] = []
    for order, defect in enumerate(defects):
        defect_text = " ".join(
            (defect.location, defect.defect_type, defect.description)
        )
        overlap = len(rec_tokens & _tokens(defect_text))
        if overlap:
            ranked.append((-overlap, order, defect))
    result: list[dict[str, str]] = []
    for _, _, defect in sorted(ranked)[:limit]:
        result.append(
            {
                "location": defect.location,
                "defect_type": defect.defect_type,
                "description": defect.description,
            }
        )
    return result


def _prompt_payload(
    recommendations: Sequence[Recommendation],
    defects: Sequence[DefectObservation],
    facility_noun: str,
) -> dict[str, object]:
    items = []
    for idx, recommendation in enumerate(recommendations):
        if not is_suspect_recommendation_location(recommendation.location):
            continue
        items.append(
            {
                "item_id": idx,
                "original_recommendation": recommendation.content,
                "original_location": recommendation.location,
                "related_defects": _related_defects(recommendation, defects),
            }
        )
    return {
        "facility_type": facility_noun,
        "legal_locations": list(legal_recommendation_locations()),
        "oracle10_examples": [
            {"recommendation": source, "location": location}
            for source, location in _ORACLE10_EXAMPLES
        ],
        "items": items,
    }


def _model_locations(client: Any, payload: Mapping[str, object]) -> dict[int, str]:
    if client is None or not payload.get("items"):
        return {}
    messages = [
        {
            "role": "system",
            "content": (
                "你只做维护建议location字段映射。只能从legal_locations中选择简洁构件部位；"
                "不得改写建议正文，不得输出病害描述、动作、尺寸、编号或解释。"
                "Oracle示例仅用于字段粒度，不是当前样本事实。"
                "只输出JSON：{\"locations\":[{\"item_id\":0,\"location\":\"主梁\"}]}。"
            ),
        },
        {"role": "user", "content": __import__("json").dumps(payload, ensure_ascii=False)},
    ]
    try:
        result = client.chat_json(messages, temperature=0, max_tokens=800)
        value = getattr(result, "value", result)
    except Exception:
        return {}
    if not isinstance(value, Mapping):
        return {}
    locations = value.get("locations")
    if not isinstance(locations, Sequence) or isinstance(locations, (str, bytes)):
        return {}
    mapped: dict[int, str] = {}
    for item in locations:
        if not isinstance(item, Mapping):
            continue
        try:
            item_id = int(item.get("item_id"))
        except (TypeError, ValueError):
            continue
        location = " ".join(str(item.get("location", "") or "").split()).strip()
        if is_valid_recommendation_location(location):
            mapped[item_id] = location
    return mapped


def map_recommendation_locations(
    recommendations: Sequence[Recommendation],
    defects: Sequence[DefectObservation],
    *,
    facility_noun: str = "桥梁",
    client: Any = None,
) -> tuple[Recommendation, ...]:
    """Map only suspect locations; invalid LLM output preserves deterministic facts."""

    records = tuple(recommendations)
    payload = _prompt_payload(records, defects, facility_noun)
    model_values = _model_locations(client, payload)
    output: list[Recommendation] = []
    for index, recommendation in enumerate(records):
        current = recommendation.location
        deterministic = deterministic_recommendation_location(
            recommendation, facility_noun=facility_noun
        )
        if not is_suspect_recommendation_location(current):
            output.append(recommendation)
            continue
        model_value = model_values.get(index, "")
        location = model_value if is_valid_recommendation_location(model_value) else deterministic
        # If deterministic fallback is still long/invalid, preserve the exact
        # original result rather than inventing a component.
        if not is_valid_recommendation_location(location):
            location = current
        output.append(replace(recommendation, location=location))
    return tuple(output)
