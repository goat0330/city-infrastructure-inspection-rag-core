"""Evidence-first optional composer for competition-facing narrative.

The production pipeline does not enable this module by default.  When it is
used for an explicit A/B experiment, it may only reorganise text already
present in the report or in deterministic recommendation rows.  It never
manufactures engineering causes, consequences, dates, scores, grades, defects,
or recommendation counts.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from .summary.facility_context import FacilityContext

_NONE_VALUES = frozenset({"", "无", "暂无", "未提供", "未提取到", "不适用", "无此项"})
_DEFECT_TERMS = (
    "病害", "裂缝", "破损", "剥落", "脱落", "露筋", "锈蚀", "锈胀",
    "渗水", "浸水", "泛碱", "堵塞", "积水", "积泥", "沉积物",
    "车辙", "坑槽", "沉降", "变形", "松动", "缺失", "蜂窝", "麻面",
    "滑痕", "刮痕", "堆积", "凸起", "错位", "脱空", "空洞", "漏浆",
    "离析", "不密实", "磨损", "鼓起", "脱漆", "涂层破损", "构件", "结构",
)
_CAUSE_CONNECTORS = (
    "由于", "因为", "主要是由于", "主要由于", "原因是", "原因为",
    "导致", "造成", "受", "所致",
)
_CAUSE_NOISE = (
    "检测不得", "检测过程中", "试验过程中", "原样恢复", "检测方法",
    "评定方法", "规范要求", "主要结论", "综合评估", "安全性评估",
)
_SAFETY_WORDS = (
    "影响", "降低", "削弱", "危及", "不影响", "暂不影响",
    "满足设计荷载", "承载能力满足", "结构安全", "耐久性", "通行安全",
)
_SAFETY_NOISE = (
    "截面号", "计算模型", "计算结果表", "校验系数", "实测值", "理论值",
    "检测方法", "评定方法", "规范", "试验加载", "输入参数", "软件计算",
)


@dataclass(frozen=True)
class OfficialAnswers:
    overall_conclusion: str
    risk_points: str
    detailed_conclusion: tuple[str, str, str, str]
    causes: tuple[str, ...]
    treatments: tuple[str, ...]
    safety_impact: tuple[str, ...]
    history_status: str


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split()).strip()


def _value(item: object, name: str) -> str:
    if isinstance(item, Mapping):
        return _text(item.get(name, ""))
    return _text(getattr(item, name, ""))


def _present(value: object) -> bool:
    return _text(value) not in _NONE_VALUES


def _clean_sentence(value: object, *, max_chars: int = 420) -> str:
    text = _text(value)
    text = re.sub(r"^\s*(?:[（(]?\d+[）).、．:]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", "", text)
    text = text.strip("，,；;。． ")
    return text[:max_chars].rstrip("，,；;。． ")


def _source_causes(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = _clean_sentence(value, max_chars=300)
        if not text or any(noise in text for noise in _CAUSE_NOISE):
            continue
        if not any(term in text for term in _DEFECT_TERMS):
            continue
        if not any(connector in text for connector in _CAUSE_CONNECTORS):
            continue
        if text not in result:
            result.append(text)
        if len(result) == 6:
            break
    return tuple(result)


def _treatment_texts(recommendations: Sequence[object]) -> tuple[str, ...]:
    result: list[str] = []
    for item in recommendations:
        content = _clean_sentence(_value(item, "content"), max_chars=320)
        if content and content not in result:
            result.append(content)
    return tuple(result)


def _explicit_safety_impacts(document_text: str) -> tuple[str, ...]:
    result: list[str] = []
    for part in re.split(r"(?<=[。；;！？!?])|[\r\n]+", document_text or ""):
        text = _clean_sentence(part, max_chars=360)
        if not 12 <= len(text) <= 360:
            continue
        if any(marker in text for marker in _SAFETY_NOISE):
            continue
        if not any(marker in text for marker in _SAFETY_WORDS):
            continue
        if not any(marker in text for marker in _DEFECT_TERMS):
            continue
        sentence = text + "。"
        if sentence not in result:
            result.append(sentence)
        if len(result) == 4:
            break
    return tuple(result)


def _history_status(summary: object) -> str:
    previous_score = _value(summary, "previous_overall_score")
    previous_grade = _value(summary, "previous_overall_grade")
    current_score = _value(summary, "overall_score")
    current_grade = _value(summary, "overall_grade")

    previous_parts: list[str] = []
    current_parts: list[str] = []
    if _present(previous_score):
        previous_parts.append(f"评分{previous_score}")
    if _present(previous_grade):
        previous_parts.append(f"等级{previous_grade}")
    if _present(current_score):
        current_parts.append(f"评分{current_score}")
    if _present(current_grade):
        current_parts.append(f"等级{current_grade}")

    if previous_parts and current_parts:
        return f"上一次定检{'、'.join(previous_parts)}；本次{'、'.join(current_parts)}。"
    if previous_parts:
        return f"上一次定检{'、'.join(previous_parts)}。"
    return ""


def compose_official_answers(
    *,
    summary: object,
    defects: Sequence[object],
    recommendations: Sequence[object],
    facility_context: FacilityContext,
    source_causes: Sequence[str] = (),
    document_text: str = "",
) -> OfficialAnswers:
    """Return only report-grounded optional narrative fields.

    ``defects`` and ``facility_context`` stay in the signature for contract
    compatibility, but they are not used to invent prose.  Missing evidence
    remains missing.
    """

    del defects, facility_context
    overall = _text(_value(summary, "overall_conclusion"))[:700]
    risks = _text(_value(summary, "risk_points"))[:500]
    detailed_values = [value for value in (overall, risks) if value]
    detailed = tuple((detailed_values + ["", "", "", ""])[:4])
    return OfficialAnswers(
        overall_conclusion=overall,
        risk_points=risks,
        detailed_conclusion=detailed,  # type: ignore[arg-type]
        causes=_source_causes(source_causes),
        treatments=_treatment_texts(recommendations),
        safety_impact=_explicit_safety_impacts(document_text),
        history_status=_history_status(summary),
    )
