"""Deterministic fact extraction for the remaining text-list sections."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
import re
from typing import Iterable, Mapping, Sequence

from ..contracts import (
    BridgeSummary,
    DefectObservation,
    DocumentModel,
    ParagraphBlock,
    Recommendation,
    TableBlock,
)


@dataclass(frozen=True)
class TextSectionExtraction:
    """The four text-list values assembled from existing Word evidence."""

    detailed_conclusion: tuple[str, ...] = ()
    causes: tuple[str, ...] = ()
    treatments: tuple[str, ...] = ()
    safety_impact: tuple[str, ...] = ()


@dataclass(frozen=True)
class _TextUnit:
    block_index: int
    text: str


_DISEASE_WORDS = (
    "病害", "缺陷", "裂缝", "裂纹", "破损", "损坏", "锈蚀", "露筋", "渗水", "积水",
    "缺失", "离析", "不密实", "泛碱", "变形", "磨损", "腐蚀", "开裂", "松动", "脱落",
    "坑槽", "沉降", "剥落", "老化", "冲刷", "断裂", "移位",
)
_COMPONENT_WORDS = (
    "桥面", "上部", "下部", "梁", "板", "腹板", "翼板", "横隔板", "湿接缝", "盖梁",
    "支座", "桥台", "桥墩", "栏杆", "护栏", "伸缩缝", "泄水", "钢筋", "保护层", "结构",
    "构件", "桥梁", "路面", "墩台",
)
_CAUSE_WORDS = ("由于", "因", "主要原因为", "原因是", "导致", "造成", "受影响")
_IMPACT_WORDS = (
    "影响", "风险", "隐患", "承载能力", "行车安全", "结构安全", "耐久性", "使用功能",
    "功能", "可能导致", "不利于",
)
_ACTION_WORDS = (
    "建议", "应及时", "需及时", "及时进行", "及时处理", "及时修复", "及时维修", "修补",
    "维修", "养护", "处置", "处治", "处理", "加固", "清理", "封闭", "更换", "复位", "除锈",
    "涂刷", "灌浆", "修理", "观测", "巡查", "维护", "管理",
)
_FACT_WORDS = (
    "总体", "整体", "技术状况", "评分", "等级", "BCI", "病害", "裂缝", "破损", "锈蚀",
    "露筋", "渗水", "缺失", "结构", "承载", "良好", "完好", "满足", "合格率",
)
_SECTION_PATTERNS = {
    "safety": re.compile(r"(?:\d+(?:\.\d+)*\s*)?安全性评估(?!规程|等级|内容)"),
    "conclusion": re.compile(r"(?:\d+(?:\.\d+)*\s*)?(?:检测结论|评估结论|检查结论|总体结论|综合结论)"),
    "treatment": re.compile(r"(?:\d+(?:\.\d+)*\s*)?(?:处理建议|处置建议|处治建议|处理意见|建议明细)"),
    "overview": re.compile(r"(?:\d+(?:\.\d+)*\s*)?(?:外观检查结果|外观病害检查)"),
    "cause": re.compile(r"(?:\d+(?:\.\d+)*\s*)?(?:病害原因分析|病害成因分析|原因分析)"),
}

SUMMARY_STYLE_ENV = "SUMMARY_STYLE"
SUMMARY_STYLE = os.getenv(SUMMARY_STYLE_ENV, "legacy").strip().lower() or "legacy"
VALID_SUMMARY_STYLES = frozenset({"legacy", "official"})


def normalize_summary_style(style: str | None = None) -> str:
    """Resolve the optional deterministic summary-style experiment."""

    resolved = (
        str(style).strip().lower()
        if style is not None
        else os.getenv(SUMMARY_STYLE_ENV, "legacy").strip().lower()
    ) or "legacy"
    if resolved not in VALID_SUMMARY_STYLES:
        allowed = ", ".join(sorted(VALID_SUMMARY_STYLES))
        raise ValueError(f"invalid {SUMMARY_STYLE_ENV}={resolved!r}; expected one of: {allowed}")
    return resolved


def _official_component(location: str, description: str) -> str:
    compact = re.sub(r"\s+", "", f"{location}{description}")
    if any(token in compact for token in (
        "桥面", "铺装", "伸缩缝", "栏杆", "护栏", "人行道", "泄水", "排水", "防撞墙",
    )):
        return "桥面系"
    if any(token in compact for token in (
        "桥墩", "桥台", "墩柱", "台身", "台帽", "基础", "承台", "盖梁", "翼墙", "锥坡",
    )):
        return "下部结构"
    if any(token in compact for token in (
        "主梁", "横梁", "纵梁", "横隔", "腹板", "翼板", "湿接缝", "支座", "梁体", "梁板",
    )):
        return "上部结构"
    return ""


def _official_overall_conclusion(
    current: str,
    defects: Sequence[object],
    *,
    facility_context: object | None = None,
) -> str:
    grouped: dict[str, list[str]] = {
        "上部结构": [],
        "下部结构": [],
        "桥面系": [],
    }
    for defect in defects:
        location = _field_value(defect, "location")
        defect_type = _field_value(defect, "defect_type")
        description = _field_value(defect, "description")
        component = _official_component(location, description or defect_type)
        if not component:
            continue
        fact = _clean_text(defect_type or description, strip_number=False).strip("，,；;。 ")
        if fact and fact not in grouped[component]:
            grouped[component].append(fact)

    clauses: list[str] = []
    for component in ("上部结构", "下部结构", "桥面系"):
        facts = grouped[component][:5]
        if facts:
            clauses.append(f"{component}存在{'、'.join(facts)}")
    if not clauses:
        return current

    noun = _context_value(facility_context, "facility_noun", "桥梁") or "桥梁"
    # Keep the official bridge wording exact for bridges while preserving the
    # facility noun for pedestrian/underpass/tunnel reports.
    return f"本次定检结果表明，{noun}" + "；".join(clauses) + "。"


def _official_trend(value: str) -> str:
    text = _clean_text(value, strip_number=False).strip("，,；;。 ")
    if not text or text in {"无", "暂无", "不适用"}:
        return "无" if text else text
    text = re.sub(r"^与上一次(?:定检|检测|检查)相比[，,:：\s]*", "", text)
    parts: list[str] = []
    for raw in re.split(r"[；;]+", text):
        part = raw.strip("，,；;。 ")
        if not part:
            continue
        part = part.replace(":", "：")
        match = re.match(r"^(上部结构|下部结构|桥面系|总体|整体|桥梁)(?:：)?(.*)$", part)
        component = match.group(1) if match else ""
        content = match.group(2).strip("：，,；;。 ") if match else part
        content = re.sub(r"^新增病害[：]?", "新增", content)
        content = re.sub(r"^病害发展(?:趋势)?[：]?", "", content)
        if content in {"", "无", "暂无", "无变化", "新增无", "新增病害无"}:
            continue
        content = re.sub(r"[,，]+", "、", content)
        cleaned = f"{component}{content}" if component else content
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    if not parts:
        return "无"
    return "与上一次定检相比，" + "；".join(parts) + "。"


def apply_summary_style(
    summary: BridgeSummary,
    defects: Sequence[object],
    *,
    facility_context: object | None = None,
    style: str | None = None,
) -> BridgeSummary:
    """Apply the optional official summary wording without changing facts."""

    resolved = normalize_summary_style(style)
    if resolved == "legacy":
        return summary
    return replace(
        summary,
        overall_conclusion=_official_overall_conclusion(
            summary.overall_conclusion, defects, facility_context=facility_context
        ),
        trend=_official_trend(summary.trend),
    )

_TITLE_RE = re.compile(
    r"^(?:第?\d+(?:\.\d+)*\s*)?(?:检测结论|评估结论|检查结论|总体结论|安全性评估|"
    r"桥梁安全性评估|安全性评估等级|安全性评估内容|现状评估|预测评估|综合评估|"
    r"综合结论|检测结果|评估结果|外观检查|专项检测|桥面系|上部结构|下部结构|处理建议|"
    r"处置建议|处治建议|建议明细|病害明细|应采取的措施|技术状况等级评定|结构检算|"
    r"静载试验|动载试验|基本状况卡)$"
)
_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:[（(]?[0-9一二三四五六七八九十百千万]+[）)\.、:：]?|[⑴-⒇])\s*"
)


def extract_text_sections(
    document: DocumentModel,
    routes: Sequence[object],
    recommendations: Sequence[Recommendation],
    summary: BridgeSummary,
    defects: Sequence[DefectObservation] | None = None,
    *,
    facility_context: object | None = None,
    field_states: Mapping[str, str] | None = None,
) -> TextSectionExtraction:
    """Extract fact-only text sections without introducing new report facts.

    The original four-argument call remains available for callers that still
    rely on the paragraph-window fallback.  The pipeline supplies
    ``defects.records`` as the fifth argument and therefore uses the
    structured-facts path below.
    """

    if defects is not None:
        return _structured_text_sections(
            document,
            routes,
            recommendations,
            summary,
            defects,
            facility_context=facility_context,
            field_states=field_states,
        )

    all_units = _document_units(document)
    heading_blocks = _heading_blocks(routes)
    recommendation_blocks = _route_blocks(routes, {"recommendations", "treatment_recommendations"})
    inspection_units = _route_units(routes, "inspection_conclusion", recommendation_blocks)
    safety_units = _section_window(all_units, "safety", {"conclusion", "treatment"})
    if not safety_units:
        safety_units = tuple(unit for unit in all_units if unit.block_index not in recommendation_blocks)
    if not inspection_units:
        inspection_units = _pre_section_units(all_units, "safety")

    detailed = _detailed_conclusion(
        summary,
        inspection_units,
        safety_units,
        all_units,
        heading_blocks,
    )
    causes = _causes(safety_units, heading_blocks)
    safety = _safety_impact(safety_units, heading_blocks)
    treatments = _treatments(recommendations)
    return TextSectionExtraction(detailed, causes, treatments, safety)


_MISSING_VALUES = frozenset(
    {
        "",
        "无",
        "未提供",
        "未给出",
        "缺失",
        "未知",
        "n/a",
        "na",
        "none",
        "null",
        "-",
        "--",
    }
)
_EXPLICIT_CAUSE_RE = re.compile(
    r"(?:由于|因为|主要原因(?:是|为)?|原因(?:是|为)|系[^。；;]{1,80}所致|由[^。；;]{1,80}(?:引起|导致)|受[^。；;]{1,80}影响)"
)
_CAUSE_NOISE_PHRASES = (
    "检测不得对", "不得对设施结构造成损坏", "检测过程中",
    "试验过程中", "负责原样恢复", "检测结束后负责",
)
_CAUSE_EXCLUDED_PREFIXES = (
    "严格",
    "严禁",
    "建议",
    "应",
    "需",
    "及时",
    "避免",
    "按照",
    "进行",
    "预测",
    "鉴于",
    "对",
    "对于",
    "加强",
    "按",
    "根据",
    "本报告",
    "挠度检测",
)
_SAFETY_EVIDENCE_WORDS = (
    "安全",
    "承载能力",
    "耐久性",
    "行车",
    "通行",
    "风险",
    "隐患",
    "使用功能",
    "功能",
    "结构功能",
)
_SAFETY_NOISE_WORDS = (
    "检测依据",
    "技术规范",
    "评定标准",
    "评定方法",
    "总体评定",
    "设计指标",
    "重要部件",
    "次要部件",
    "完好状态",
    "较好状态",
    "合格级",
    "不合格级",
    "评估等级",
    "等级划分",
    "桥梁完好状况",
)
_STRUCTURED_COMPONENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "桥面系",
        (
            "桥面系",
            "桥面铺装",
            "桥面",
            "铺装",
            "路面",
            "行车道",
            "人行道",
            "伸缩缝",
            "泄水",
            "排水",
            "路缘石",
        ),
    ),
    (
        "上部结构",
        (
            "上部结构",
            "主梁",
            "梁体",
            "梁底",
            "梁",
            "箱梁",
            "板",
            "腹板",
            "翼板",
            "翼缘板",
            "横隔板",
            "横向联系",
            "湿接缝",
            "索塔",
            "塔柱",
            "主塔",
            "斜拉索",
        ),
    ),
    (
        "下部结构",
        (
            "下部结构",
            "桥墩",
            "墩身",
            "墩台",
            "桥台",
            "台身",
            "台帽",
            "盖梁",
            "基础",
            "支座",
            "垫石",
        ),
    ),
    (
        "附属设施",
        (
            "栏杆",
            "护栏",
            "照明",
            "标志",
            "标线",
            "防护网",
            "桁车",
            "检修设施",
            "附属设施",
        ),
    ),
)


def _structured_text_sections(
    document: DocumentModel,
    routes: Sequence[object],
    recommendations: Sequence[Recommendation],
    summary: BridgeSummary,
    defects: Sequence[DefectObservation],
    *,
    facility_context: object | None = None,
    field_states: Mapping[str, str] | None = None,
) -> TextSectionExtraction:
    summary_value = getattr(summary, "summary", summary)
    recommendation_records = _record_sequence(recommendations)
    defect_records = _usable_defects(_record_sequence(defects))
    units = _document_units(document)
    heading_blocks = _heading_blocks(routes)
    recommendation_blocks = _route_blocks(
        routes,
        {"recommendations", "treatment_recommendations"},
    ) | _recommendation_evidence_blocks(recommendation_records)
    safety_units = _section_window(units, "safety", {"conclusion", "treatment"})
    if not safety_units:
        safety_units = _route_units(routes, "safety_assessment", recommendation_blocks)
    if not safety_units:
        safety_units = units
    cause_units = _section_window(units, "cause", {"safety", "conclusion", "treatment"})
    if not cause_units:
        # Cause paragraphs are often in chapter 7 while safety assessment is
        # chapter 9.  Falling back to all report units is still safer than
        # reusing only the safety window, because _source_causes itself accepts
        # only explicit disease-cause statements and excludes actions/noise.
        cause_units = units
    source_causes = _source_causes(
        cause_units,
        summary_value,
        recommendation_blocks,
        heading_blocks,
    )
    impact_sources = _impact_sources(safety_units, heading_blocks)
    return TextSectionExtraction(
        detailed_conclusion=_structured_detailed_conclusion(
            document,
            summary_value,
            routes,
            defect_records,
            recommendation_records,
            facility_context=facility_context,
            field_states=field_states,
        ),
        causes=_structured_causes(defect_records, source_causes),
        treatments=_structured_treatments(recommendation_records),
        safety_impact=_structured_safety_impact(
            summary_value,
            defect_records,
            impact_sources,
        ),
    )


def _record_sequence(value: object) -> tuple[object, ...]:
    records = getattr(value, "records", value)
    if records is None or isinstance(records, (str, bytes, dict)):
        return ()
    try:
        return tuple(records)  # type: ignore[arg-type]
    except TypeError:
        return ()


def _recommendation_evidence_blocks(records: Sequence[object]) -> set[int]:
    result: set[int] = set()
    for record in records:
        evidence = getattr(record, "evidence", ())
        for anchor in evidence if isinstance(evidence, (list, tuple)) else ():
            block_index = getattr(anchor, "block_index", None)
            if isinstance(block_index, int):
                result.add(block_index)
    return result


def _field_value(item: object, field: str) -> str:
    if isinstance(item, dict):
        value = item.get(field, "")
    else:
        value = getattr(item, field, "")
    return " ".join(str(value or "").replace("\u00a0", " ").split()).strip()


def _present_value(value: object) -> str:
    text = " ".join(str(value or "").split()).strip()
    if text.casefold() in _MISSING_VALUES:
        return ""
    return text


def _summary_field(summary: object, field: str) -> str:
    return _present_value(_field_value(summary, field))


def _usable_defects(records: Sequence[object]) -> tuple[object, ...]:
    result: list[object] = []
    for record in records:
        text = _compact(
            " ".join(
                (
                    _field_value(record, "location"),
                    _field_value(record, "defect_type"),
                    _field_value(record, "description"),
                )
            )
        )
        defect_type = _compact(_field_value(record, "defect_type"))
        description = _compact(_field_value(record, "description"))
        if not text or (
            defect_type in {"/", "\\", "-", "—", "_"}
            and not _canonical_type(text)
        ):
            continue
        if re.match(r"(?:照片|图|表|病害分布|缺陷照片)", text) and not _canonical_type(text):
            continue
        result.append(record)
    return tuple(result)


def _canonical_type(record_text: str) -> str:
    text = _compact(record_text)
    if any(word in text for word in ("露筋", "锈蚀", "腐蚀")):
        return "露筋锈蚀"
    if any(word in text for word in ("渗水", "渗漏", "漏水", "泛碱", "浸水")):
        return "渗水泛碱"
    if any(word in text for word in ("蜂窝", "麻面")):
        return "蜂窝麻面"
    if "支座" in text and "变形" in text:
        return "支座变形"
    if ("铺装" in text or "伸缩缝" in text) and any(
        word in text for word in ("破损", "损坏", "坑槽", "开裂", "裂缝", "脱落")
    ):
        return "铺装/伸缩缝破损"
    if any(word in text for word in ("裂缝", "裂纹", "开裂")):
        return "裂缝"
    return ""


def _defect_type(record: object) -> str:
    text = " ".join(
        (
            _field_value(record, "defect_type"),
            _field_value(record, "description"),
        )
    )
    return _canonical_type(text) or _field_value(record, "defect_type") or "未分类病害"


def _defect_counts(records: Sequence[object]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for record in records:
        name = _defect_type(record)
        if name and name not in counts:
            counts[name] = 0
        if name:
            counts[name] += 1
    return tuple(counts.items())


def _format_defect_counts(records: Sequence[object], *, limit: int = 8) -> str:
    counts = _defect_counts(records)
    if not counts:
        return "未提取到结构化病害记录"
    return "、".join(f"{name}{count}条" for name, count in counts[:limit])


def _classify_component(text: str) -> str | None:
    compact = _compact(text)
    for label, words in _STRUCTURED_COMPONENTS:
        if any(word in compact for word in words):
            return label
    return None


def _record_component(record: object) -> str | None:
    return _classify_component(
        " ".join(
            (
                _field_value(record, "location"),
                _field_value(record, "defect_type"),
                _field_value(record, "description"),
            )
        )
    )


def _component_summary(label: str, records: Sequence[object]) -> str:
    counts = _format_defect_counts(records, limit=5)
    examples: list[str] = []
    for record in records[:3]:
        location = _field_value(record, "location")
        defect_type = _defect_type(record)
        if location and _compact(location) != _compact(defect_type):
            examples.append(f"{_truncate_fact(location, 36)}：{defect_type}")
    suffix = f"；典型部位为{ '、'.join(examples)}" if examples else ""
    return f"记录{len(records)}条，主要为{counts}{suffix}"


def _context_value(context: object | None, name: str, default: str = "") -> str:
    if isinstance(context, Mapping):
        return str(context.get(name, default) or default)
    return str(getattr(context, name, default) or default)


def _facility_component_labels(facility_type: str) -> tuple[str, ...]:
    return {
        "pedestrian_underpass": ("顶板", "侧墙", "翼墙", "洞口", "接缝止水", "排水及附属设施"),
        "vehicle_underpass": ("顶板", "侧墙", "翼墙", "洞口", "接缝止水", "排水及附属设施"),
        "underpass": ("顶板", "侧墙", "翼墙", "洞口", "接缝止水", "排水及附属设施"),
        "pedestrian_overpass": ("桥面板", "梯道", "栏杆", "墩柱", "盖梁", "附属设施"),
        "tunnel": ("洞口", "衬砌", "路面", "防排水", "附属设施"),
    }.get(facility_type, tuple(label for label, _ in _STRUCTURED_COMPONENTS))


def _record_matches_label(record: object, label: str) -> bool:
    text = _compact(" ".join((
        _field_value(record, "location"),
        _field_value(record, "defect_type"),
        _field_value(record, "description"),
    )))
    aliases = {
        "顶板": ("顶板",), "侧墙": ("侧墙",), "翼墙": ("翼墙",), "洞口": ("洞口", "出入口"),
        "接缝止水": ("沉降缝", "变形缝", "止水带", "接缝"),
        "排水及附属设施": ("排水", "积水", "泄水", "栏杆", "护栏", "照明", "附属"),
        "桥面板": ("桥面", "桥面板", "铺装"), "梯道": ("梯道", "踏步", "楼梯"),
        "栏杆": ("栏杆", "护栏"), "墩柱": ("墩柱", "桥墩"), "盖梁": ("盖梁",),
        "附属设施": ("附属", "标志", "限高", "照明", "排水"),
        "洞口": ("洞口",), "衬砌": ("衬砌",), "路面": ("路面", "铺装"),
        "防排水": ("防水", "排水", "渗水", "积水"),
    }.get(label, (label,))
    return any(alias in text for alias in aliases)


_FORMAL_CONCLUSION_TITLES = (
    "详细结论",
    "检测结论",
    "评估结论",
    "检查结论",
    "总体结论",
    "综合结论",
    "综合评定",
)
_OVERVIEW_CONCLUSION_TITLES = (
    "检测结果汇总",
    "检查结果汇总",
    "外观检测结果及病害成因分析",
    "外观检查结果及病害成因分析",
)
_DETAILED_STOP_TITLES = (
    "病害成因分析",
    "桥面线形",
    "桥位环境调查",
    "技术状况等级评定",
    "桥梁技术状况等级评定",
    "安全评估",
    "安全性评估",
    "处理建议",
    "处置建议",
    "维修建议",
    "养护建议",
)
_DETAILED_EXCLUDED_MARKERS = (
    "检测依据",
    "评定依据",
    "技术规范",
    "评定标准",
    "结构检算",
    "荷载试验",
    "静载试验",
    "动载试验",
    "自振频率",
    "冲击系数",
    "混凝土强度",
    "保护层合格率",
    "钢筋配置",
    "桥梁博士",
    "计算结果",
    "试验车辆",
)
_DETAILED_DISEASE_WORDS = (
    *_DISEASE_WORDS,
    "磨光",
    "露骨",
    "蜂窝",
    "麻面",
    "跳车",
    "错台",
    "高差",
    "涂层",
    "螺钉缺失",
    "孔盖缺失",
    "剪切变形",
    "无泄水孔",
    "无排水设施",
    "未设置",
)
_DETAIL_NUMBER_RE = re.compile(
    r"^\s*(?:[（(]?[0-9一二三四五六七八九十百千万]+[）)\.、:：]?|[①-⑳⑴-⒇])\s*"
)
_DETAIL_SECTION_PREFIX_RE = re.compile(
    r"^\s*\d+(?:\.\d+)+\s*(?:外观(?:检测|检查)?结果及病害成因分析|外观检查|检测结果汇总)?\s*"
)
_DETAIL_CAPTION_RE = re.compile(
    r"(?:表|图|照片)\s*[0-9一二三四五六七八九十百千万]+(?:[.\-][0-9一二三四五六七八九十百千万]+)*"
    r"[^；;。]*?(?=(?:[；;。]|$))"
)

_DETAIL_SUMMARY_STOP_RE = re.compile(
    r"(?:\d+|[一二三四五六七八九十]+)[、.．]?\s*(?:"
    r"专项检测(?:结果)?|技术状况(?:等级)?评定|桥梁技术状况指数|"
    r"桥梁结构验算(?:结果)?|结构检算|静载试验(?:结果)?|动载试验(?:结果)?|"
    r"病害成因分析|桥面线形|桥位环境调查|安全性?评估|处理建议|处置建议|维修建议|养护建议"
    r")"
)
_NEGATED_DISEASE_RE = re.compile(
    r"(?:无|未见|未发现|没有)[^，,；;。]{0,20}"
    r"(?:明显)?(?:病害|缺陷|破损|裂缝|裂纹|沉降|变形|下挠|积水|冲刷|锈蚀|残缺|渗水|泛碱)"
)
_CONCRETE_DETAILED_DISEASE_WORDS = tuple(
    word for word in _DETAILED_DISEASE_WORDS if word not in {"病害", "缺陷"}
)
_GOOD_STATE_MARKERS = (
    "基本完好", "较为完好", "状况良好", "外观良好", "整体良好", "正常使用", "无明显异常"
)
_EXPLICIT_POSITIVE_DISEASE_WORDS = (
    "存在", "局部破损", "开裂", "裂缝", "裂纹", "锈蚀", "渗水", "漏水", "积水",
    "缺失", "损坏", "变形", "下挠", "露筋", "剥落", "脱落", "磨损", "坑槽",
    "蜂窝", "麻面", "错台", "跳车", "冲刷", "泛碱", "松动", "断裂",
)


def _has_positive_detailed_disease(value: str) -> bool:
    compact = _compact(value)
    if any(marker in compact for marker in ("无泄水孔", "无排水设施", "未设置泄水", "缺少排水")):
        return True
    positive_text = _NEGATED_DISEASE_RE.sub("", compact)
    if _has_any(positive_text, _GOOD_STATE_MARKERS) and not _has_any(
        positive_text, _EXPLICIT_POSITIVE_DISEASE_WORDS
    ):
        return False
    return _has_any(positive_text, _CONCRETE_DETAILED_DISEASE_WORDS)


def _route_category_value(route: object) -> str:
    value = getattr(getattr(route, "category", ""), "value", getattr(route, "category", ""))
    return str(value)


def _route_heading_title(route: object) -> str:
    raw = str(getattr(getattr(route, "heading", None), "raw_text", "") or "")
    compact = " ".join(raw.replace("\u00a0", " ").split()).strip()
    compact = re.sub(r"^\s*(?:第?\d+(?:\.\d+)*|[一二三四五六七八九十]+)[、.．:：\s]*", "", compact)
    return compact.strip("：:。.;；，,、 ")


def _block_heading_title(block: object) -> str:
    raw = str(getattr(block, "raw_text", "") or "")
    compact = " ".join(raw.replace("\u00a0", " ").split()).strip()
    compact = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", compact)
    return compact.strip("：:。.;；，,、 ")


def _is_detail_heading(text: str) -> bool:
    compact = " ".join((text or "").split()).strip("：:。.;；，,、 ")
    compact = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", compact)
    return any(compact == title or compact.endswith(title) for title in (
        *_FORMAL_CONCLUSION_TITLES,
        *_OVERVIEW_CONCLUSION_TITLES,
        *_DETAILED_STOP_TITLES,
        "外观检查",
        "外观检测",
    ))


def _is_raw_test_or_calculation(sentence: str) -> bool:
    compact = _compact(sentence)
    if any(marker in compact for marker in (
        "检测依据", "评定依据", "技术规范", "评定标准", "结构建模",
        "计算跨径", "计算模型", "弯矩包络", "剪力包络", "荷载效率",
        "测试仪器", "测点布置", "加载程序", "试验工况", "理论计算",
        "钢筋明细表", "截面号", "实测值", "理论值", "校验系数在",
    )):
        return True
    # Raw numeric rows are not conclusions.  Keep short result sentences such
    # as “承载能力满足要求” even when they mention a design load.
    numeric_hits = len(re.findall(r"\d+(?:\.\d+)?(?:MPa|mm|m/s2|Hz|kN|kN·m|%|℃)", sentence))
    has_result = _has_any(compact, (
        "满足要求", "符合要求", "工作性能良好", "处于弹性工作状态",
        "承载能力满足", "刚度满足", "强度满足", "动力特性满足",
        "安全性评估等级", "技术状况等级",
    ))
    return numeric_hits >= 3 and not has_result


def _clean_detailed_fact(value: str, *, formal: bool = False) -> str:
    """Clean one conclusion paragraph without discarding valid assessment facts.

    Formal conclusion routes may contain disease summaries, impact statements,
    overall assessments and concise test conclusions.  Only captions, headings,
    methods, standards and raw calculation data are removed.  Overview fallback
    remains stricter and keeps disease statements only.
    """

    text = " ".join((value or "").replace("\u00a0", " ").split()).strip()
    if not text or _is_detail_heading(text):
        return ""
    text = _DETAIL_SECTION_PREFIX_RE.sub("", text)
    text = _DETAIL_NUMBER_RE.sub("", text)
    text = _DETAIL_CAPTION_RE.sub("", text)
    text = re.sub(
        r"(?:具体病害情况见|现场病害典型照片见|病害照片见|具体情况见)[^。；;]*[。；;]?",
        "",
        text,
    )
    text = re.sub(r"(?:表|图)\s*\d+(?:\.\d+)*(?:-\d+)?\s*[^。；;]*", "", text)

    kept: list[str] = []
    for raw in re.split(r"[。；;]+", text):
        sentence = " ".join(raw.split()).strip(" ，,；;。． ")
        compact = _compact(sentence)
        if not compact or _is_detail_heading(sentence):
            continue
        if any(marker in compact for marker in (
            "目录", "本页以下无正文", "照片", "示意图", "布置图",
            "检查结果表", "病害分布表", "病害分布情况表",
        )):
            continue
        if any(marker in compact for marker in _DETAILED_EXCLUDED_MARKERS) and not _has_any(
            compact, ("满足要求", "符合要求", "安全性评估等级", "技术状况等级")
        ):
            continue
        if _is_raw_test_or_calculation(sentence):
            continue
        if _is_action(sentence) and not _has_any(compact, _IMPACT_WORDS):
            continue
        if formal:
            if not _has_any(compact, (
                *_DETAILED_DISEASE_WORDS, *_IMPACT_WORDS,
                "技术状况", "评定为", "良好状态", "合格状态",
                "满足要求", "符合要求", "工作性能良好",
                "弹性工作状态", "无开展", "未出现新裂缝",
            )):
                continue
        elif not _has_positive_detailed_disease(sentence):
            continue
        kept.append(_truncate_fact(sentence, 420))
    return "；".join(_unique(kept)[:8])

def _clean_summary_overview(value: str) -> str:
    """Keep the external-inspection disease facts from a noisy summary.

    Some report summaries concatenate external inspection, dimensions,
    special tests, structural calculations and load-test data into one value.
    Stop before those later sections, split numbered sub-items, and retain only
    concrete positive disease statements.  Measurements inside a retained
    disease statement are preserved verbatim.
    """

    text = " ".join((value or "").replace("\u00a0", " ").split()).strip()
    if not text:
        return ""

    stop_positions = [
        match.start()
        for match in _DETAIL_SUMMARY_STOP_RE.finditer(text)
        if match.start() > 0
    ]
    for marker in _DETAILED_STOP_TITLES:
        position = text.find(marker)
        if position > 0:
            stop_positions.append(position)
    if stop_positions:
        text = text[: min(stop_positions)]

    text = re.sub(
        r"^\s*(?:\d+|[一二三四五六七八九十]+)[、.．]?\s*"
        r"(?:外观检查结果?|外观检测结果?|外观病害检查)\s*[:：]?",
        "",
        text,
    )
    text = re.sub(
        r"(?:具体病害情况见|现场病害典型照片见|病害照片见|具体情况见)[^。；;]*[。；;]?",
        "",
        text,
    )
    text = _DETAIL_CAPTION_RE.sub("", text)
    text = re.sub(r"(?:桥面系|上部结构|下部结构)检查结果表", " ", text)
    text = re.sub(r"(?:表|图)\s*\d+(?:\.\d+)*(?:-\d+)?\s*[^。；;]*", " ", text)

    numbered = re.split(r"(?=[（(]?\d+[）)])", text)
    source_items = numbered if len(numbered) > 1 else re.split(r"[。]+", text)
    facts: list[str] = []
    for item in source_items:
        item = re.sub(r"^\s*[（(]?\d+[）)]\s*", "", item)
        fact = _clean_detailed_fact(item)
        if fact:
            facts.append(fact)
    selected = _select_detailed_facts(facts)
    return "；".join(selected)

def _detail_fact_priority(value: str) -> int:
    compact = _compact(value)
    score = 0
    if _has_any(compact, ("主要", "目前", "当前", "总体", "经检查", "经检测", "外观检查")):
        score += 4
    score += min(4, sum(1 for word in _DETAILED_DISEASE_WORDS if word in compact))
    score += min(3, sum(1 for word in _COMPONENT_WORDS if word in compact))
    if _has_any(compact, _IMPACT_WORDS):
        score += 2
    return score


def _select_detailed_facts(values: Sequence[str]) -> tuple[str, ...]:
    unique = list(_unique(values))
    if not unique:
        return ()
    ranked = sorted(
        enumerate(unique),
        key=lambda item: (-_detail_fact_priority(item[1]), item[0]),
    )
    chosen_indexes = sorted(index for index, _ in ranked[:4])
    chosen: list[str] = []
    used = 0
    for index in chosen_indexes:
        value = unique[index]
        remaining = 1080 - used
        if remaining <= 40:
            break
        value = _truncate_fact(value, min(remaining, 420))
        chosen.append(value)
        used += len(value)
    return tuple(chosen)


def _labelled_table_conclusion_facts(document: DocumentModel) -> tuple[str, ...]:
    """Read formal conclusion rows from flattened one-table reports."""

    accepted = (
        "详细结论", "检测结论", "评估结论", "检查结论",
        "总体结论", "综合结论", "综合评定", "安全性评估",
    )
    facts: list[str] = []
    for block in document.blocks:
        if not isinstance(block, TableBlock):
            continue
        for row in block.rows:
            cells = [" ".join(str(cell.raw_text or "").split()).strip() for cell in row.cells]
            nonempty = [value for value in cells if value]
            if len(nonempty) < 2:
                continue
            label = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", nonempty[0]).strip("：:。.;；，,、 ")
            if not any(label == item or label.endswith(item) for item in accepted):
                continue
            for value in nonempty[1:]:
                fact = _clean_detailed_fact(value, formal=True)
                if fact:
                    facts.append(fact)
    return _unique(facts)


def _route_detailed_facts(document: DocumentModel, routes: Sequence[object]) -> tuple[str, ...]:
    strict: list[str] = []
    overview: list[str] = []
    seen_blocks: set[int] = set()
    for route in routes:
        if _route_category_value(route) != "inspection_conclusion":
            continue
        title = _route_heading_title(route)
        is_strict = any(title == item or title.endswith(item) for item in _FORMAL_CONCLUSION_TITLES)
        is_overview = any(title == item or title.endswith(item) for item in _OVERVIEW_CONCLUSION_TITLES)
        if not (is_strict or is_overview):
            continue
        target = strict if is_strict else overview
        for block in getattr(route, "blocks", ()):
            block_index = int(getattr(block, "block_index", -1))
            if block_index in seen_blocks:
                continue
            seen_blocks.add(block_index)
            if isinstance(block, TableBlock):
                # Whole-document tables are handled by row labels below.
                continue
            if not isinstance(block, ParagraphBlock):
                continue
            raw = str(getattr(block, "raw_text", "") or "")
            block_title = _block_heading_title(block)
            if any(block_title == marker or block_title.endswith(marker) for marker in _DETAILED_STOP_TITLES):
                break
            fact = _clean_detailed_fact(raw, formal=is_strict)
            if fact:
                target.append(fact)
    labelled = list(_labelled_table_conclusion_facts(document))
    values = [*strict, *labelled] if (strict or labelled) else overview
    return _select_detailed_facts(values)


def _route_detailed_overview(document: DocumentModel, routes: Sequence[object]) -> str:
    return "；".join(_route_detailed_facts(document, routes))


def _route_disease_overview(routes: Sequence[object]) -> str:
    """Keep the high-precision disease overview used by the v4 baseline."""

    strict: list[str] = []
    overview: list[str] = []
    seen_blocks: set[int] = set()
    for route in routes:
        if _route_category_value(route) != "inspection_conclusion":
            continue
        title = _route_heading_title(route)
        is_strict = any(title == item or title.endswith(item) for item in _FORMAL_CONCLUSION_TITLES)
        is_overview = any(title == item or title.endswith(item) for item in _OVERVIEW_CONCLUSION_TITLES)
        if not (is_strict or is_overview):
            continue
        target = strict if is_strict else overview
        for block in getattr(route, "blocks", ()):
            if not isinstance(block, ParagraphBlock):
                continue
            block_index = int(getattr(block, "block_index", -1))
            if block_index in seen_blocks:
                continue
            seen_blocks.add(block_index)
            block_title = _block_heading_title(block)
            if any(block_title == marker or block_title.endswith(marker) for marker in _DETAILED_STOP_TITLES):
                break
            fact = _clean_detailed_fact(str(getattr(block, "raw_text", "") or ""), formal=False)
            if fact:
                target.append(fact)
    values = strict if strict else overview
    return "；".join(_select_detailed_facts(values))


def _concise_assessment_facts(values: Sequence[str], facility_name: str = "") -> str:
    clauses: list[str] = []
    name = _compact(facility_name)
    for value in values:
        for raw in re.split(r"[；;。]+", value):
            clause = " ".join(raw.split()).strip(" ，,；;。 ")
            compact = _compact(clause)
            if not compact:
                continue
            if name:
                clause = clause.replace(facility_name, "该设施")
                compact = _compact(clause)
            # Score/grade is already carried by paragraph one.  Keep it only
            # when the same clause also contains a safety or load conclusion.
            only_grade = _has_any(compact, ("技术状况指数", "技术状况等级", "评定为B级", "评定为A级", "评定为C级")) and not _has_any(
                compact, ("安全", "承载", "耐久", "满足", "运营", "弹性", "无开展", "新裂缝")
            )
            if only_grade:
                continue
            if not _has_any(compact, (
                "安全", "承载", "耐久", "使用功能", "满足要求", "满足设计",
                "符合要求", "可安全运营", "弹性工作状态", "无开展",
                "未出现新裂缝", "工作性能良好", "动力特性满足",
            )):
                continue
            clause = re.sub(r"整体技术状况指数BCI\s*=\s*\d+(?:\.\d+)?[，,]?", "", clause, flags=re.I)
            clause = re.sub(r"综合评定(?:桥梁|该设施)的整体技术状况等级为[ABCDEF]级[，,]?为[^，,；;。]+状态[，,；;]?", "", clause)
            clause = clause.strip(" ，,；;。 ")
            if clause:
                clauses.append(_truncate_fact(clause, 150))
    return "；".join(_unique(clauses)[:4])


def _concise_recommendation_action(value: str, location: str = "") -> str:
    text = " ".join((value or "").split()).strip(" ，,；;。 ")
    compact = _compact(text)
    loc = _compact(location)
    if not compact:
        return ""
    if "排水" in compact and any(word in compact for word in ("布置", "增设", "设置", "疏通", "清理")):
        return "增设或疏通排水设施"
    if "栏杆" in compact or "护栏" in compact:
        if "锈蚀" in compact:
            return "修复栏杆并进行除锈防护"
        return "修复栏杆或护栏"
    if "裂缝" in compact and any(word in compact for word in ("封闭", "灌浆", "修补", "修复")):
        prefix = loc if loc and loc not in {"桥梁", "该设施", "全桥"} else "结构"
        return f"封闭或修补{prefix}裂缝"
    if "露筋" in compact or "混凝土破损" in compact:
        prefix = loc if loc and loc not in {"桥梁", "该设施", "全桥"} else "混凝土构件"
        return f"修补{prefix}破损露筋部位"
    if "锈蚀" in compact and any(word in compact for word in ("除锈", "涂装", "防锈")):
        prefix = loc if loc and loc not in {"桥梁", "该设施", "全桥"} else "金属构件"
        return f"对{prefix}除锈防护"
    if "更换" in compact:
        prefix = loc if loc and loc not in {"桥梁", "该设施", "全桥"} else "损坏构件"
        return f"更换{prefix}"
    if any(word in compact for word in ("定期检查", "日常检查", "巡查", "观测", "养护维修", "日常养护")):
        return "加强日常检查、观测和养护"
    if any(word in compact for word in ("严禁", "禁止", "车辆管理", "标识标牌")):
        return "加强通行和超载管理"
    if "桥面" in compact and any(word in compact for word in ("修复", "修补", "铺装")):
        return "修复桥面铺装"
    text = re.sub(r"《[^》]+》", "", text)
    text = re.sub(r"^.*?(?=(?:对|针对|封闭|修复|修补|维修|更换|清理|增设|布置|加强|严禁))", "", text)
    text = re.split(r"(?:以免|从而|保证|提高|防止|避免|该病害|若不|如不|同时也会)", text, maxsplit=1)[0]
    return _truncate_fact(text.strip(" ，,；;。 "), 40)

def _valid_score_value(value: str) -> str:
    text = _present_value(value)
    return text if re.fullmatch(r"\d+(?:\.\d+)?", text) else ""


def _valid_grade_value(value: str) -> str:
    text = _present_value(value).replace(" ", "")
    return text if re.fullmatch(r"(?:[A-Ea-e]级|[一二三四五六]类|优|良好|中等|差)", text) else ""


def _history_text(summary: object, noun: str) -> str:
    """Return only explicit historical comparison facts.

    Absence of previous values does not prove that this is the first periodic
    inspection, so the deterministic layer must not invent that sentence.
    """

    history = _summary_field(summary, "trend")
    previous_score = _valid_score_value(_summary_field(summary, "previous_overall_score"))
    previous_grade = _valid_grade_value(_summary_field(summary, "previous_overall_grade"))
    history_parts: list[str] = []
    if previous_score:
        history_parts.append(f"上一周期总体评分为{previous_score}分")
    if previous_grade:
        history_parts.append(f"上一周期总体等级为{previous_grade}")
    if history and history != "无":
        history_parts.append(f"报告记录的发展趋势为{history}")
    return "历史对比：" + "，".join(history_parts) if history_parts else ""

def _structured_detailed_conclusion(
    document: DocumentModel,
    summary: object,
    routes: Sequence[object],
    defects: Sequence[object],
    recommendations: Sequence[object],
    *,
    facility_context: object | None = None,
    field_states: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return concise, report-backed conclusion paragraphs.

    This field is not a second defect/recommendation table.  It contains only
    the formal score/grade, a compact disease overview and explicit assessment
    conclusions.  Missing history is omitted rather than converted into a
    fabricated "first inspection/no history" statement.
    """

    score = _structured_score_paragraph(summary, facility_context=facility_context)
    noun = _context_value(facility_context, "facility_noun", "桥梁")
    route_facts = list(_route_detailed_facts(document, routes))

    disease_overview = _route_disease_overview(routes)
    if not disease_overview:
        disease_overview = _clean_summary_overview(_summary_field(summary, "overall_conclusion"))
    assessment_facts = [
        fact for fact in route_facts
        if _compact(fact) != _compact(disease_overview)
        and not (disease_overview and _compact(fact) in _compact(disease_overview))
    ]

    paragraphs: list[str] = []
    if _compact(score):
        paragraphs.append(score)

    history = _history_text(summary, noun)
    if disease_overview or history:
        overview = _truncate_fact(disease_overview, 520) if disease_overview else ""
        parts: list[str] = []
        if history:
            parts.append(history)
        if overview:
            parts.append(f"检测病害具体表现为：{overview}")
        paragraphs.append("本次报告" + "；".join(parts))

    # Keep formal assessment as the third official paragraph.
    assessment = _concise_assessment_facts(
        assessment_facts,
        _summary_field(summary, "bridge_name"),
    )
    if assessment:
        paragraphs.append("目前" + assessment.lstrip("目前，, "))

    # The official answer shape contains a fourth synthesis paragraph.  Build
    # it only from already extracted risk and recommendation evidence so the
    # deterministic fallback remains useful when the live model is rejected.
    risk = _safe_summary_fact(_summary_field(summary, "risk_points"))
    recommendation_texts = [
        _field_value(item, "content") for item in recommendations
        if _field_value(item, "content")
    ]
    synthesis_parts: list[str] = []
    if risk:
        synthesis_parts.append(_truncate_fact(risk, 260).rstrip("。"))
    elif disease_overview:
        synthesis_parts.append("应重点关注上述突出病害及其后续发展")
    if recommendation_texts:
        selected = "；".join(_unique(recommendation_texts)[:2])
        synthesis_parts.append("处置重点为" + _truncate_fact(selected, 220).rstrip("。"))
    if synthesis_parts:
        paragraphs.append("综上，" + "；".join(synthesis_parts))

    # Preserve the four official slots when evidence is available.  Missing
    # slots are not filled with invented facts; the live narrative layer can
    # still enhance them when it has evidence.
    return tuple(
        _clean_text(value, strip_number=False).rstrip("；;")
        + ("" if value.rstrip().endswith("。") else "。")
        for value in paragraphs[:4]
        if _compact(value)
    )

def _structured_score_paragraph(summary: object, *, facility_context: object | None = None) -> str:
    score = _valid_score_value(_summary_field(summary, "overall_score"))
    grade = _valid_grade_value(_summary_field(summary, "overall_grade"))
    facility_type = _context_value(facility_context, "facility_type", "bridge")
    facility_name = _summary_field(summary, "bridge_name")
    subject = {
        "pedestrian_underpass": "该人行通道",
        "vehicle_underpass": "该车行下穿道",
        "underpass": "该下穿道",
        "pedestrian_passage": "该人行通道",
        "pedestrian_overpass": "该人行天桥",
        "tunnel": "该隧道",
        "culvert": "该涵洞",
        "road": "该道路",
    }.get(facility_type, "该桥")
    if "人行天桥" in facility_name:
        subject = "该人行天桥"
    elif "桥式通道" in facility_name:
        subject = "该桥式通道"
    elif "人行通道" in facility_name or "人行地通道" in facility_name:
        subject = "该人行通道"

    components: list[str] = []
    for label, prefix in (
        ("上部结构", "superstructure"),
        ("下部结构", "substructure"),
        ("桥面系", "deck"),
    ):
        component_score = _valid_score_value(_summary_field(summary, f"{prefix}_score"))
        component_grade = _valid_grade_value(_summary_field(summary, f"{prefix}_grade"))
        if not component_score and not component_grade:
            continue
        item = label
        if component_score:
            item += f"评分 {component_score} 分"
        if component_grade:
            item += f"（{component_grade}）"
        components.append(item)

    sentence = ""
    if score and grade:
        sentence = (
            f"经综合评定，{subject}总体技术状况评分 {score} 分，"
            f"总体技术状况等级为 {grade}。"
        )
    elif grade:
        sentence = f"经综合评定，{subject}总体技术状况等级为 {grade}。"
    elif score:
        sentence = f"经综合评定，{subject}总体技术状况评分 {score} 分。"
    elif components:
        sentence = "分项技术状况评定结果为："
    else:
        return ""

    if components:
        if sentence.endswith("："):
            sentence += "，".join(components) + "。"
        else:
            sentence += "其中，" + "，".join(components) + "。"
    return sentence

def _safe_summary_fact(value: str) -> str:
    compact = _compact(value)
    if not compact or len(compact) > 500:
        return ""
    if _has_any(compact, ("评估分级", "等级划分", "见下表", "安全性评估内容")):
        return ""
    return value


def _source_causes(
    units: Sequence[_TextUnit],
    summary: object,
    recommendation_blocks: set[int],
    heading_blocks: set[int],
) -> tuple[str, ...]:
    candidates: list[str] = []
    for unit in units:
        if unit.block_index in recommendation_blocks or _is_heading_unit(unit, heading_blocks):
            continue
        for value in _split_sentences(unit.text, split_semicolon=True):
            compact = _compact(value)
            if any(marker in compact for marker in _CAUSE_NOISE_PHRASES):
                continue
            if (
                _EXPLICIT_CAUSE_RE.search(compact)
                and len(compact) >= 8
                and _has_any(compact, _DISEASE_WORDS)
                and not _is_title(value)
                and not _is_noise(value)
                and not _is_action(value)
                and not compact.startswith(_CAUSE_EXCLUDED_PREFIXES)
            ):
                candidates.append(_truncate_fact(value, 300))
    for field in ("overall_conclusion", "risk_points"):
        value = _summary_field(summary, field)
        for candidate in _split_sentences(value, split_semicolon=True):
            compact = _compact(candidate)
            if (
                _EXPLICIT_CAUSE_RE.search(compact)
                and _has_any(compact, _DISEASE_WORDS)
                and not _is_action(candidate)
                and not compact.startswith(_CAUSE_EXCLUDED_PREFIXES)
            ):
                candidates.append(_truncate_fact(candidate, 300))
    return _unique(candidates)


def _structured_causes(
    defects: Sequence[object],
    source_causes: Sequence[str],
) -> tuple[str, ...]:
    # Defect labels are not causal evidence.  Return only sentences that the
    # source report itself states as a cause; otherwise leave the field empty.
    return _unique(source_causes)[:4]


def _impact_sources(
    units: Sequence[_TextUnit],
    heading_blocks: set[int],
) -> tuple[tuple[str | None, str], ...]:
    result: list[tuple[str | None, str]] = []
    for unit in units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        for value in _split_sentences(unit.text, split_semicolon=True):
            compact = _compact(value)
            if not _is_impact(value) or not _has_any(compact, _SAFETY_EVIDENCE_WORDS):
                continue
            if _is_action(value):
                continue
            if value.rstrip().endswith(("：", ":")) or "桥梁博士" in compact:
                continue
            if (
                _is_noise(value)
                or _is_title(value)
                or len(compact) > 500
                or _has_any(compact, _SAFETY_NOISE_WORDS)
                or _has_any(compact, ("评估分级", "重要桥梁", "一般桥梁", "城市桥梁按"))
            ):
                continue
            result.append((_classify_component(value), _truncate_fact(value, 300)))
    seen: set[tuple[str | None, str]] = set()
    return tuple(item for item in result if not (item in seen or seen.add(item)))


def _safety_topic(value: str, category: str | None) -> str:
    compact = _compact(value)
    if "承载" in compact or "荷载" in compact:
        return "承载能力"
    if "耐久" in compact:
        return "耐久性"
    if any(marker in compact for marker in ("行车", "通行", "行人")):
        return "通行安全"
    if "使用功能" in compact or "功能" in compact:
        return "使用功能"
    if "结构安全" in compact or "安全" in compact:
        return "结构安全"
    return category or "总体"


def _safety_polarity(value: str) -> str:
    compact = _compact(value)
    if any(marker in compact for marker in (
        "不影响", "未影响", "暂不影响", "满足要求", "符合要求",
        "承载能力满足", "安全运营", "处于弹性工作状态", "工作性能良好",
    )):
        return "reassuring"
    if any(marker in compact for marker in (
        "影响", "削弱", "降低", "危及", "风险", "隐患", "不满足", "不足",
    )):
        return "adverse"
    return "neutral"


def _safety_rank(value: str, category: str | None) -> tuple[int, int, int]:
    compact = _compact(value)
    score = 0
    if any(marker in compact for marker in ("综合评定", "总体评定", "最终评定", "安全性评估")):
        score += 8
    if any(marker in compact for marker in ("承载能力", "结构安全", "使用功能", "耐久性")):
        score += 5
    if any(marker in compact for marker in ("满足要求", "符合要求", "不影响", "影响")):
        score += 3
    if category is None:
        score += 2
    return (-score, len(compact), 0)


def _select_safety_impacts(
    values: Sequence[tuple[str | None, str]],
    *,
    limit: int = 3,
) -> tuple[str, ...]:
    """Select non-contradictory final safety conclusions from source text."""

    by_topic: dict[str, tuple[tuple[int, int, int], str, str]] = {}
    for category, value in values:
        text = _clean_text(value, strip_number=False).strip("，,；;。 ")
        compact = _compact(text)
        if not text or len(compact) > 320:
            continue
        if any(marker in compact for marker in (
            "已有证据为", "报告未明确", "评估分级", "等级划分", "检测依据",
            "建议", "应及时", "需及时", "维修", "修复", "处治", "处置",
        )):
            continue
        topic = _safety_topic(text, category)
        rank = _safety_rank(text, category)
        polarity = _safety_polarity(text)
        current = by_topic.get(topic)
        if current is None or rank < current[0]:
            by_topic[topic] = (rank, text, polarity)
            continue
        # At equal evidence quality prefer a qualified/reassuring final
        # conclusion over a generic adverse phrase; this prevents simultaneous
        # "影响" and "不影响" statements for the same topic.
        if rank == current[0] and polarity == "reassuring" and current[2] != "reassuring":
            by_topic[topic] = (rank, text, polarity)

    ordered = sorted(by_topic.values(), key=lambda item: item[0])
    return tuple(
        text + ("" if text.endswith("。") else "。")
        for _, text, _ in ordered[:limit]
    )


def _structured_safety_impact(
    summary: object,
    defects: Sequence[object],
    impact_sources: Sequence[tuple[str | None, str]],
) -> tuple[str, ...]:
    """Return only explicit, non-contradictory report safety conclusions."""

    candidates = list(impact_sources)
    risk = _safe_summary_fact(_summary_field(summary, "risk_points"))
    if risk and _is_impact(risk) and not _is_action(risk):
        candidates.append((None, risk))
    return _select_safety_impacts(candidates)


def _structured_treatments(recommendations: Sequence[object]) -> tuple[str, ...]:
    result: list[str] = []
    for recommendation in recommendations:
        content = _field_value(recommendation, "content")
        if content:
            result.append(_clean_text(content, strip_number=False))
    return _unique(result)


def _truncate_fact(value: str, limit: int) -> str:
    text = _clean_text(value, strip_number=False)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，,；; ") + "…"


def _document_units(document: DocumentModel) -> tuple[_TextUnit, ...]:
    return tuple(
        _TextUnit(block.block_index, str(getattr(block, "raw_text", "")))
        for block in document.blocks
        if _compact(str(getattr(block, "raw_text", "")))
    )


def _heading_blocks(routes: Sequence[object]) -> set[int]:
    result: set[int] = set()
    for route in routes:
        heading = getattr(route, "heading", None)
        if heading is not None:
            result.add(int(getattr(heading, "block_index", -1)))
    return result


def _route_blocks(routes: Sequence[object], categories: set[str]) -> set[int]:
    result: set[int] = set()
    for route in routes:
        category = getattr(getattr(route, "category", ""), "value", getattr(route, "category", ""))
        if str(category) not in categories:
            continue
        result.update(int(getattr(block, "block_index", -1)) for block in getattr(route, "blocks", ()))
    return result


def _route_units(
    routes: Sequence[object],
    category_name: str,
    excluded_blocks: set[int],
) -> tuple[_TextUnit, ...]:
    result: list[_TextUnit] = []
    seen: set[tuple[int, str]] = set()
    for route in routes:
        category = getattr(getattr(route, "category", ""), "value", getattr(route, "category", ""))
        if str(category) != category_name:
            continue
        for block in getattr(route, "blocks", ()):
            if not isinstance(block, ParagraphBlock) or block.block_index in excluded_blocks:
                continue
            key = (block.block_index, block.raw_text)
            if key not in seen and _compact(block.raw_text):
                seen.add(key)
                result.append(_TextUnit(block.block_index, block.raw_text))
    return tuple(sorted(result, key=lambda item: item.block_index))


def _section_window(
    units: Sequence[_TextUnit],
    start_name: str,
    end_names: set[str],
) -> tuple[_TextUnit, ...]:
    start = _last_marker(units, _SECTION_PATTERNS[start_name])
    if start is None:
        return ()
    end = _first_marker_after(units, end_names, start)
    return _slice_units(units, start, end)


def _pre_section_units(units: Sequence[_TextUnit], section_name: str) -> tuple[_TextUnit, ...]:
    marker = _last_marker(units, _SECTION_PATTERNS[section_name])
    if marker is None:
        return tuple(units)
    result: list[_TextUnit] = []
    for unit in units:
        if unit.block_index < marker[0]:
            result.append(unit)
        elif unit.block_index == marker[0] and marker[1] > 0:
            result.append(_TextUnit(unit.block_index, unit.text[: marker[1]]))
    return tuple(result)


def _last_marker(units: Sequence[_TextUnit], pattern: re.Pattern[str]) -> tuple[int, int, int, int] | None:
    matches: list[tuple[int, int, int, int]] = []
    for order, unit in enumerate(units):
        for match in pattern.finditer(unit.text):
            matches.append((unit.block_index, match.start(), match.end(), order))
    return max(matches, key=lambda item: (item[0], item[1])) if matches else None


def _first_marker_after(
    units: Sequence[_TextUnit], names: set[str], start: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    matches: list[tuple[int, int, int, int]] = []
    for name in names:
        marker = _last_marker_after(units, _SECTION_PATTERNS[name], start)
        if marker is not None:
            matches.append(marker)
    return min(matches, key=lambda item: (item[0], item[1])) if matches else None


def _last_marker_after(
    units: Sequence[_TextUnit], pattern: re.Pattern[str], start: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    matches: list[tuple[int, int, int, int]] = []
    for order, unit in enumerate(units):
        for match in pattern.finditer(unit.text):
            marker = (unit.block_index, match.start(), match.end(), order)
            if (unit.block_index, match.start()) > (start[0], start[1]):
                matches.append(marker)
    return min(matches, key=lambda item: (item[0], item[1])) if matches else None


def _slice_units(
    units: Sequence[_TextUnit],
    start: tuple[int, int, int, int],
    end: tuple[int, int, int, int] | None,
) -> tuple[_TextUnit, ...]:
    result: list[_TextUnit] = []
    for unit in units:
        if unit.block_index < start[0]:
            continue
        if unit.block_index == start[0]:
            text = unit.text[start[2] :]
        else:
            text = unit.text
        if end is not None:
            if unit.block_index > end[0]:
                break
            if unit.block_index == end[0]:
                offset = start[2] if unit.block_index == start[0] else 0
                text = text[: end[1] - offset]
        if _compact(text):
            result.append(_TextUnit(unit.block_index, text))
    return tuple(result)


def _detailed_conclusion(
    summary: BridgeSummary,
    inspection_units: Sequence[_TextUnit],
    safety_units: Sequence[_TextUnit],
    all_units: Sequence[_TextUnit],
    heading_blocks: set[int],
) -> tuple[str, ...]:
    result: list[str] = []
    score = _score_sentence(summary)
    if score:
        result.append(score)

    descriptions: list[str] = []
    for unit in inspection_units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        raw = _clean_text(unit.text, strip_number=False)
        if _is_noise(raw) or _is_title(raw):
            continue
        candidates = _split_sentences(raw, split_semicolon=True)
        for value in candidates:
            text = _compact(value)
            if not _has_defect(text) or not _has_any(text, _COMPONENT_WORDS):
                continue
            if _is_noise(value) or _is_cause(value) or _is_impact(value) or _is_action(value):
                continue
            if _has_any(text, ("检查", "检测", "试验", "照片", "见表", "见图", "规范", "测点", "指数", "评分", "验算", "表格", "实测", "结构尺寸", "安全性评估", "处理建议", "处理意见", "变形规律", "变形稳定", "各项观测", "荷载", "加载")):
                continue
            descriptions.append(_truncate_description(value))
    if not descriptions:
        overview_units = _section_window(all_units, "overview", {"safety"})
        if not overview_units:
            overview_units = _pre_section_units(all_units, "safety")
        for unit in overview_units:
            raw = _clean_text(unit.text, strip_number=False)
            if _is_noise(raw) or _is_title(raw):
                continue
            for value in _split_sentences(raw, split_semicolon=True):
                text = _compact(value)
                if not _has_defect(text) or not _has_any(text, _COMPONENT_WORDS):
                    continue
                if _is_noise(value) or _is_cause(value) or _is_impact(value) or _is_action(value):
                    continue
                if _has_any(text, ("检查", "检测", "试验", "照片", "见表", "见图", "规范", "测点", "指数", "评分", "验算", "表格", "实测", "结构尺寸", "安全性评估", "处理建议", "处理意见", "变形规律", "变形稳定", "各项观测", "荷载", "加载")):
                    continue
                descriptions.append(_truncate_description(value))
    descriptions = _unique(value for value in descriptions if len(_compact(value)) >= 4)
    if descriptions:
        result.append("；".join(descriptions[:6]))

    state: list[str] = []
    for unit in safety_units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        raw = _clean_text(unit.text, strip_number=False)
        if _is_noise(raw) or _is_title(raw):
            continue
        for value in _split_sentences(raw):
            text = _compact(value)
            if _is_cause(value) or _is_impact(value) or _is_action(value):
                continue
            if _has_any(text, ("检测表明", "满足", "合格率", "承载能力", "状况良好", "外观良好", "试验结果", "检算结果")):
                state.append(value)
    state = _unique(state)
    if state:
        result.append(" ".join(state))

    conclusion = _section_window(all_units, "conclusion", {"treatment"})
    final_facts: list[str] = []
    for unit in conclusion:
        for value in _split_sentences(unit.text):
            text = _compact(value)
            if _is_noise(value) or _is_title(value) or _is_cause(value) or _is_impact(value) or _is_action(value):
                continue
            if _has_any(text, _FACT_WORDS):
                final_facts.append(value)
    final_facts = _unique(final_facts)
    if final_facts:
        result.append(" ".join(final_facts))
    return _unique(result)


def _causes(units: Sequence[_TextUnit], heading_blocks: set[int]) -> tuple[str, ...]:
    result: list[str] = []
    for unit in units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        raw = _clean_text(unit.text, strip_number=False)
        if _is_noise(raw) or _is_title(raw):
            continue
        for value in _split_sentences(raw, split_semicolon=True):
            compact_value = _compact(value)
            if any(marker in compact_value for marker in _CAUSE_NOISE_PHRASES):
                continue
            if _is_cause(value) and _has_any(compact_value, _DISEASE_WORDS + _COMPONENT_WORDS):
                if _has_any(_compact(value), ("检查建议", "巡查建议", "定期检查", "日常检查")):
                    continue
                result.append(value)
    return _unique(result)


def _safety_impact(units: Sequence[_TextUnit], heading_blocks: set[int]) -> tuple[str, ...]:
    buckets: dict[str, list[str]] = {"deck": [], "upper": [], "lower": [], "overall": []}
    for unit in units:
        if _is_heading_unit(unit, heading_blocks):
            continue
        raw = _clean_text(unit.text, strip_number=False)
        if _is_noise(raw) or _is_title(raw):
            continue
        for value in _split_sentences(raw, split_semicolon=True):
            text = _compact(value)
            if value.rstrip().endswith(("：", ":")) or ("由桥梁博士" in text and "算得" in text):
                continue
            if _is_cause(value) or _is_action(value) or not _has_any(text, _IMPACT_WORDS):
                continue
            if not _has_any(text, _DISEASE_WORDS + _COMPONENT_WORDS):
                continue
            if _has_any(text, ("检查建议", "巡查建议", "定期检查", "日常检查")):
                continue
            if _has_any(text, ("桥面", "路面", "栏杆", "护栏", "泄水", "行车", "行人")):
                bucket = "deck"
            elif _has_any(text, ("上部", "梁", "板", "腹板", "翼板", "湿接缝")):
                bucket = "upper"
            elif _has_any(text, ("下部", "桥台", "盖梁", "支座", "桥墩", "墩台")):
                bucket = "lower"
            else:
                bucket = "overall"
            buckets[bucket].append(value)
    return tuple(
        _clean_text(" ".join(_unique(values)), strip_number=False)
        for values in buckets.values()
        if values
    )


def _treatments(recommendations: Sequence[Recommendation]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for recommendation in recommendations:
        key = (recommendation.category, recommendation.location, recommendation.content)
        if key in seen or not recommendation.content.strip():
            continue
        seen.add(key)
        result.append(
            "；".join(
                part
                for part in (
                    recommendation.category,
                    recommendation.location,
                    recommendation.content,
                )
                if part.strip()
            )
        )
    return tuple(result)


def _score_sentence(summary: BridgeSummary) -> str:
    def value(item: object) -> str:
        return str(item or "").strip()

    if not value(summary.overall_score) and not value(summary.overall_grade):
        return ""
    result = (
        f"经综合评定，该桥总体技术状况评分 {value(summary.overall_score)} 分，"
        f"总体技术状况等级为 {value(summary.overall_grade)}。"
    )
    parts: list[str] = []
    for label, score, grade in (
        ("上部结构", summary.superstructure_score, summary.superstructure_grade),
        ("下部结构", summary.substructure_score, summary.substructure_grade),
        ("桥面系", summary.deck_score, summary.deck_grade),
    ):
        if value(score) or value(grade):
            parts.append(f"{label}评分 {value(score)} 分（{value(grade)}）。")
    if parts:
        result += "其中，" + " ".join(parts)
    return _clean_text(result, strip_number=False)


def _split_sentences(value: str, *, split_semicolon: bool = False) -> tuple[str, ...]:
    pattern = r"(?<=[。！？!?；;])\s*" if split_semicolon else r"(?<=[。！？!?])\s*"
    return tuple(
        cleaned
        for part in re.split(pattern, " ".join(value.split()))
        if (cleaned := _clean_text(part)) and not _is_noise(cleaned)
    )


def _clean_text(value: str, *, strip_number: bool = True) -> str:
    result = re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip(" \t;；")
    if strip_number:
        result = _NUMBER_PREFIX_RE.sub("", result)
    return result.strip(" \t;；")


def _truncate_description(value: str) -> str:
    result = _clean_text(value, strip_number=False)
    for marker in ("具体病害情况见", "现场病害典型照片见"):
        if marker in result:
            result = result.split(marker, 1)[0].rstrip(" 。；;")
    return result


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _has_any(value: str, words: Iterable[str]) -> bool:
    return any(word in value for word in words)


def _has_defect(value: str) -> bool:
    return _has_any(value, _DISEASE_WORDS) or bool(re.search(r"无(?:泄水|排水|防护|设施|孔盖)", value))


def _is_cause(value: str) -> bool:
    text = _compact(value)
    for word in _CAUSE_WORDS:
        if word != "因" and word in text:
            return True
    return bool(re.search(r"(?<!此)因(?!此)", text))


def _is_impact(value: str) -> bool:
    return _has_any(_compact(value), _IMPACT_WORDS)


def _is_action(value: str) -> bool:
    return _has_any(_compact(value), _ACTION_WORDS)


def _is_title(value: str) -> bool:
    return bool(_TITLE_RE.fullmatch(_compact(value).strip("：:。.;；，,、")))


def _is_heading_unit(unit: _TextUnit, heading_blocks: set[int]) -> bool:
    if unit.block_index not in heading_blocks:
        return False
    text = _clean_text(unit.text, strip_number=False)
    return len(_compact(text)) <= 80 and _is_title(text)


def _is_noise(value: str) -> bool:
    text = _compact(value)
    if len(text) < 4 or "……" in value or "...." in value:
        return True
    if re.fullmatch(r"(?:表|图|照片)?[0-9一二三四五六七八九十.-]+", text):
        return True
    if re.fullmatch(r"(?:第)?[0-9一二三四五六七八九十]+页", text):
        return True
    if re.match(r"^(?:图|照片)\s*[0-9]", text):
        return True
    if re.match(r"^表\s*[0-9]", text) and not _has_any(text, ("病害为", "病害主要", "主要病害")):
        return True
    if "目录" in text and not _has_any(text, _DISEASE_WORDS + _IMPACT_WORDS):
        return True
    return False


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _compact(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)
