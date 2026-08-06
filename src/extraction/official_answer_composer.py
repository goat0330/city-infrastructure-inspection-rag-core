"""Compose competition-facing narrative from deterministic extraction facts.

Extractors own facts.  This module owns only the wording used by the official
summary table and narrative sections.  It never changes dates, scores, grades,
defect rows, recommendation rows, or recommendation counts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from .summary.facility_context import FacilityContext

_NONE_VALUES = frozenset({"", "无", "暂无", "未提供", "未提取到", "不适用", "无此项"})
_INTERNAL_TERMS = (
    "未提取到",
    "结构化病害记录",
    "记录",
    "典型部位为",
    "按结构部位归纳",
    "检测结果见表",
    "详见表",
    "详见图",
    "见表",
    "见图",
)
_ACTION_TERMS = (
    "建议",
    "处治",
    "修复",
    "维修",
    "更换",
    "清理",
    "加强养护",
    "加固",
    "封闭",
    "灌浆",
    "重新铺装",
    "设置",
    "增设",
)
_DEFECT_TERMS = (
    "裂缝",
    "破损",
    "剥落",
    "脱落",
    "露筋",
    "锈蚀",
    "锈胀",
    "渗水",
    "浸水",
    "泛碱",
    "堵塞",
    "沉积物",
    "积水",
    "积泥",
    "车辙",
    "坑槽",
    "沉降",
    "变形",
    "松动",
    "缺失",
    "蜂窝",
    "麻面",
    "滑痕",
    "刮痕",
    "堆积",
    "凸起",
    "错位",
    "脱空",
    "空洞",
    "漏浆",
    "离析",
    "不密实",
    "磨损",
    "鼓起",
    "火烧",
    "脱漆",
    "油漆脱落",
    "涂层破损",
)
_CAUSE_CONNECTORS = ("由于", "因为", "主要是由于", "主要由于", "原因是", "原因为", "导致", "造成")
_CAUSE_NOISE = (
    "桥面系目前能够满足功能要求",
    "目前能够满足功能要求",
    "主要结论",
    "外观检测结果显示",
    "外观检查结果显示",
    "综合评估",
    "安全性评估",
    "检测不得",
    "检测过程中",
    "试验过程中",
    "原样恢复",
)

# Specific components are deliberately short. They turn row-level facts
# into official report phrases such as "桥面铺装破损" instead of repeating
# location, defect type, and the full description in the same sentence.
_BRIDGE_COMPONENTS: dict[str, tuple[str, ...]] = {
    "上部结构": (
        "湿接缝",
        "铰接缝",
        "铰缝",
        "横隔板",
        "腹板",
        "翼缘板",
        "翼板",
        "梁底",
        "底板",
        "空心板",
        "箱梁",
        "主梁",
        "梁体",
        "上部结构",
    ),
    "下部结构": (
        "支座垫石",
        "支座垫板",
        "支座",
        "盖梁挡块",
        "挡块",
        "盖梁",
        "台帽",
        "桥台前墙",
        "前墙",
        "桥台",
        "侧墙",
        "桥墩",
        "墩柱",
        "下部结构",
    ),
    "桥面系": (
        "桥面铺装",
        "伸缩缝保护带",
        "伸缩缝止水带",
        "伸缩缝",
        "防撞护栏",
        "防撞栏杆",
        "护栏",
        "栏杆",
        "泄水孔",
        "泄水管",
        "排水设施",
        "标识牌",
        "反光设施",
        "防护网",
        "限高牌",
        "人行道",
        "车行道",
        "桥面",
        "路面",
        "铺装",
        "桥面系",
    ),
}


@dataclass(frozen=True)
class OfficialAnswers:
    overall_conclusion: str
    risk_points: str
    detailed_conclusion: tuple[str, str, str, str]
    causes: tuple[str, ...]
    treatments: tuple[str, ...]
    safety_impact: tuple[str, ...]
    history_status: str


@dataclass(frozen=True)
class _SystemFacts:
    label: str
    phrases: tuple[str, ...]
    source_texts: tuple[str, ...]


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split()).strip()


def _value(item: object, name: str) -> str:
    if isinstance(item, Mapping):
        return _text(item.get(name, ""))
    return _text(getattr(item, name, ""))


def _present(value: object) -> bool:
    return _text(value) not in _NONE_VALUES


def _is_underpass(context: FacilityContext) -> bool:
    return context.facility_type in {
        "pedestrian_underpass",
        "vehicle_underpass",
        "underpass",
        "pedestrian_passage",
    }


def _subject(context: FacilityContext) -> str:
    # The official sample uses the concise report pronoun "该桥" for bridge-like
    # facilities. Underpasses and other facilities retain their own noun.
    if context.facility_type == "bridge" or context.facility_noun in {"桥梁", "人行天桥", "天桥"}:
        return "该桥"
    noun = context.facility_noun or "设施"
    if noun.startswith(("该", "本")):
        return noun
    return f"该{noun}"


def _history_noun(context: FacilityContext) -> str:
    if context.facility_type == "bridge" or context.facility_noun in {"桥梁", "人行天桥", "天桥"}:
        return "桥梁"
    return context.facility_noun or "设施"


def _clean_sentence(value: object, *, max_chars: int = 420) -> str:
    text = _text(value)
    text = re.sub(r"^\s*(?:主要结论|总体结论|检测结论|外观检测结果显示|外观检查结果显示)\s*[:：]?\s*", "", text)
    text = re.sub(r"(?:^|\s)(?:\d+(?:\.\d+)+|[（(]?\d+[）)])\s*", "", text)
    text = re.sub(r"(?:表|图)\s*\d+(?:[.\-]\d+)*", "", text)
    for term in _INTERNAL_TERMS:
        text = text.replace(term, "")
    text = re.sub(r"[：:]\s*[：:]", "：", text)
    text = re.sub(r"[，,；;。．]{2,}", "；", text)
    text = text.strip("，,；;。．：: ")
    if len(text) > max_chars:
        text = text[:max_chars].rstrip("，,；;。．：: ")
    return text


def _facility_systems(context: FacilityContext) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if _is_underpass(context):
        return (
            ("顶板", ("顶板",)),
            ("侧墙及翼墙", ("侧墙", "翼墙", "墙体")),
            ("洞口及接缝止水", ("洞口", "沉降缝", "止水带", "接缝")),
            ("排水和附属设施", ("排水", "出入口", "栏杆", "附属")),
        )
    if context.facility_type == "tunnel":
        return (
            ("洞口", ("洞口", "洞门")),
            ("衬砌及主体结构", ("衬砌", "拱部", "边墙", "仰拱")),
            ("路面及防排水", ("路面", "排水", "渗水", "止水")),
            ("附属设施", ("照明", "通风", "附属")),
        )
    if context.facility_type == "culvert":
        return (
            ("主体结构", ("顶板", "侧墙", "翼墙", "涵身")),
            ("洞口及排水", ("洞口", "排水", "积水", "堵塞")),
            ("附属设施", ("栏杆", "附属")),
        )
    if context.facility_type == "road":
        return (
            ("路面", ("路面", "铺装", "车辙", "坑槽")),
            ("路基及边坡", ("路基", "边坡", "沉降")),
            ("排水及防护设施", ("排水", "边沟", "防护", "护栏")),
        )
    # Pedestrian overpasses use the bridge three-part wording in Gold.
    return tuple((label, markers) for label, markers in _BRIDGE_COMPONENTS.items())


def _strip_measurement_tail(value: str) -> str:
    text = value
    text = re.sub(r"[，,；;]\s*(?:L|W|S|B|H|D)\s*=.*$", "", text, flags=re.I)
    text = re.sub(r"[，,；;]\s*(?:长|宽|面积|深度)\s*[约为]?[0-9].*$", "", text)
    text = re.sub(r"[，,]\s*(?:见)?(?:照片|附图|图)\s*[\w.\-]+\s*$", "", text)
    return text.strip("，,；;。． ")


def _canonical_defect_type(defect_type: str, description: str) -> str:
    source = f"{defect_type} {description}"
    combos = (
        ("渗水泛碱", ("渗水泛碱", "浸水泛碱")),
        ("破损露筋锈蚀", ("破损露筋锈蚀", "露筋锈蚀")),
        ("蜂窝麻面", ("蜂窝麻面",)),
        ("混凝土不密实", ("混凝土不密实", "不密实")),
        ("剪切变形", ("剪切变形",)),
        ("沉积物堵塞", ("沉积物阻塞", "沉积物堵塞")),
        ("泥沙堆积", ("泥沙堆积", "泥沙覆盖")),
        ("杂物堆积", ("杂物堆积", "碎石堆积")),
    )
    for canonical, markers in combos:
        if any(marker in source for marker in markers):
            return canonical
    if "阻塞" in source or "堵塞" in source:
        return "堵塞"
    cleaned = re.sub(r"\s*[,，/＋+]\s*", "、", defect_type).strip("、，,；;。． ")
    generic_types = {
        "其他", "病害", "缺陷", "现状", "外观", "完好", "正常",
        "修补", "加固", "维修", "处治",
    }
    if cleaned and cleaned not in generic_types and any(
        marker in cleaned for marker in _DEFECT_TERMS
    ):
        return cleaned
    for marker in _DEFECT_TERMS:
        if marker in description:
            return marker
    return ""


def _type_parts(value: str) -> tuple[str, ...]:
    parts = [part.strip() for part in re.split(r"[、,，/＋+]+", value) if part.strip()]
    return tuple(dict.fromkeys(parts))


def _specific_component(label: str, location: str, description: str) -> str:
    text = f"{location} {description}"
    if label in _BRIDGE_COMPONENTS:
        for component in _BRIDGE_COMPONENTS[label]:
            if component in text:
                return "桥面铺装" if component in {"铺装", "桥面", "路面", "车行道"} and label == "桥面系" else component
    # Non-bridge labels are already specific enough; retain a short observed
    # component when available, otherwise use the system label.
    for component in (
        "顶板", "侧墙", "翼墙", "洞口", "沉降缝", "止水带", "排水设施",
        "衬砌", "拱部", "边墙", "仰拱", "路面", "路基", "边坡", "栏杆",
    ):
        if component in text:
            return component
    return label


def _system_label_for(text: str, systems: Sequence[tuple[str, tuple[str, ...]]]) -> str:
    # Match specific bridge markers first so a phrase containing "桥台路面"
    # is assigned by its actual disease object rather than its coordinate text.
    if systems and systems[0][0] == "上部结构":
        for label in ("桥面系", "上部结构", "下部结构"):
            if any(marker in text for marker in _BRIDGE_COMPONENTS[label]):
                return label
    for label, markers in systems:
        if any(marker in text for marker in markers):
            return label
    return ""


def _group_defects(defects: Sequence[object], context: FacilityContext) -> tuple[_SystemFacts, ...]:
    systems = _facility_systems(context)
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    source_texts: dict[str, list[str]] = defaultdict(list)
    for item in defects:
        location = _value(item, "location")
        defect_type = _value(item, "defect_type")
        description = _strip_measurement_tail(_clean_sentence(_value(item, "description"), max_chars=180))
        joined = f"{location} {defect_type} {description}"
        label = _system_label_for(joined, systems)
        if not label:
            # Do not create the non-Gold "其他部位" class.  For bridge-like
            # facilities, ancillary and traffic-safety observations belong to
            # the bridge deck system; unrelated rows are omitted from prose.
            if systems and systems[0][0] == "上部结构" and any(
                marker in joined for marker in ("标识", "反光", "防护", "限高", "排水", "泄水")
            ):
                label = "桥面系"
            else:
                continue
        canonical_type = _canonical_defect_type(defect_type, description)
        if not canonical_type:
            continue
        component = _specific_component(label, location, description)
        for part in _type_parts(canonical_type):
            if part not in grouped[label][component]:
                grouped[label][component].append(part)
        if description and description not in source_texts[label]:
            source_texts[label].append(description)

    result: list[_SystemFacts] = []
    for label, _ in systems:
        component_map = grouped.get(label, {})
        phrases: list[str] = []
        for component, types in component_map.items():
            unique_types = list(dict.fromkeys(types))[:4]
            phrase = component + "、".join(unique_types)
            if phrase not in phrases:
                phrases.append(phrase)
        if phrases:
            result.append(_SystemFacts(label, tuple(phrases[:6]), tuple(source_texts[label][:8])))
    return tuple(result)


def _facts_by_label(groups: Sequence[_SystemFacts]) -> dict[str, _SystemFacts]:
    return {group.label: group for group in groups}



def _ordered_groups(groups: Sequence[_SystemFacts], *, mode: str) -> tuple[_SystemFacts, ...]:
    mapping = _facts_by_label(groups)
    if set(mapping).intersection({"上部结构", "下部结构", "桥面系"}):
        # The official sample presents summary/current-state facts as
        # upper structure → substructure → deck system. Safety impact starts
        # from the deck system because it first explains traffic and drainage.
        order = (
            ("桥面系", "上部结构", "下部结构")
            if mode == "safety"
            else ("上部结构", "下部结构", "桥面系")
        )
        return tuple(mapping[label] for label in order if label in mapping)
    return tuple(groups)

def _group_text(group: _SystemFacts, *, limit: int = 5) -> str:
    return "、".join(group.phrases[:limit])


def _score_paragraph(summary: object, subject: str) -> str:
    score = _value(summary, "overall_score")
    grade = _value(summary, "overall_grade")
    if _present(score) and _present(grade):
        paragraph = f"经综合评定，{subject}总体技术状况评分 {score} 分，总体技术状况等级为 {grade}。"
    elif _present(grade):
        paragraph = f"经综合评定，{subject}总体技术状况等级为 {grade}。"
    else:
        return "该文档无总体技术状况评分和总体技术状况等级。"

    details: list[str] = []
    for label, score_name, grade_name in (
        ("上部结构", "superstructure_score", "superstructure_grade"),
        ("下部结构", "substructure_score", "substructure_grade"),
        ("桥面系", "deck_score", "deck_grade"),
    ):
        component_score = _value(summary, score_name)
        component_grade = _value(summary, grade_name)
        if _present(component_score) and _present(component_grade):
            details.append(f"{label}评分 {component_score} 分（{component_grade}）")
        elif _present(component_grade):
            details.append(f"{label}等级为 {component_grade}")
    if details:
        paragraph += "其中，" + "，".join(details) + "。"
    return paragraph



def _history_status(summary: object, context: FacilityContext, document_text: str) -> str:
    compact = _text(document_text)
    previous = (_value(summary, "previous_overall_score"), _value(summary, "previous_overall_grade"))
    trend = _value(summary, "trend")
    if any(_present(value) for value in previous) or (_present(trend) and trend != "无"):
        return "historical_comparison_available"
    if re.search(r"与\s*20\d{2}年.*相比|上次检测|往年检测|历次检测|上一次定检", compact):
        return "historical_comparison_available"
    if re.search(
        r"首次(?:定期|系统)?(?:检测|检查|评估)|第一次(?:定期)?检测|初次(?:定期)?检测",
        compact,
    ):
        return "first_inspection_confirmed"
    return "historical_comparison_missing"


def _history_paragraph(
    summary: object,
    groups: Sequence[_SystemFacts],
    context: FacilityContext,
    document_text: str,
) -> tuple[str, str]:
    status = _history_status(summary, context, document_text)
    noun = _history_noun(context)
    facts = [
        f"{group.label}主要存在{_group_text(group, limit=5)}"
        for group in _ordered_groups(groups, mode="history")
    ]
    defect_text = "；".join(facts) if facts else "本次检测未见明显病害"
    if status == "historical_comparison_available":
        trend = _clean_sentence(_value(summary, "trend"), max_chars=220)
        prefix = (
            trend + "。本次检测病害具体表现为："
            if trend
            else "本次报告提供了历史检测或病害对比信息，检测病害具体表现为："
        )
    elif status == "first_inspection_confirmed":
        prefix = (
            f"本次为{noun}首次定期检测，无往年检测评分、病害对比数据，"
            "不存在既有病害扩展情况，检测病害具体表现为："
        )
    else:
        prefix = (
            "本次报告未提供往年检测评分及病害对比数据，无法开展跨期变化比较。"
            "检测病害具体表现为："
        )
    return prefix + defect_text + "。", status

def _capacity_met(summary: object, document_text: str) -> bool:
    text = f"{_value(summary, 'overall_conclusion')} {_text(document_text)}"
    return bool(re.search(r"承载能力[^。；]{0,40}满足|满足[^。；]{0,30}设计荷载|结构(?:强度|刚度)[^。；]{0,30}满足", text))


def _grade_state(grade: str) -> str:
    compact = grade.replace(" ", "")
    if compact.startswith("A") or compact == "一类":
        return "完好状态"
    if compact.startswith("B") or compact == "二类":
        return "良好状态"
    if compact.startswith("C") or compact == "三类":
        return "合格状态"
    if compact.startswith("D") or compact == "四类":
        return "较差状态"
    return ""



def _overall_conclusion(
    groups: Sequence[_SystemFacts],
    summary: object,
    context: FacilityContext,
    document_text: str,
) -> str:
    clauses = [
        f"{group.label}主要存在{_group_text(group, limit=6)}"
        for group in _ordered_groups(groups, mode="summary")
    ]
    subject = _subject(context)
    body = "；".join(clauses) if clauses else f"{subject}本次检测未见明显病害"
    grade = _value(summary, "overall_grade")
    conclusions: list[str] = [f"本次定检结果表明，{body}。"]
    if _capacity_met(summary, document_text):
        conclusions.append(f"{subject}主体结构承载能力满足设计荷载要求")
        if _present(grade):
            conclusions[-1] += f"，总体技术状况等级为{grade}"
        conclusions[-1] += "。"
    elif _present(grade):
        state = _grade_state(grade)
        if state:
            conclusions.append(f"{subject}总体技术状况等级为{grade}（{state}）。")
        else:
            conclusions.append(f"{subject}总体技术状况等级为{grade}。")
    return "".join(conclusions)

def _risk_effect(text: str) -> str:
    if any(term in text for term in ("支座", "剪切变形", "脱空", "错位")):
        return "持续劣化可能影响支座正常工作和结构受力"
    if any(term in text for term in ("护栏", "栏杆", "防撞", "防护网")):
        return "持续发展会削弱防护功能并影响通行安全"
    if any(term in text for term in ("伸缩缝", "堵塞", "积水", "排水", "泄水")):
        return "会影响桥面排水和伸缩功能，并加剧雨水下渗"
    if any(term in text for term in ("露筋", "锈蚀", "剥落", "渗水", "浸水", "泛碱")):
        return "长期发展会加速钢筋锈蚀并降低结构耐久性"
    if any(term in text for term in ("裂缝", "破损", "车辙", "坑槽")):
        return "若不及时处理，会影响使用功能并降低构件耐久性"
    return "持续发展可能降低结构耐久性和正常使用功能"


def _risk_points(groups: Sequence[_SystemFacts]) -> str:
    candidates: list[str] = []
    # Prefer high-impact bridge components before ordinary surface damage.
    priority = ("支座", "露筋", "锈蚀", "裂缝", "护栏", "伸缩缝", "渗水", "破损", "堵塞")
    phrases = [phrase for group in groups for phrase in group.phrases]
    phrases.sort(key=lambda value: next((i for i, marker in enumerate(priority) if marker in value), len(priority)))
    for phrase in phrases:
        if not any(term in phrase for term in _DEFECT_TERMS):
            continue
        sentence = f"{phrase}，{_risk_effect(phrase)}"
        if sentence not in candidates:
            candidates.append(sentence)
        if len(candidates) == 4:
            break
    result = "；".join(candidates) if candidates else "现有局部病害持续发展可能影响结构耐久性和正常使用功能"
    for action in _ACTION_TERMS:
        result = result.replace(action, "")
    return result.strip("，,；;。 ") + "。"



def _current_state(
    groups: Sequence[_SystemFacts],
    context: FacilityContext,
    document_text: str,
) -> str:
    subject = _subject(context)
    parts: list[str] = []
    for group in _ordered_groups(groups, mode="current"):
        facts = _group_text(group, limit=5)
        if facts:
            parts.append(f"{group.label}主要存在{facts}")
    if not parts:
        return f"目前，{subject}未见明显病害。"
    paragraph = f"目前，{subject}" + "；".join(parts) + "。"
    joined = "、".join(phrase for group in groups for phrase in group.phrases)
    if any(
        marker in joined
        for marker in ("露筋", "锈蚀", "剥落", "渗水", "泛碱", "裂缝", "破损")
    ):
        paragraph += "现有病害以局部耐久性和使用功能损伤为主。"
    return paragraph

def _treatment_texts(recommendations: Sequence[object]) -> tuple[str, ...]:
    result: list[str] = []
    for item in recommendations:
        content = _clean_sentence(_value(item, "content"), max_chars=320)
        if content and content not in result:
            result.append(content)
    return tuple(result)


def _problem_labels(groups: Sequence[_SystemFacts]) -> str:
    labels = [group.label for group in _ordered_groups(groups, mode="current")]
    if not labels:
        return "局部构件"
    if len(labels) == 1:
        return labels[0]
    return "、".join(labels[:-1]) + "及" + labels[-1]


def _top_defects(groups: Sequence[_SystemFacts], limit: int = 5) -> str:
    phrases = [phrase for group in _ordered_groups(groups, mode="current") for phrase in group.phrases]
    return "、".join(phrases[:limit])



def _judgement_risk(groups: Sequence[_SystemFacts]) -> str:
    phrases = [phrase for group in groups for phrase in group.phrases]
    clauses: list[str] = []
    if any(
        marker in phrase
        for phrase in phrases
        for marker in ("露筋", "锈蚀", "剥落", "渗水", "浸水", "泛碱", "蜂窝", "麻面")
    ):
        clauses.append("加速钢筋锈蚀和材料劣化，降低结构耐久性")
    if any(
        marker in phrase
        for phrase in phrases
        for marker in ("支座", "剪切变形", "脱空", "错位")
    ):
        clauses.append("影响支座正常工作和结构传力")
    if any(
        marker in phrase
        for phrase in phrases
        for marker in ("桥面铺装", "伸缩缝", "堵塞", "车辙", "护栏", "栏杆", "排水")
    ):
        clauses.append("影响桥面排水、伸缩、防护或通行功能")
    if not clauses:
        clauses.append("降低结构耐久性和正常使用功能")
    return "，并".join(clauses[:2])


def _judgement(
    summary: object,
    context: FacilityContext,
    groups: Sequence[_SystemFacts],
    document_text: str,
) -> str:
    subject = _subject(context)
    grade = _value(summary, "overall_grade")
    grade_state = _grade_state(grade)
    top_defects = _top_defects(groups, limit=3)
    problem_labels = _problem_labels(groups)

    if _capacity_met(summary, document_text):
        opening = f"综上，{subject}主体结构承载能力满足设计荷载要求"
        if _present(grade):
            opening += f"，总体技术状况等级为{grade}"
        opening += "。"
    elif _present(grade):
        opening = f"综上，{subject}总体技术状况等级为{grade}"
        if grade_state:
            opening += f"（{grade_state}）"
        opening += "。"
    else:
        opening = f"综上，{subject}当前总体状态尚可。"

    if top_defects:
        opening += (
            f"较突出病害为{top_defects}。上述病害持续发展可能"
            f"{_judgement_risk(groups)}。"
        )
    elif groups:
        opening += (
            f"{problem_labels}存在局部病害，持续发展可能影响结构耐久性和正常使用功能。"
        )

    opening += "建议结合建议明细优先处治突出病害，并加强日常检查和养护管理。"
    return opening


def _source_causes(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = _clean_sentence(value, max_chars=300)
        if not text:
            continue
        if any(noise in text for noise in _CAUSE_NOISE):
            continue
        if not any(term in text for term in _DEFECT_TERMS):
            continue
        if not any(connector in text for connector in _CAUSE_CONNECTORS):
            continue
        if text not in result:
            result.append(text)
    return tuple(result[:6])


def _observed_objects(groups: Sequence[_SystemFacts], markers: Sequence[str], limit: int = 4) -> str:
    phrases = [phrase for group in groups for phrase in group.phrases if any(marker in phrase for marker in markers)]
    return "、".join(phrases[:limit])


def _causes(groups: Sequence[_SystemFacts], source_causes: Sequence[str]) -> tuple[str, ...]:
    explicit = _source_causes(source_causes)
    if explicit:
        return explicit
    result: list[str] = []
    rules = (
        (("铺装", "裂缝", "破损", "车辙", "坑槽"), "车辆荷载长期作用、温度变化及材料老化共同影响"),
        (("露筋", "锈蚀", "剥落", "蜂窝", "麻面", "不密实", "离析"), "混凝土保护层破损、施工密实性不足及长期环境侵蚀"),
        (("渗水", "浸水", "泛碱"), "防排水不畅、接缝密封老化或雨水长期下渗"),
        (("堵塞", "沉积物", "堆积", "积水", "积泥"), "杂物沉积、清理维护不及时及排水设施功能下降"),
        (("支座", "变形", "错位", "脱空"), "长期荷载作用、材料老化及梁体位移"),
        (("裂缝", "开裂"), "车辆荷载、温度收缩、材料老化及局部受力共同作用"),
    )
    used: set[str] = set()
    for markers, reason in rules:
        objects = _observed_objects(groups, markers)
        if not objects or objects in used:
            continue
        result.append(f"{objects}主要是由于{reason}。")
        used.add(objects)
        if len(result) == 6:
            break
    return tuple(result) or ("现有病害成因需结合现场复核和后续检测综合判断。",)



def _safety_impacts(
    groups: Sequence[_SystemFacts],
    summary: object,
    context: FacilityContext,
    document_text: str,
) -> tuple[str, ...]:
    result: list[str] = []
    for group in _ordered_groups(groups, mode="safety"):
        facts = _group_text(group, limit=5)
        if not facts:
            continue
        if group.label == "桥面系":
            result.append(
                f"桥面系现状病害主要表现为{facts}，可能影响桥面平整度、排水、伸缩及防护功能；"
                "病害持续发展会降低通行舒适性和使用安全。"
            )
        elif group.label == "上部结构":
            if _capacity_met(summary, document_text):
                result.append(
                    f"上部结构主要存在{facts}。当前承载能力满足设计荷载要求，"
                    "但相关病害长期发展可能加速材料劣化并降低结构耐久性。"
                )
            else:
                result.append(
                    f"上部结构主要存在{facts}，相关病害长期发展可能削弱结构整体性，"
                    "降低构件耐久性并对承载性能产生不利影响。"
                )
        elif group.label == "下部结构":
            result.append(
                f"下部结构主要存在{facts}，持续发展可能影响支座、桥墩或桥台的正常工作，"
                "降低结构耐久性并影响传力状态。"
            )
        elif any(marker in group.label for marker in ("栏杆", "附属", "防护")):
            result.append(
                f"{group.label}主要存在{facts}，可能削弱防护或附属功能并增加通行风险。"
            )
        else:
            result.append(f"{group.label}主要存在{facts}，{_risk_effect(facts)}。")

    subject = _subject(context)
    if _capacity_met(summary, document_text):
        overall = (
            f"综合来看，{subject}当前承载能力满足要求，现有病害以局部耐久性和使用功能影响为主；"
            "仍应及时维修，防止病害进一步发展。"
        )
    else:
        grade = _value(summary, "overall_grade")
        overall = f"综合来看，{subject}现有病害主要影响局部耐久性和使用功能"
        if _present(grade):
            overall += f"，总体技术状况等级为{grade}"
        overall += "；应及时处治突出病害，避免进一步影响结构性能和通行安全。"
    result.append(overall)
    return tuple(result[:4])

def compose_official_answers(
    *,
    summary: object,
    defects: Sequence[object],
    recommendations: Sequence[object],
    facility_context: FacilityContext,
    source_causes: Sequence[str] = (),
    document_text: str = "",
) -> OfficialAnswers:
    """Return the single deterministic narrative used by all final outputs."""

    groups = _group_defects(defects, facility_context)
    treatments = _treatment_texts(recommendations)
    overall = _overall_conclusion(groups, summary, facility_context, document_text)
    risks = _risk_points(groups)
    history, status = _history_paragraph(summary, groups, facility_context, document_text)
    detailed = (
        _score_paragraph(summary, _subject(facility_context)),
        history,
        _current_state(groups, facility_context, document_text),
        _judgement(summary, facility_context, groups, document_text),
    )
    return OfficialAnswers(
        overall_conclusion=overall,
        risk_points=risks,
        detailed_conclusion=detailed,
        causes=_causes(groups, source_causes),
        treatments=treatments,
        safety_impact=_safety_impacts(groups, summary, facility_context, document_text),
        history_status=status,
    )
