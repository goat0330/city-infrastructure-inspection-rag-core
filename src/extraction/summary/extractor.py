"""Deterministic extraction of bridge identity, scores, dates, and summary facts.

The extractor consumes the native :class:`~src.contracts.DocumentModel` produced
by ``parse_docx``.  It deliberately keeps every observed candidate and its Word
anchor; the selected value is only a deterministic view over those candidates.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
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
from ..grade_mapping import GRADE_SCORE_PAIRS, apply_grade_mode, normalize_grade_mode
from .facility_context import (
    FacilityContext,
    FieldState,
    build_field_states,
    infer_facility_semantics,
)


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
_INTERNAL_FIELDS = ("inspection_date",)
_CONTEXT_STATE_FIELDS = (
    *_SUMMARY_FIELDS,
    "facility_name",
    "facility_type_raw",
    "facility_type",
    "facility_noun",
    "inspection_date",
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
    "bridge_name": (
        "桥梁名称",
        "桥名",
        "工程名称",
        "设施名称",
        "桥梁工程名称",
        "工程设施名称",
        "工程/设施名称",
        "工程（设施）名称",
        "项目名称",
        "桥式通道名称",
        "人行天桥名称",
        "地通道名称",
        "人行通道名称",
        "人行地通道名称",
        "地下通道名称",
        "车行下穿道名称",
        "车行地通道名称",
        "车行通道名称",
        "下穿道名称",
        "隧道名称",
        "涵洞名称",
        "道路名称",
        "通道名称",
    ),
    "bridge_id": ("桥梁编号", "桥梁ID", "桥梁Id", "桥梁id"),
    "report_date": (
        "报告日期",
        "出具日期",
        "报告出具日期",
        "报告发出日期",
        "签发日期",
        "签字日期",
    ),
    "inspection_date": (
        "检测日期",
        "检验日期",
        "检查日期",
        "检测结束日期",
        "检测完成日期",
        "检测结束时间",
        "检测时间",
        "检查时间",
    ),
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
    "overall_conclusion": (
        "主要结论",
        "评估结论",
        "检测结果",
        "外观及专项检测结果综述",
        "综合评估",
        "综合结论",
        "总体结论",
        "检测结论",
        "检查结论",
    ),
    "risk_points": (
        "主要风险点",
        "主要风险",
        "主要病害",
        "突出病害",
        "突出风险",
        "风险点",
        "安全风险",
        "安全隐患",
    ),
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
    "major_conclusion": 760,
    "conclusion_review": 740,
    "comprehensive_assessment": 720,
    "major_risk": 700,
    "body_name": 580,
    "cover_facility_name": 520,
    "risk_fallback": 420,
    "risk_label": 300,
    "overall_assessment_table": 400,
    "cover": 320,
    "facility_name": 600,
    "cover_name": 500,
    "conclusion_name": 400,
    "section_score_table": 300,
    "section_score": 280,
    "sign": 260,
    "summary_page": 200,
    "detection": 180,
    "conclusion": 100,
    "safety_assessment": 100,
    "paragraph": 80,
    "recommendations_table": 300,
    "paired_score_grade": 820,
    "component_dr": 760,
    "bci": 500,
    "underpass_conclusion": 520,
    "project_name": 80,
    "filename": 40,
    # Filenames are useful release metadata, but they are not the authoritative
    # report body.  They may fill a missing value or surface a conflict; they
    # must never beat an explicit table/label/conclusion candidate.
    "filename_facility": 60,
    "filename_grade": 60,
    "filename_history": 55,
    "previous_detection": 680,
    "history_comparison": 660,
}

_DATE_PRIORITY = {
    "cover": 300,
    "sign": 290,
    "cover_range_end": 230,
    "detection_end": 220,
    "detection": 200,
    "range": 100,
}
_REPORT_DATE_KINDS = {"cover", "sign", "cover_range_end"}
_INSPECTION_DATE_KINDS = {"detection", "detection_end", "range"}
_DATE_RE = re.compile(
    r"(?P<year>(?:19|20)\d{2})"
    r"(?:年\s*(?P<month_cn>1[0-2]|0?[1-9])月(?:\s*(?P<day_cn>3[01]|[12]\d|0?[1-9])日)?"
    r"|[./-]\s*(?P<month>1[0-2]|0?[1-9])(?:[./-]\s*(?P<day>3[01]|[12]\d|0?[1-9]))?)"
)
_DATE_RANGE_RE = re.compile(
    r"(?P<year1>(?:19|20)\d{2})[./-]\s*"
    r"(?P<month1>1[0-2]|0?[1-9])"
    r"(?:[./-]\s*(?P<day1>3[01]|[12]\d|0?[1-9]))?\s*"
    r"(?:~|～|至|到)\s*"
    r"(?:(?P<year2>(?:19|20)\d{2})[./-]\s*)?"
    r"(?P<month2>1[0-2]|0?[1-9])"
    r"(?:[./-]\s*(?P<day2>3[01]|[12]\d|0?[1-9]))?"
)
_CN_DATE_RANGE_RE = re.compile(
    r"(?P<year1>(?:19|20)\d{2})年\s*"
    r"(?P<month1>1[0-2]|0?[1-9])月"
    r"(?:\s*(?P<day1>3[01]|[12]\d|0?[1-9])日)?\s*"
    r"(?:~|～|至|到|-|—)\s*"
    r"(?:(?P<year2>(?:19|20)\d{2})年\s*)?"
    r"(?P<month2>1[0-2]|0?[1-9])月"
    r"(?:\s*(?P<day2>3[01]|[12]\d|0?[1-9])日)?"
)
_SCORE_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?")
_GRADE_RE = re.compile(r"(?:[A-Ea-e]\s*级?|[一二三四五六]类|优等?|良好?|中等?|差)")
_RECOMMENDATION_COUNT_RE = re.compile(r"(\d+)\s*条")

_BCI_SCORE_RE = re.compile(r"BCI\s*([mMkKsSxX]?)\s*[=＝]\s*(\d+(?:\.\d+)?)")
_BCI_COMPONENT = {"m": "deck", "s": "superstructure", "k": "superstructure", "x": "substructure"}
_BSI_COMPONENT = {"m": "deck", "s": "superstructure", "x": "substructure"}
_BSI_TOKEN_RE = re.compile(r"BSI\s*([mMsSxX])", flags=re.IGNORECASE)
_BSI_PAIRED_VALUE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*分?\s*[（(]\s*([A-Ea-e]\s*级)\s*[）)]"
)
_BSI_DIRECT_PAIR_RE = re.compile(
    r"BSI\s*([mMsSxX])\s*(?:=|＝|:|：|为|是)?\s*"
    r"(\d+(?:\.\d+)?)\s*分?\s*[（(]\s*([A-Ea-e]\s*级)\s*[）)]",
    flags=re.IGNORECASE,
)
_GRADE_AFTER_RE = re.compile(
    r"(?:结构状况)?评定(?:等级|级别)?(?:为|是|[:：=])?\s*"
    r"([A-Ea-e]\s*级|[一二三四五六]类)"
)
_OVERALL_GRADE_RE = re.compile(
    r"(?<!下部结构)(?<!上部结构)(?<!桥面系)整体技术状况等级(?:评定|定)?为\s*([A-Ea-e]\s*级|[一二三四五六]类)"
)
_CLASS_OVERALL_GRADE_RE = re.compile(
    r"(?<!下部结构)(?<!上部结构)(?<!桥面系)"
    r"(?:桥梁|本桥|本设施)?(?:总体|整体)?技术状况(?:等级|级别)(?:评定)?"
    r"(?:为|是|[:：=])?\s*([一二三四五六]类)"
)
_UNDERPASS_GRADE_RE = re.compile(r"技术状况总评\s*[，,、\t\s]*([一二三四五六]类)")
_UNDERPASS_CONCLUSION_GRADE_RE = re.compile(r"满足\s*([一二三四五六]类)\s*技术标准")

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
    "桥梁整体技术状况等级",
    "部位名称技术状况指数权重BCI",
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

_GENERIC_BRIDGE_NAME_RE = (
    re.compile(
        r"^(?:(?:19|20)\d{2}年度|年度)?"
        r"桥梁(?:等结构设施)?定期检测(?:评估)?(?:项目|报告)?$"
    ),
    re.compile(r"^(?:检测评估项目|检测评估|检测报告|桥梁检测报告|报告)$"),
)
_BRIDGE_NAME_RE = re.compile(
    r"(?P<name>[\u3400-\u9fffA-Za-z0-9ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ#＃+—\-]{2,80}"
    r"(?:人行地通道|人行地道|地下通道|人行通道|车行下穿道|下穿道|人行天桥|桥式通道|匝道桥|立交桥|大桥|中桥|小桥|天桥|隧道|涵洞|道路|桥(?!梁|等)|通道|立交))"
)
_GENERIC_FACILITY_NAMES = frozenset(
    {"通道", "人行通道", "人行地通道", "人行地道", "地下通道", "车行下穿道", "下穿道", "隧道", "涵洞", "道路"}
)
_HIGH_CONCLUSION_KINDS = frozenset(
    {"major_conclusion", "conclusion_review", "comprehensive_assessment"}
)
_CONCLUSION_FRAGMENT_MARKERS = (
    "芯样",
    "强度检测结果",
    "混凝土强度",
    "条石强度",
    "抗压强度",
    "混凝土抗压强度",
    "条石抗压强度",
    "保护层厚度",
    "碳化深度",
    "单项试验",
)
_RISK_DEFECT_MARKERS = (
    "破损",
    "裂缝",
    "开裂",
    "渗水",
    "泛碱",
    "锈蚀",
    "露筋",
    "变形",
    "沉降",
    "缺失",
    "堵塞",
    "脱落",
    "病害",
)
_RISK_ADVICE_MARKERS = (
    "建议",
    "及时",
    "修复",
    "维修",
    "修补",
    "处理",
    "处置",
    "加固",
    "封闭",
    "灌缝",
    "清理",
)
_RISK_CONSEQUENCE_MARKERS = (
    "影响",
    "降低",
    "削弱",
    "危及",
    "隐患",
    "安全",
    "耐久",
    "承载",
    "受力",
    "通行",
    "行车",
    "行人",
    "使用功能",
)
_SUMMARY_ACTION_MARKERS = (
    "建议",
    "应及时",
    "需及时",
    "维修",
    "修复",
    "修补",
    "处治",
    "处置",
    "加固",
    "更换",
    "清理",
    "养护",
    "可直接用",
    "环氧砂浆",
)
_SUMMARY_NOISE_MARKERS = (
    "目录",
    "检测依据",
    "评定依据",
    "技术规范",
    "评定标准",
    "检查结果表",
    "病害分布表",
    "照片",
    "示意图",
    "布置图",
    "结构检算",
    "荷载试验",
    "静载试验",
    "动载试验",
    "自振频率",
    "冲击系数",
    "混凝土强度",
    "保护层合格率",
    "桥梁博士",
    "计算结果",
)


def _clean(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _split_summary_sentences(value: str) -> tuple[str, ...]:
    """Split a noisy summary cell into source-faithful sentence candidates."""

    result: list[str] = []
    for part in re.split(r"[\r\n]+|(?<=[。；;！？!?])", value or ""):
        text = _clean(part).strip("，,；;。． ")
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _is_raw_summary_data(value: str) -> bool:
    compact = _compact(value)
    if any(marker in compact for marker in _SUMMARY_NOISE_MARKERS):
        return True
    numeric_hits = len(
        re.findall(r"\d+(?:\.\d+)?(?:MPa|mm|㎜|m/s2|Hz|kN|kN·m|%|℃)", value)
    )
    has_judgement = any(
        marker in compact
        for marker in (
            "满足要求",
            "符合要求",
            "承载能力满足",
            "安全性评估",
            "技术状况等级",
            "总体技术状况",
        )
    )
    return numeric_hits >= 3 and not has_judgement


def _normalise_overall_conclusion(value: str) -> str:
    """Keep only concise formal conclusions, never an entire inspection body."""

    selected: list[str] = []
    total = 0
    for sentence in _split_summary_sentences(value):
        compact = _compact(sentence)
        if not compact or _is_raw_summary_data(sentence):
            continue
        if any(marker in compact for marker in _SUMMARY_ACTION_MARKERS):
            sentence = re.split(
                r"[，,；;。]?\s*(?=(?:建议|应及时|需及时|维修|修复|修补|处治|处置|加固|更换|清理|养护|可直接用|环氧砂浆))",
                sentence,
                maxsplit=1,
            )[0].strip("，,；;。 ")
            compact = _compact(sentence)
            if not sentence:
                continue
        has_overall = any(
            marker in compact
            for marker in (
                "总体",
                "整体",
                "综合评定",
                "技术状况",
                "安全性评估",
                "承载能力",
                "满足要求",
                "符合要求",
                "正常使用",
                "安全运营",
            )
        )
        has_defect_fact = any(marker in compact for marker in _RISK_DEFECT_MARKERS) and any(
            marker in compact
            for marker in (
                "桥面",
                "主梁",
                "梁体",
                "支座",
                "桥墩",
                "桥台",
                "栏杆",
                "护栏",
                "顶板",
                "侧墙",
                "结构",
                "构件",
            )
        )
        if not (has_overall or has_defect_fact):
            continue
        sentence = sentence[:180].rstrip("，,；; ")
        if not sentence or sentence in selected:
            continue
        separator = 1 if selected else 0
        if total + separator + len(sentence) > 250:
            remaining = 250 - total - separator
            if remaining < 24:
                break
            sentence = sentence[:remaining].rstrip("，,；; ")
        selected.append(sentence)
        total += separator + len(sentence)
        if len(selected) >= 4 or total >= 250:
            break
    return "；".join(selected)


def _normalise_risk_points(value: str) -> str:
    """Keep at most three report-backed defect→consequence statements."""

    selected: list[str] = []
    total = 0
    for sentence in _split_summary_sentences(value):
        compact = _compact(sentence)
        if not compact or len(compact) > 260:
            continue
        if any(marker in compact for marker in _SUMMARY_NOISE_MARKERS):
            continue
        if any(marker in compact for marker in _SUMMARY_ACTION_MARKERS):
            continue
        if re.search(r"(?:19|20)\d{2}年.*(?:检测|检查|维修|加固)", compact):
            continue
        if not any(marker in compact for marker in _RISK_DEFECT_MARKERS):
            continue
        if not any(marker in compact for marker in _RISK_CONSEQUENCE_MARKERS):
            continue
        sentence = sentence[:120].rstrip("，,；; ")
        if not sentence or sentence in selected:
            continue
        separator = 1 if selected else 0
        if total + separator + len(sentence) > 200:
            remaining = 200 - total - separator
            if remaining < 24:
                break
            sentence = sentence[:remaining].rstrip("，,；; ")
        selected.append(sentence)
        total += separator + len(sentence)
        if len(selected) >= 3 or total >= 200:
            break
    return "；".join(selected)


def _alias_pattern(alias: str) -> str:
    """Match a label even when Word inserts spaces between its characters."""

    return r"\s*".join(re.escape(character) for character in alias)


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
    facility_context: FacilityContext = field(default_factory=FacilityContext)
    field_states: Mapping[str, FieldState] = field(default_factory=dict)

    @property
    def bridge_id_candidates(self) -> tuple[SummaryCandidate, ...]:
        return self.candidates.get("bridge_id", ())

    @property
    def report_date_candidates(self) -> tuple[SummaryCandidate, ...]:
        return self.candidates.get("report_date", ())

    @property
    def inspection_date_candidates(self) -> tuple[SummaryCandidate, ...]:
        return self.candidates.get("inspection_date", ())

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
            "inspection_date_candidates": [candidate.to_dict() for candidate in self.inspection_date_candidates],
            "facility_context": self.facility_context.to_dict(),
            "field_states": dict(self.field_states),
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
        if key == "inspection_date_candidates":
            return self.inspection_date_candidates
        if key == "facility_context":
            return self.facility_context
        if key == "field_states":
            return self.field_states
        if key == "inspection_date":
            return self.facility_context.inspection_date
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
            field: [] for field in (*_SUMMARY_FIELDS, *_INTERNAL_FIELDS)
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
        normalized_value = (
            _clean(value)
            if field == "bridge_name" and source_kind == "filename_facility"
            else _normalise_field_value(field, value)
        )
        candidate = SummaryCandidate(
            field=field,
            value=normalized_value,
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
            _scope_from_label(candidate.label),
        )
        if any(
            (
                item.field,
                item.value,
                item.source_kind,
                _source_sort_key(item.source),
                _scope_from_label(item.label),
            )
            == identity
            for item in self.values[field]
        ):
            return
        self.values[field].append(candidate)


def extract_summary(
    document: DocumentModel,
    routes: Iterable[object] | None = None,
    *,
    grade_mode: str | None = None,
) -> SummaryExtraction:
    """Extract a deterministic bridge summary from a parsed Word document.

    ``routes`` may be supplied by the shared section router.  When omitted, the
    router is run locally.  ``report`` mode keeps grades stated by the report.
    The explicit ``generic`` A/B mode maps only already-extracted scores to
    grades; it never derives or changes a score from a grade.
    """

    if not isinstance(document, DocumentModel):
        raise TypeError("extract_summary expects a parsed DocumentModel")

    resolved_grade_mode = normalize_grade_mode(grade_mode)
    selected_routes = tuple(route_sections(document) if routes is None else routes)
    route_categories = _route_categories(selected_routes)
    collector = _CandidateCollector()
    first_heading = _first_heading_index(document, selected_routes)

    blocks = tuple(document.blocks)
    previous_section_blocks = _previous_section_block_indexes(blocks)
    for block in blocks:
        # Chapter 1.2 describes the prior inspection.  Its BCI/grade must never
        # compete with current-period summary facts.  Dedicated history parsing
        # below owns every fact in this window.
        if block.block_index in previous_section_blocks:
            continue
        categories = route_categories.get(block.block_index, set())
        if isinstance(block, TableBlock):
            source_kind = _table_source_kind(block, categories, blocks)
            _extract_table(
                block,
                source_kind,
                collector,
                scope=_score_scope(block, blocks),
            )
        elif isinstance(block, ParagraphBlock):
            source_kind = _paragraph_source_kind(block, categories, first_heading)
            _extract_paragraph(block, source_kind, collector)

    _extract_route_text(selected_routes, collector)
    _extract_cover_names(blocks, first_heading, collector)
    _extract_cover_dates(blocks, first_heading, collector)
    _extract_cover_table_date_fallback(blocks, collector)
    _extract_history_facts(blocks, collector)
    _extract_filename_facts(document.source_file, collector)
    _extract_risk_fallback(blocks, collector)
    recommendation_count = _select_recommendation_count(collector)
    if recommendation_count is None:
        recommendation_count = _recommendation_count_from_summary(collector)

    summary_values = {
        field: _selected_or_missing(field, collector.values[field])
        for field in _SUMMARY_FIELDS
    }
    # Long-span reports can publish separate main-bridge and approach-bridge
    # assessments instead of one facility-wide value.  The candidates already
    # retain that scope; keep both observed facts rather than silently reducing
    # the public field to the first (usually main-bridge) value.
    for field in (
        "overall_score",
        "overall_grade",
        "previous_overall_score",
        "previous_overall_grade",
    ):
        scoped_value = _select_scoped_overall_value(field, collector.values[field])
        if scoped_value:
            summary_values[field] = scoped_value
    # Some long-span bridge reports use a class system and split the final
    # assessment into scoped tables.  If the selected ``X类`` grade and an
    # explicit Dr score occur on the same assessment row, pair those two
    # observed facts instead of leaving the score empty.  This is provenance
    # matching, not grade-to-score inference.
    if summary_values["overall_score"] == "无" and re.fullmatch(
        r"[一二三四五六]类", summary_values["overall_grade"] or ""
    ):
        paired_score = _paired_class_system_score(
            collector.values["overall_score"],
            collector.values["overall_grade"],
            summary_values["overall_grade"],
        )
        if paired_score:
            summary_values["overall_score"] = paired_score
    report_grade_values = {grade_field: summary_values[grade_field] for _, grade_field in GRADE_SCORE_PAIRS}
    # Platform A/B experiment: report mode preserves the report's explicit
    # grades; generic mode changes only the four grade fields by mapping their
    # already-extracted scores.  Scores are never inferred from grades.
    summary_values = apply_grade_mode(summary_values, mode=resolved_grade_mode)
    # Current and previous scores are separated by source section, not by value.
    # Two inspections may legitimately have the same numeric score; clearing an
    # equal historical value would convert an observed fact into a false missing
    # value.  Leakage prevention therefore belongs to the candidate-routing
    # boundary above and the dedicated history parser below.
    # The official Gold contract uses “无” for the trend of first/no-history
    # reports.  Score fields already use the same display value when no
    # applicable score candidate exists; keep the trend consistent instead of
    # leaking an empty string into the renderer.
    if (
        not summary_values["trend"]
        and summary_values["previous_overall_score"] == "无"
        and summary_values["previous_overall_grade"] == "无"
    ):
        summary_values["trend"] = "无"
    summary = BridgeSummary(**summary_values)
    facility_name = summary.bridge_name
    facility_type_raw, facility_type, facility_noun = infer_facility_semantics(facility_name)
    inspection_date = _select_value("inspection_date", collector.values["inspection_date"])
    facility_context = FacilityContext(
        facility_name=facility_name,
        facility_type_raw=facility_type_raw,
        facility_type=facility_type,
        facility_noun=facility_noun,
        report_date=summary.report_date,
        inspection_date=inspection_date,
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
    field_states = build_field_states(
        {
            **{
                field: getattr(summary, field)
                for field in _SUMMARY_FIELDS
            },
            **facility_context.to_dict(),
        },
        collector.values,
        _CONTEXT_STATE_FIELDS,
    )
    if facility_type in {"pedestrian_underpass", "vehicle_underpass", "underpass"}:
        for field in _SCORE_FIELDS:
            if getattr(summary, field) == "无" and not collector.values[field]:
                field_states[field] = "not_applicable"
    if resolved_grade_mode == "generic":
        for score_field, grade_field in GRADE_SCORE_PAIRS:
            # V13 preserves standalone report grades when their score is
            # missing and preserves all ``X类`` grades.  Field state therefore
            # follows the final visible grade, not the old V12 assumption that
            # every missing score forces the grade to explicit-none.
            if getattr(summary, grade_field) not in {"", "无", "暂无", "不适用"}:
                field_states[grade_field] = "present"
            elif report_grade_values.get(grade_field) in {"", "无", "暂无", "不适用"}:
                field_states[grade_field] = "explicit_none"
    if summary.trend == "无":
        field_states["trend"] = "explicit_none"
    return SummaryExtraction(
        summary=summary,
        candidates=candidates,
        sources=sources,
        quality_flags=tuple(quality_flags),
        recommendation_count=recommendation_count,
        facility_context=facility_context,
        field_states=field_states,
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
        for field in ("bridge_name", "bridge_id", "report_date", "inspection_date")
        for alias in _ALIASES[field]
    }
    return len(keys & metadata) >= 1 and (
        len(keys & metadata) >= 2
        or any(marker in _compact(table.raw_text) for marker in ("字段内容", "项目值"))
    )


def _score_scope(table: TableBlock, blocks: Sequence[object]) -> str | None:
    """Return a nearby main/approach-bridge scope for a score table."""

    start = max(0, table.block_index - 3)
    for block in reversed(blocks[start:table.block_index]):
        if not isinstance(block, ParagraphBlock):
            continue
        compact = _compact(block.raw_text)
        has_main = "主桥" in compact
        has_approach = "引桥" in compact
        if has_main != has_approach:
            return "主桥" if has_main else "引桥"
    return None


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
    *,
    scope: str | None = None,
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
                    else (
                        "facility_name"
                        if key_field == "bridge_name"
                        else source_kind
                    )
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

    _extract_bsi_score_grade_pairs_from_table(table, source_kind, collector)
    _extract_header_columns(table, source_kind, collector)
    if source_kind in {"overall_assessment_table", "section_score_table"}:
        _extract_score_matrix(table, source_kind, collector, scope=scope)

    _extract_final_assessment_row(table, source_kind, collector, scope=scope)
    _extract_component_dr_rows(table, source_kind, collector)

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
    *,
    scope: str | None = None,
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

        # Some official overall-assessment tables have visually merged headers
        # whose OOXML cell order makes the weight column look like the score
        # column.  Resolve only the unambiguous numeric shape: three component
        # weights in [0, 1] summing to 1 and one separate three-value BCI column.
        if source_kind == "overall_assessment_table":
            component_values: dict[int, list[tuple[str, float]]] = defaultdict(list)
            known_value_columns = set(score_columns) | set(grade_columns)
            for data_row in rows[header_index + 1 :]:
                category_cell, base = _score_category_cell(data_row, known_value_columns)
                if category_cell is None or base not in {"deck", "superstructure", "substructure"}:
                    continue
                for data_cell in data_row.cells:
                    if data_cell is category_cell or data_cell.column_index in grade_columns:
                        continue
                    number = _numeric_value(data_cell.raw_text)
                    if number is not None:
                        component_values[data_cell.column_index].append((base, number))
            dense_columns = {
                column: values
                for column, values in component_values.items()
                if {base for base, _ in values} == {"deck", "superstructure", "substructure"}
            }
            weight_columns = [
                column
                for column, values in dense_columns.items()
                if all(0 <= value <= 1 for _, value in values)
                and abs(sum(value for _, value in values) - 1.0) <= 0.02
            ]
            component_score_columns = [
                column
                for column, values in dense_columns.items()
                if all(1 < value <= 100 for _, value in values)
            ]
            if len(weight_columns) == 1 and len(component_score_columns) == 1:
                score_columns.pop(weight_columns[0], None)
                score_columns[component_score_columns[0]] = "score"
        if not score_columns and not grade_columns:
            continue
        value_columns = set(score_columns) | set(grade_columns)
        for row in rows[header_index + 1 :]:
            category_cell, base = _score_category_cell(row, value_columns)
            if category_cell is None or base is None:
                continue
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
                        label=_score_label(category_cell.raw_text, scope),
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
                        label=_score_label(category_cell.raw_text, scope),
                    )
        return


def _extract_final_assessment_row(
    table: TableBlock,
    source_kind: str,
    collector: _CandidateCollector,
    *,
    scope: str | None = None,
) -> None:
    """Read legacy ``综合评定分数Dr`` rows that lack a score header column."""

    for row in table.rows:
        cells = list(row.cells)
        if not cells or "综合评定分数" not in _compact(cells[0].raw_text):
            continue
        first_compact = _compact(cells[0].raw_text)
        # A labelled component Dr row belongs to that component, not the whole
        # facility.  Component extraction is handled separately below.
        if any(marker in first_compact for marker in ("上部结构", "下部结构", "桥面系")):
            continue
        row_text = "\t".join(cell.raw_text for cell in cells)
        # Legacy long-span bridge reports often place the explicit Dr score
        # inside the row label itself: ``综合评定分数 Dr=70.4``.  V12 already
        # extracted the adjacent ``二类`` grade but missed this numeric fact.
        # Keep the rule narrow to the final-assessment row and preserve scope.
        score_match = re.search(
            r"综合评定分数\s*D[rR]\s*(?:为|是|[:：=])?\s*(\d+(?:\.\d+)?)\s*分?",
            _clean(cells[0].raw_text),
            flags=re.IGNORECASE,
        )
        if score_match is None:
            score_match = re.search(r"[（(]\s*(\d+(?:\.\d+)?)\s*[）)]", row_text)
        if score_match is not None:
            _add_field(
                collector,
                "overall_score",
                score_match.group(1),
                source_kind,
                cells[0].source or table.source,
                label=_score_label(cells[0].raw_text, scope),
            )
        grade_match = _GRADE_RE.search("\t".join(cell.raw_text for cell in cells[1:]))
        if grade_match is not None:
            _add_field(
                collector,
                "overall_grade",
                grade_match.group(0),
                source_kind,
                cells[-1].source or table.source,
                label=_score_label("等级", scope),
            )


def _same_table_row(left: SourceAnchor | None, right: SourceAnchor | None) -> bool:
    if left is None or right is None:
        return False
    return (
        left.source_file == right.source_file
        and left.table_index is not None
        and left.table_index == right.table_index
        and left.row_index is not None
        and left.row_index == right.row_index
    )


def _paired_class_system_score(
    score_candidates: Sequence[SummaryCandidate],
    grade_candidates: Sequence[SummaryCandidate],
    selected_grade: str,
) -> str:
    """Pair a scoped Dr score with the selected explicit ``X类`` grade row."""

    grades = [
        candidate
        for candidate in grade_candidates
        if _normalise_field_value("overall_grade", candidate.value) == selected_grade
    ]
    grades.sort(
        key=lambda candidate: (
            -_selection_priority("overall_grade", candidate),
            -candidate.priority,
            _source_sort_key(candidate.source),
        )
    )
    for grade in grades:
        paired = [
            candidate
            for candidate in score_candidates
            if "综合评定分数" in _compact(candidate.label)
            and _same_table_row(candidate.source, grade.source)
        ]
        if paired:
            paired.sort(key=lambda candidate: (-candidate.priority, _source_sort_key(candidate.source)))
            return _normalise_field_value("overall_score", paired[0].value)
    return ""


def _score_label(label: str, scope: str | None = None) -> str:
    cleaned = _clean(label)
    return f"{scope}{cleaned}" if scope and scope not in cleaned else cleaned


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


def _score_category_cell(
    row: object,
    value_columns: set[int],
) -> tuple[object | None, str | None]:
    """Find the labelled component cell without relying on row position.

    Real assessment tables often prepend a serial/group column, so the first
    physical cell is not necessarily “上部结构/下部结构/桥面系/总体”.  Only
    non-value columns are considered and explicit component labels outrank
    generic overall wording.
    """

    cells = tuple(getattr(row, "cells", ()))
    ranked: list[tuple[int, int, object, str]] = []
    for order, cell in enumerate(cells):
        column = getattr(cell, "column_index", order)
        if column in value_columns:
            continue
        text = _clean(getattr(cell, "raw_text", ""))
        base = _field_base_for_category(text)
        if base is None:
            continue
        compact = _compact(text)
        exact = {
            "上部结构": 40,
            "下部结构": 40,
            "桥面系": 40,
            "总体": 35,
            "整体": 35,
            "总评": 35,
            "全桥": 35,
        }.get(compact, 0)
        component = 30 if base in {"superstructure", "substructure", "deck"} else 20
        ranked.append((exact + component, -order, cell, base))
    if not ranked:
        return None, None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, cell, base = ranked[0]
    return cell, base


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
    if compact in {"主桥", "引桥", "主桥总体", "引桥总体"}:
        return "overall"
    if "总体" in compact or "整体" in compact or compact in {"总评", "全桥"}:
        return "overall"
    return None


def _field_for_key(value: str) -> str | None:
    normalised = _normalised_key(value)
    field = _ALIAS_TO_FIELD.get(normalised)
    if field is not None:
        return field
    label = _clean(value)
    for alias, alias_field in _SORTED_ALIASES:
        if re.fullmatch(
            _alias_pattern(alias) + r"\s*(?:[（(][^）)]*[）)])?",
            label,
            flags=re.IGNORECASE,
        ):
            return alias_field
    return None


def _extract_paragraph(
    block: ParagraphBlock,
    source_kind: str,
    collector: _CandidateCollector,
) -> None:
    if not _clean(block.raw_text):
        return
    _extract_embedded_fields(block.raw_text, source_kind, block.source, collector)
    if source_kind == _ROUTE_CONCLUSION:
        _extract_plain_bridge_name(
            block.raw_text,
            "conclusion_name",
            block.source,
            collector,
        )
    elif source_kind != "cover":
        _extract_plain_bridge_name(
            block.raw_text,
            "body_name",
            block.source,
            collector,
        )
    _extract_bsi_score_grade_pairs(block.raw_text, block.source, collector)
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
            _alias_pattern(alias)
            + r"\s*(?:[（(][^）)]*[）)])?\s*"
            + r"(?:(?:[:：=])\s*|(?:为|是)\s*|(?=[\d.无暂无不适用A-Ea-e优良中差]|$))"
        )
        for match in pattern.finditer(text):
            if any(start <= match.start() < end or match.start() <= start < match.end() for start, end, _, _ in matches):
                continue
            matches.append((match.start(), match.end(), field, alias))
    matches.sort(key=lambda item: item[0])
    for index, (start, end, field, alias) in enumerate(matches):
        value_end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        value = text[end:value_end].strip(" \t:：=，,；;。．")
        if field == "bridge_name":
            # Recover the source spelling from the original paragraph.  The
            # normalized scan above may expand Unicode Roman numerals (Ⅲ→III).
            raw_match = re.search(
                _alias_pattern(alias)
                + r"\s*(?:[（(][^）)]*[）)])?\s*(?:(?:[:：=])\s*|(?:为|是)\s*)(.+)$",
                raw_text,
                flags=re.IGNORECASE,
            )
            if raw_match is not None:
                raw_value = raw_match.group(1).strip(" \t:：=，,；;。．")
                # Stop before another labelled field when several values share
                # one compact paragraph.
                raw_value = re.split(
                    r"\s+(?=(?:桥梁编号|报告日期|总体技术状况|总体评分|总体等级)\s*[:：=])",
                    raw_value,
                    maxsplit=1,
                )[0].strip()
                if raw_value:
                    value = raw_value
        if not value and field not in {"bridge_id", "previous_overall_score", "previous_overall_grade"}:
            continue
        date_kind = _date_kind_for_alias(alias) if field in {"report_date", "inspection_date"} else None
        candidate_kind = _date_source_kind(source_kind, date_kind)
        if field == "bridge_name":
            candidate_kind = (
                "project_name"
                if _compact(alias) == "项目名称"
                else "cover_facility_name"
                if source_kind == "cover"
                else "facility_name"
            )
        elif field == "overall_conclusion":
            candidate_kind = _conclusion_source_kind(alias, candidate_kind)
        elif field == "risk_points":
            candidate_kind = _risk_source_kind(alias, candidate_kind)
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


def _bsi_base(token: str) -> str | None:
    match = _BSI_TOKEN_RE.search(token or "")
    if match is None:
        return None
    return _BSI_COMPONENT.get(match.group(1).lower())


def _add_bsi_pair(
    collector: _CandidateCollector,
    token: str,
    score: str,
    grade: str,
    source: SourceAnchor | None,
) -> None:
    base = _bsi_base(token)
    if base is None:
        return
    suffix = token[-1].lower()
    label = f"BSI{suffix}评分等级配对"
    _add_field(collector, f"{base}_score", score, "paired_score_grade", source, label=label)
    _add_field(collector, f"{base}_grade", grade, "paired_score_grade", source, label=label)


def _extract_bsi_score_grade_pairs(
    raw_text: str,
    source: SourceAnchor | None,
    collector: _CandidateCollector,
) -> None:
    """Extract explicit BSI score-grade pairs as one report fact.

    Paired facts such as ``BSIs=81.36（B级）`` or the official triplet
    ``BSIm、BSIs、BSIx分别为60.00（D级）、81.36（B级）、91.90（A级）``
    outrank standalone intermediate calculations.  This function never infers
    a score from a grade or a report-mode grade from a score.
    """

    text = _clean(raw_text)
    if not text or "bsi" not in text.casefold():
        return

    for match in _BSI_DIRECT_PAIR_RE.finditer(text):
        _add_bsi_pair(collector, f"BSI{match.group(1)}", match.group(2), match.group(3), source)

    for marker_match in re.finditer(r"分别为", text):
        prefix = text[max(0, marker_match.start() - 96):marker_match.start()]
        tokens = [f"BSI{item.group(1)}" for item in _BSI_TOKEN_RE.finditer(prefix)]
        if len(tokens) < 2:
            continue
        tokens = tokens[-3:]
        tail = text[marker_match.end():marker_match.end() + 192]
        pairs = list(_BSI_PAIRED_VALUE_RE.finditer(tail))
        if len(pairs) < len(tokens):
            continue
        for token, pair in zip(tokens, pairs):
            _add_bsi_pair(collector, token, pair.group(1), pair.group(2), source)


def _row_source_anchor(table: TableBlock, row, raw_text: str) -> SourceAnchor | None:
    source = next((cell.source for cell in row.cells if cell.source is not None), table.source)
    if source is None:
        return None
    return SourceAnchor(
        source_file=source.source_file,
        block_index=source.block_index,
        raw_text=raw_text,
        table_index=source.table_index if source.table_index is not None else table.table_index,
        row_index=getattr(row, "row_index", source.row_index),
        column_index=None,
        paragraph_index=None,
    )


def _extract_bsi_score_grade_pairs_from_table(
    table: TableBlock,
    source_kind: str,
    collector: _CandidateCollector,
) -> None:
    del source_kind
    for row in table.rows:
        row_text = " ".join(_clean(cell.raw_text) for cell in row.cells if _clean(cell.raw_text))
        if not row_text:
            continue
        _extract_bsi_score_grade_pairs(row_text, _row_source_anchor(table, row, row_text), collector)


def _extract_component_dr_rows(
    table: TableBlock,
    source_kind: str,
    collector: _CandidateCollector,
) -> None:
    """Extract an explicit component Dr score only when label and number share a row."""

    component_markers = (
        ("superstructure", "上部结构"),
        ("substructure", "下部结构"),
        ("deck", "桥面系"),
    )
    for row in table.rows:
        row_text = " ".join(_clean(cell.raw_text) for cell in row.cells if _clean(cell.raw_text))
        compact = _compact(row_text)
        if not compact or "dr" not in compact.casefold():
            continue
        bases = [base for base, component in component_markers if component in compact]
        if len(bases) != 1:
            continue
        match = re.search(
            r"(?i)(?:综合评定分数|评定分数|技术状况(?:评定)?分数|技术状况评分)?\s*"
            r"D[rR]\s*(?:为|是|[:：=])\s*(\d+(?:\.\d+)?)\s*分?",
            row_text,
        )
        if match is None:
            continue
        source = _row_source_anchor(table, row, row_text)
        _add_field(
            collector,
            f"{bases[0]}_score",
            match.group(1),
            "component_dr",
            source,
            label=f"{bases[0]}显式Dr评定分数",
        )
        grade_match = _GRADE_RE.search(row_text[match.end():])
        if grade_match is not None:
            _add_field(
                collector,
                f"{bases[0]}_grade",
                grade_match.group(0),
                "component_dr",
                source,
                label=f"{bases[0]}显式Dr评定等级",
            )


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
    for scope in ("主桥", "引桥"):
        scoped_score = re.search(
            rf"{scope}\s*(?:总体|整体)?\s*技术状况.*?"
            rf"评定为\s*{_GRADE_RE.pattern}\s*[（(]\s*"
            rf"(\d+(?:\.\d+)?)\s*[）)]",
            text,
        )
        if scoped_score is not None:
            _add_field(
                collector,
                "overall_score",
                scoped_score.group(1),
                source_kind,
                source,
                label=f"{scope}总体技术状况评分",
            )
        scoped_grade = re.search(
            rf"{scope}\s*(?:总体|整体)?\s*技术状况.*?"
            rf"评定为\s*({_GRADE_RE.pattern})",
            text,
        )
        if scoped_grade is not None:
            _add_field(
                collector,
                "overall_grade",
                scoped_grade.group(1),
                source_kind,
                source,
                label=f"{scope}总体技术状况等级",
            )

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
        scoped_overall = (
            base == "overall"
            and score_match is not None
            and _score_scope_before(text, score_match.start(1)) is not None
        )
        if score_match and not scoped_overall:
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
            if grade_match and not scoped_overall:
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
        scoped_grade = (
            base == "overall"
            and _score_scope_before(text, grade_match.start()) is not None
            if grade_match is not None
            else False
        )
        if grade_match and not scoped_grade:
            _add_field(
                collector,
                f"{base}_grade",
                grade_match.group(1),
                source_kind,
                source,
                label=base,
            )

        # Many official reports state component grades as
        # “上部结构结构状况评定为B级” or “评定等级为C级” rather than
        # using a literal “上部结构等级” key.  These are explicit facts, not
        # score-to-grade derivations, so capture them directly.
        if base != "overall":
            explicit_grade = re.search(
                rf"{label_pattern}(?:结构)?(?:技术)?状况?\s*"
                rf"(?:等级|级别)?\s*评定(?:等级|级别)?\s*"
                rf"(?:为|是|[:：=])?\s*({_GRADE_RE.pattern})",
                text,
            )
            if explicit_grade is None:
                explicit_grade = re.search(
                    rf"{label_pattern}.*?评定(?:等级|级别)?\s*"
                    rf"(?:为|是|[:：=])?\s*({_GRADE_RE.pattern})",
                    text,
                )
            if explicit_grade is not None:
                _add_field(
                    collector,
                    f"{base}_grade",
                    explicit_grade.group(1),
                    source_kind,
                    source,
                    label=f"{base}显式评定等级",
                )

    # Current comprehensive summaries often use “BCI评分为95.39分” instead
    # of “BCI=95.39”.  Preserve the explicit score without inferring it from
    # a grade or from the filename.
    overall_bci_phrase = re.search(
        r"(?<![A-Za-z])BCI\s*(?:指数)?\s*(?:评分|得分|分数)\s*"
        r"(?:为|是|[:：=])?\s*(\d+(?:\.\d+)?)\s*分?",
        text,
        flags=re.IGNORECASE,
    )
    if overall_bci_phrase is not None:
        _add_field(
            collector,
            "overall_score",
            overall_bci_phrase.group(1),
            source_kind,
            source,
            label="BCI评分",
        )

    # Other explicit current-summary forms used by class-system/legacy
    # reports.  Do not accept a bare ``95.39分``: a score marker is required.
    explicit_overall_score = re.search(
        r"(?:"
        r"综合(?:评定)?(?:评分|分数|得分)|"
        r"总体(?:技术状况)?(?:评分|分数|得分)|"
        r"整体(?:技术状况)?(?:评分|分数|得分)|"
        r"(?<!上部结构)(?<!下部结构)(?<!桥面系)技术状况评分|"
        r"(?:本桥|该桥|本设施|该设施|桥梁)\s*评分"
        r")\s*(?:为|是|[:：=])?\s*(\d+(?:\.\d+)?)\s*分?",
        text,
    )
    if explicit_overall_score is not None:
        _add_field(
            collector,
            "overall_score",
            explicit_overall_score.group(1),
            source_kind,
            source,
            label="显式总体评分",
        )

    class_overall_grade = _CLASS_OVERALL_GRADE_RE.search(text)
    if class_overall_grade is not None:
        _add_field(
            collector,
            "overall_grade",
            class_overall_grade.group(1),
            source_kind,
            source,
            label="技术状况等级",
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

    for match in _UNDERPASS_CONCLUSION_GRADE_RE.finditer(text):
        _add_field(
            collector,
            "overall_grade",
            match.group(1),
            "underpass_conclusion",
            source,
            label="技术标准",
        )


def _score_scope_before(text: str, start: int) -> str | None:
    prefix = text[max(0, start - 12) : start].rstrip()
    matches = list(re.finditer(r"(主桥|引桥)\s*(?:总体|整体)?$", prefix))
    return matches[-1].group(1) if matches else None


def _conclusion_source_kind(label: str, fallback: str) -> str:
    compact = _compact(label)
    if any(marker in compact for marker in ("主要结论", "总体结论", "综合结论", "评估结论")):
        return "major_conclusion"
    if compact == "检测结果":
        return "conclusion_review"
    if "外观及专项检测结果综述" in compact:
        return "conclusion_review"
    if "综合评估" in compact:
        return "comprehensive_assessment"
    return fallback


def _risk_source_kind(label: str, fallback: str) -> str:
    compact = _compact(label)
    if any(marker in compact for marker in ("主要风险", "主要病害", "突出病害", "突出风险")):
        return "major_risk"
    if any(marker in compact for marker in ("风险点", "安全风险", "安全隐患")):
        return "risk_label"
    return fallback


def _conclusion_label(text: str) -> str:
    compact = _compact(text)
    for alias, field in _SORTED_ALIASES:
        if field == "overall_conclusion" and _compact(alias) in compact:
            return alias
    return ""


def _looks_like_conclusion_fragment(value: str) -> bool:
    compact = _compact(value)
    return any(marker in compact for marker in _CONCLUSION_FRAGMENT_MARKERS)


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
            if body:
                heading = getattr(route, "heading", None)
                label = _conclusion_label(getattr(heading, "raw_text", ""))
                _add_field(
                    collector,
                    "overall_conclusion",
                    "\n".join(_clean(block.raw_text) for block in body),
                    _conclusion_source_kind(label, "conclusion"),
                    body[0].source,
                    label=label or "检测结论",
                )


def _risk_fragments(value: str) -> tuple[str, ...]:
    fragments: list[str] = []
    for part in re.split(r"[\n\r\t]+|(?<=[。；;！？!?])", value or ""):
        cleaned = _clean(part).strip("，,；;。．")
        cleaned = re.sub(r"^(?:主要结论|安全影响|风险点|处置建议|处理建议)\s*[:：]\s*", "", cleaned)
        if not cleaned or len(cleaned) > 360:
            continue
        if not any(marker in cleaned for marker in _RISK_DEFECT_MARKERS):
            continue
        if any(marker in cleaned for marker in _SUMMARY_ACTION_MARKERS):
            continue
        if not any(marker in cleaned for marker in _RISK_CONSEQUENCE_MARKERS):
            continue
        if cleaned not in fragments:
            fragments.append(cleaned)
    return tuple(fragments)


def _extract_risk_fallback(
    blocks: Sequence[object],
    collector: _CandidateCollector,
) -> None:
    if any(candidate.source_kind == "major_risk" for candidate in collector.values["risk_points"]):
        return
    added = 0
    for block in blocks:
        if not isinstance(block, (ParagraphBlock, TableBlock)):
            continue
        for fragment in _risk_fragments(block.raw_text):
            collector.add(
                "risk_points",
                fragment,
                "risk_fallback",
                block.source,
                label="病害处置建议",
            )
            added += 1
            if added >= 3:
                return


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
        compact = _compact(text)
        if any(_compact(alias) in compact for alias in (*_ALIASES["report_date"], *_ALIASES["inspection_date"])):
            continue
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
        for match in _DATE_RE.finditer(text):
            collector.add(
                "report_date",
                _date_value(match.group(0)),
                "cover",
                block.source,
                label="封面日期",
                date_kind="cover",
            )



def _extract_cover_table_date_fallback(
    blocks: Sequence[object],
    collector: _CandidateCollector,
) -> None:
    """Use the end of a labelled cover-table inspection range as report date.

    Several pedestrian-overpass reports are flattened into one table and have
    no separate cover paragraph.  Their Gold report date is the end date in the
    labelled ``检验日期/检测日期`` row.  This remains lower priority than an
    explicit report/sign date and is only added for a labelled range.
    """

    labels = ("检验日期", "检测日期", "检查日期", "samplingdate")
    for block in blocks[:4]:
        if not isinstance(block, TableBlock):
            continue
        for row in block.rows[:16]:
            cells = list(row.cells)
            for index, cell in enumerate(cells[:-1]):
                label = _compact(cell.raw_text).casefold()
                if not any(marker in label for marker in labels):
                    continue
                value_cell = cells[index + 1]
                value = _clean(value_cell.raw_text)
                if not _is_date_range(value):
                    continue
                _add_field(
                    collector,
                    "report_date",
                    value,
                    "cover",
                    value_cell.source or cell.source or block.source,
                    label=cell.raw_text,
                    date_kind="cover_range_end",
                )
                return

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
    if field in {"report_date", "inspection_date"}:
        date_kind = date_kind or _date_kind_for_alias(label)
        if field == "report_date" and date_kind not in _REPORT_DATE_KINDS:
            return
        if field == "inspection_date" and date_kind not in _INSPECTION_DATE_KINDS:
            return
        if date_kind not in {"cover", "sign", "cover_range_end", "detection_end"} and _is_date_range(value):
            date_kind = "range"
        original = _clean(value)
        value = _date_value(original)
        if not value and original:
            return
    if field == "overall_conclusion":
        source_kind = _conclusion_source_kind(label, source_kind)
        if source_kind not in _HIGH_CONCLUSION_KINDS and _looks_like_conclusion_fragment(value):
            return
        value = _normalise_overall_conclusion(value)
        if not value:
            return
    if field == "risk_points":
        source_kind = _risk_source_kind(label, source_kind)
        value = _normalise_risk_points(value)
        if not value:
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
    if any(marker in compact for marker in ("检测结束", "检测完成", "检测终止")):
        return "detection_end"
    if any(marker in compact for marker in ("检测", "检验", "检查")):
        return "detection"
    if any(marker in compact for marker in ("签发", "签字", "出具", "发出")):
        return "sign"
    return "cover"


def _date_source_kind(source_kind: str, date_kind: str | None) -> str:
    if date_kind in _REPORT_DATE_KINDS | _INSPECTION_DATE_KINDS and source_kind in {
        "cover",
        "paragraph",
        "conclusion",
        "safety_assessment",
    }:
        return date_kind
    return source_kind


def _format_date(year: str, month: str, day: str | None = None) -> str | None:
    try:
        year_number = int(year)
        month_number = int(month)
        day_number = int(day) if day is not None else None
    except (TypeError, ValueError):
        return None
    if not 1 <= month_number <= 12:
        return None
    if day_number is not None and not 1 <= day_number <= 31:
        return None
    result = f"{year_number}年{month_number}月"
    if day_number is not None:
        result += f"{day_number}日"
    return result


def _date_value(value: str) -> str:
    cleaned = _clean(value).strip("：:=，,；;。．")
    for range_pattern in (_DATE_RANGE_RE, _CN_DATE_RANGE_RE):
        range_match = range_pattern.search(cleaned)
        if range_match is None:
            continue
        return (
            _format_date(
                range_match.group("year2") or range_match.group("year1"),
                range_match.group("month2"),
                range_match.group("day2"),
            )
            or cleaned
        )
    chinese_match = _CN_DATE_RE.search(cleaned)
    match = _DATE_RE.search(cleaned)
    if chinese_match is not None and (
        match is None or chinese_match.start() < match.start()
    ):
        chinese = _extract_cn_date(cleaned[chinese_match.start() :])
        if chinese is not None:
            return chinese
    if match is None:
        return cleaned
    return (
        _format_date(
            match.group("year"),
            match.group("month_cn") or match.group("month"),
            match.group("day_cn") or match.group("day"),
        )
        or cleaned
    )


def _is_date_range(value: str) -> bool:
    cleaned = _clean(value)
    return _DATE_RANGE_RE.search(cleaned) is not None or _CN_DATE_RANGE_RE.search(cleaned) is not None


def _is_generic_bridge_name(value: str) -> bool:
    compact = _compact(value).strip("：:=，,；;。． ")
    return not compact or any(pattern.fullmatch(compact) for pattern in _GENERIC_BRIDGE_NAME_RE)


def _is_specific_facility_name(value: str) -> bool:
    compact = _compact(value)
    return (
        len(compact) >= 3
        and compact not in _GENERIC_FACILITY_NAMES
        and any(marker in compact for marker in ("桥", "立交", "通道", "隧道", "涵洞", "道路", "下穿道"))
        and not _is_generic_bridge_name(compact)
    )


def _normalise_bridge_name(value: str) -> str:
    # NFKC converts Unicode Roman numerals (Ⅲ) into ASCII letters (III), which
    # is useful for matching but harmful for the public identity field.  Keep
    # the source spelling here and perform alias normalization only internally.
    cleaned = unicodedata.normalize("NFC", value or "").replace("\u00a0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip("：:=，,；;。． ")
    cleaned = re.split(
        r"(?:所在路名|在路名|路名|桥梁编号|桥梁ID|等级)\s*[:：=]",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    cleaned = re.sub(
        r"^(?:桥梁名称|桥名|工程名称|设施名称|桥梁工程名称|工程设施名称|工程/设施名称|工程（设施）名称|项目名称|桥式通道名称|人行天桥名称|地通道名称|人行通道名称|人行地通道名称|地下通道名称|车行下穿道名称|车行地通道名称|车行通道名称|下穿道名称|隧道名称|涵洞名称|道路名称|通道名称)\s*[:：=]\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?:检测评估项目|检测评估|评估报告|检测报告|定期检测报告|检测项目|外观检查|报告)\s*$",
        "",
        cleaned,
    )
    # Cover tables sometimes concatenate a valid facility name with the full
    # project scope.  Keep the facility prefix and discard the metadata tail.
    cleaned = re.split(
        r"\s*(?:检测项目|检测类别|委托检测|外观检查|专项检测|结构验算|荷载试验)\s*[:：]?",
        cleaned,
        maxsplit=1,
    )[0].strip()
    match = re.search(
        r"(.{2,80}?(?:人行地通道|人行地道|地下通道|人行通道|车行下穿道|下穿道|人行天桥|匝道桥|立交桥|大桥|中桥|小桥|天桥|隧道|涵洞|道路|桥|通道|立交))(?=检测评估|检测项目|检测类别|委托检测|$)",
        cleaned,
    )
    if match is not None:
        cleaned = match.group(1)
    cleaned = re.sub(r"互通式(?=立交)", "", cleaned)
    cleaned = re.sub(r"桥异形梁桥$", "异形桥", cleaned)
    cleaned = re.sub(r"(?<!立交)(\d+)#(?=人行天桥)", r"\1号", cleaned)
    # Keep the authoritative source spelling.  Roman numeral conversion is an
    # aliasing concern; rewriting it in the public field caused strict-name
    # mismatches (Ⅲ vs III) without adding factual value.
    cleaned = _strip_project_road_prefix(cleaned)
    cleaned = cleaned.strip("：:=，,；;。． ")
    return "" if _is_generic_bridge_name(cleaned) else cleaned


def _bridge_name_from_text(raw_text: str) -> str:
    text = _clean(raw_text)
    if not text:
        return ""
    for segment in re.split(r"[\n\t，,。；;：:、（）()]+", text):
        compact = _compact(segment)
        if not compact:
            continue
        compact = re.split(
            r"(?:整体|总体|技术状况|评定为|检测结果|需进行|属于|进行了|位于)",
            compact,
            maxsplit=1,
        )[0]
        for marker in ("对", "于"):
            if marker in compact:
                compact = compact.rsplit(marker, 1)[-1]
        matches = list(_BRIDGE_NAME_RE.finditer(compact))
        for match in reversed(matches):
            candidate = _normalise_bridge_name(match.group("name"))
            if _is_specific_facility_name(candidate):
                return candidate
    return ""


def _extract_plain_bridge_name(
    raw_text: str,
    source_kind: str,
    source: SourceAnchor | None,
    collector: _CandidateCollector,
) -> None:
    name = _bridge_name_from_text(raw_text)
    if name:
        collector.add(
            "bridge_name",
            name,
            source_kind,
            source,
            label="设施名",
        )


def _extract_cover_names(
    blocks: Sequence[object],
    first_heading: int,
    collector: _CandidateCollector,
) -> None:
    for block in blocks:
        if not isinstance(block, ParagraphBlock) or block.block_index >= first_heading:
            continue
        if len(_compact(block.raw_text)) > 80:
            continue
        _extract_plain_bridge_name(block.raw_text, "cover_name", block.source, collector)


_FILENAME_GRADE_RE = re.compile(
    r"(?P<label>原|现)\s*(?P<grade>[A-Ea-e]|[一二三四五六])\s*(?P<suffix>级|类)?"
)
_FILENAME_FACILITY_SUFFIXES = (
    "人行地通道", "地下通道", "人行通道", "车行下穿道", "下穿道",
    "人行天桥", "桥式通道", "匝道桥", "立交桥", "跨线桥",
    "分离式立交桥", "大桥", "中桥", "小桥", "旱桥", "天桥",
    "隧道", "涵洞", "桥", "通道",
)


def _filename_grade(value: str, suffix: str = "") -> str:
    grade = (value or "").upper()
    if grade in {"A", "B", "C", "D", "E"}:
        return f"{grade}级"
    if grade in "一二三四五六":
        return f"{grade}类"
    return grade + suffix


def _filename_identity(stem: str) -> str:
    """Return the facility-name portion of an official input filename.

    The operation is deliberately narrow: remove the maintenance-unit prefix,
    grade annotations and report suffix, while preserving chainage that is part
    of the name before ``报告``. Roman numerals keep the exact filename form.
    """

    value = (stem or "").strip()
    value = re.sub(r"^[^-—_]{1,12}[-—_]", "", value, count=1)
    # Grade annotations are metadata, not part of the facility name.
    value = re.sub(
        r"[（(][^（）()]*(?:原|现)\s*[A-Ea-e一二三四五六][^（）()]*[）)]",
        "",
        value,
    )
    # Anything after the first report suffix is document metadata.
    value = re.split(r"(?:定期)?(?:检测|检查|评估)?报告", value, maxsplit=1)[0]
    value = re.sub(r"\s+", "", value).strip("-—_（）()，,；;")
    value = re.sub(r"k(?=\d)", "K", value, flags=re.I)
    if not value:
        return ""
    # A filename ending in 匝道/互通X号 often omits the final facility noun;
    # do not force it over a more explicit cover-field name.
    if not value.endswith(_FILENAME_FACILITY_SUFFIXES):
        return ""
    return value


def _extract_filename_facts(
    source_file: str,
    collector: _CandidateCollector,
) -> None:
    """Use facts explicitly encoded by the official input filename.

    No score is inferred from a grade. Only a clearly marked ``原/现`` grade
    and a clear facility-name stem are added.
    """

    if not source_file:
        return
    raw_name = re.split(r"[\\/]", source_file)[-1]
    stem = re.sub(r"\.(?:docx?|DOCX?)$", "", raw_name)
    anchor = SourceAnchor(source_file, -1, source_file)

    identity = _filename_identity(stem)
    if identity and _is_specific_facility_name(_normalise_bridge_name(identity)):
        collector.add(
            "bridge_name", identity, "filename_facility", anchor, label="文件名设施名称"
        )
    else:
        # Preserve the historical conservative fallback for filenames whose
        # main stem does not expose a complete facility noun.
        parts = re.split(r"[-_—（）()]+", stem)
        for part in reversed(parts):
            part = re.sub(r"^\d+", "", part)
            name = _normalise_bridge_name(part)
            if _is_specific_facility_name(name):
                collector.add(
                    "bridge_name", name, "filename", anchor, label="文件名"
                )
                break
        else:
            name = _bridge_name_from_text(stem)
            if name:
                collector.add(
                    "bridge_name", name, "filename", anchor, label="文件名"
                )

    previous_grade = ""
    current_grade = ""
    for match in _FILENAME_GRADE_RE.finditer(stem):
        value = _filename_grade(match.group("grade"), match.group("suffix") or "")
        if match.group("label") == "原":
            previous_grade = value
        else:
            current_grade = value
    if previous_grade:
        collector.add(
            "previous_overall_grade", previous_grade, "filename_history", anchor,
            label="文件名原等级",
        )
    if current_grade:
        collector.add(
            "overall_grade", current_grade, "filename_grade", anchor,
            label="文件名现等级",
        )
    # Do not write a trend sentence from filename metadata.  A pair of grade
    # labels is not evidence of the report's disease-development narrative.



_PREVIOUS_SECTION_HEADING_RE = re.compile(
    r"(?:^|\s)(?:1\s*[.．、]?\s*2\s*)?(?:上一次|上次|上一年度|上年度)(?:定期)?(?:检测|检查)(?:状况|情况|结果|概况)?"
)
# Stop the 1.2 history window at any real chapter-2 heading.  The previous
# implementation only stopped at “2 检测目的/检查目的”; reports whose chapter 2
# is named “桥梁概况/工程概况/检测依据” could otherwise suppress the entire
# current-period body.  Exclude 2.1/2.2 subheadings explicitly.
_NEXT_MAIN_SECTION_RE = re.compile(
    r"^\s*(?:第\s*二\s*章|2(?:\.0)?)(?!\s*[.．]\s*\d)(?:\s*[、.．:：)）-]?\s*)[^0-9]"
)
_PREVIOUS_BCI_RE = re.compile(
    r"(?:整体|总体)?(?:技术状况指数\s*)?BCI(?!\s*[mMkKsSxX])\s*(?:为|是|[:：=＝])?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PREVIOUS_GRADE_TEXT_RE = re.compile(
    r"(?:总体|整体)(?:技术状况)?(?:等级|级别)(?:评定|评价|确定)?\s*(?:为|是|[:：=＝])?\s*([A-Ea-e]\s*级|[一二三四五六]类)"
)
_PREVIOUS_GRADE_FALLBACK_RE = re.compile(
    r"(?:总体|整体)技术状况[^。；;]{0,100}?(?:等级|级别)[^。；;]{0,30}?([A-Ea-e]\s*级|[一二三四五六]类)"
)
_HISTORY_HEADERS = {
    "location": ("位置", "部位", "结构部位"),
    "previous": ("上一次检测结果", "上次检测结果", "上一次定检结果", "历史检测结果"),
    "current": ("本次检测结果", "本次定检结果", "当前检测结果"),
    "development": ("发展状况", "发展情况", "变化情况", "病害发展"),
}

def _history_header_mapping(table: TableBlock) -> tuple[int, dict[str, int]] | None:
    """Resolve one- or multi-level history headers by logical column index."""

    mapping: dict[str, int] = {}
    for row in table.rows[:4]:
        for cell in row.cells:
            value = _compact(cell.raw_text)
            for field, aliases in _HISTORY_HEADERS.items():
                if field in mapping:
                    continue
                if any(alias in value for alias in aliases):
                    mapping[field] = cell.column_index
                    break
        if {"previous", "current", "development"}.issubset(mapping):
            return row.row_index, mapping
    return None

def _history_cell_record(row: object, column_index: int | None) -> object | None:
    if column_index is None or column_index < 0:
        return None
    for cell in getattr(row, "cells", ()):
        start = getattr(cell, "column_index", -1)
        span = max(1, int(getattr(cell, "column_span", 1) or 1))
        if start <= column_index < start + span:
            return cell
    return None

def _history_cell(row: object, column_index: int | None) -> str:
    cell = _history_cell_record(row, column_index)
    return _clean(getattr(cell, "raw_text", "")) if cell is not None else ""

def _extract_history_comparison_table(
    table: TableBlock,
    collector: _CandidateCollector,
) -> None:
    header = _history_header_mapping(table)
    if header is None:
        return
    header_row, mapping = header
    trend_parts: list[str] = []
    explicit_nonempty = False
    inherited_location = ""
    inherited_development = ""
    for row in table.rows[header_row + 1 :]:
        location_cell = _history_cell_record(row, mapping.get("location"))
        development_cell = _history_cell_record(row, mapping.get("development"))
        location = _history_cell(row, mapping.get("location"))
        development = _history_cell(row, mapping.get("development"))
        if location:
            inherited_location = location
        elif location_cell is not None and getattr(location_cell, "is_merge_continuation", False):
            location = inherited_location
        elif not location and inherited_location:
            # Blank location cells in chapter-7 comparison tables conventionally
            # continue the preceding component group.  This does not copy any
            # disease fact, only the grouping label.
            location = inherited_location
        if development:
            inherited_development = development
        elif development_cell is not None and getattr(development_cell, "is_merge_continuation", False):
            development = inherited_development
        if not development:
            continue
        compact = _compact(development)
        if not compact or compact in {"/", "-", "—", "无变化"}:
            continue
        if compact not in {"无", "暂无", "不适用"}:
            explicit_nonempty = True
        if location:
            trend_parts.append(f"{location}：{development}")
        else:
            trend_parts.append(development)
    if not trend_parts:
        return
    # Keep the source wording.  The official summary field is a compact
    # cross-period statement, not an inferred severity label.
    unique_parts = list(dict.fromkeys(trend_parts))
    trend = "；".join(unique_parts[:8])
    if not explicit_nonempty and all(_compact(part).endswith("：无") or _compact(part) == "无" for part in unique_parts):
        trend = "无"
    collector.add(
        "trend",
        trend,
        "history_comparison",
        table.source,
        label="历次检测结果对比",
    )


def _previous_section_block_indexes(blocks: Sequence[object]) -> set[int]:
    start_position: int | None = None
    for position, block in enumerate(blocks):
        if isinstance(block, ParagraphBlock) and _PREVIOUS_SECTION_HEADING_RE.search(_clean(block.raw_text)):
            start_position = position
            break
    if start_position is None:
        return set()
    result: set[int] = set()
    for block in blocks[start_position:]:
        if (
            len(result) > 0
            and isinstance(block, ParagraphBlock)
            and _NEXT_MAIN_SECTION_RE.search(_clean(block.raw_text))
        ):
            break
        block_index = getattr(block, "block_index", None)
        if isinstance(block_index, int):
            result.add(block_index)
    return result


def _extract_history_facts(
    blocks: Sequence[object],
    collector: _CandidateCollector,
) -> None:
    """Extract explicit previous-period facts and comparison text.

    Reports in the platform set commonly store the last BCI/grade under
    ``1.2 上一次检测状况`` and development under a four-column comparison
    table in chapter 7.  These are authoritative report facts and must not be
    guessed from filenames or by the LLM.
    """

    for block in blocks:
        if isinstance(block, TableBlock):
            _extract_history_comparison_table(block, collector)

    previous_indexes = _previous_section_block_indexes(blocks)
    if not previous_indexes:
        return

    scoped_history_block = _extract_scoped_previous_assessment(
        blocks,
        collector,
    )

    for block in blocks:
        if getattr(block, "block_index", None) not in previous_indexes:
            continue
        if isinstance(block, ParagraphBlock):
            text = _clean(block.raw_text)
            source = block.source
        elif isinstance(block, TableBlock):
            if block.block_index == scoped_history_block:
                # The selected row was parsed cell-by-cell above.  Parsing the
                # concatenated whole table again could mix current and earlier
                # years into one unscoped historical value.
                continue
            text = _clean(block.raw_text)
            source = block.source
        else:
            continue
        if not text or _PREVIOUS_SECTION_HEADING_RE.fullmatch(text):
            continue
        if re.search(
            r"(?:无|没有|未有)(?:上一次|上次|往年|历史)(?:定期)?(?:检测|检查)(?:记录|资料|结果|数据)?|"
            r"(?:首次|第一次)(?:定期)?(?:检测|检查)",
            text,
        ):
            collector.add(
                "previous_overall_score", "无", "previous_detection", source,
                label="上一次检测明确无记录",
            )
            collector.add(
                "previous_overall_grade", "无", "previous_detection", source,
                label="上一次检测明确无记录",
            )
            continue
        score = _PREVIOUS_BCI_RE.search(text)
        if score:
            collector.add(
                "previous_overall_score",
                score.group(1),
                "previous_detection",
                source,
                label="上一次检测BCI",
            )
        grade = _PREVIOUS_GRADE_TEXT_RE.search(text) or _PREVIOUS_GRADE_FALLBACK_RE.search(text)
        if grade:
            collector.add(
                "previous_overall_grade",
                grade.group(1),
                "previous_detection",
                source,
                label="上一次检测总体等级",
            )


def _extract_scoped_previous_assessment(
    blocks: Sequence[object],
    collector: _CandidateCollector,
) -> int | None:
    """Keep the latest explicit pre-report main/approach assessment row.

    Long-span reports summarize prior scores in a later chronological record
    table rather than inside the short chapter-1.2 prose window.  A row is
    eligible only when it has a year plus both explicit facility scopes.
    """

    current_year = _current_report_year(collector)
    if current_year is None:
        return None

    records: list[
        tuple[int, int, int, TableBlock, object, dict[str, tuple[str, str]]]
    ] = []
    for block in blocks:
        if not isinstance(block, TableBlock):
            continue
        for row in block.rows:
            row_text = _clean("；".join(cell.raw_text for cell in row.cells))
            year_match = re.search(r"(?:19|20)\d{2}", row_text)
            if year_match is None:
                continue
            year = int(year_match.group(0))
            if year >= current_year:
                continue
            scoped = _scoped_assessment_values(row_text)
            if not all(scope in scoped for scope in ("主桥", "引桥")):
                continue
            records.append(
                (year, block.block_index, row.row_index, block, row, scoped)
            )

    if not records:
        return None

    _, _, _, table_block, row, scoped = max(
        records,
        key=lambda item: (item[0], item[1], item[2]),
    )
    cells = list(getattr(row, "cells", ()))
    source = next(
        (cell.source for cell in reversed(cells) if cell.source is not None),
        table_block.source,
    )
    for scope in ("主桥", "引桥"):
        score, grade = scoped[scope]
        if score:
            collector.add(
                "previous_overall_score",
                score,
                "previous_detection",
                source,
                label=f"{scope}上一次总体评分",
            )
        if grade:
            collector.add(
                "previous_overall_grade",
                grade,
                "previous_detection",
                source,
                label=f"{scope}上一次总体等级",
            )
    return table_block.block_index


def _current_report_year(collector: _CandidateCollector) -> int | None:
    for field in ("report_date", "inspection_date"):
        selected = _select_value(field, collector.values[field])
        match = re.search(r"(?:19|20)\d{2}", selected)
        if match is not None:
            return int(match.group(0))
    return None


def _scoped_assessment_values(text: str) -> dict[str, tuple[str, str]]:
    """Parse explicit main/approach score-grade facts from one table row."""

    matches = list(re.finditer(r"主桥|引桥", text))
    result: dict[str, tuple[str, str]] = {}
    for index, match in enumerate(matches):
        scope = match.group(0)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.start():end]
        score_match = re.search(
            r"(?:评定)?(?:评分|分数|得分)\s*(?:D[rR]\s*)?"
            r"(?:为|是|[:：=＝])?\s*(\d+(?:\.\d+)?)",
            segment,
            flags=re.IGNORECASE,
        )
        grade_match = re.search(
            rf"(?:技术)?等级(?:评定|评价|确定)?\s*(?:为|是|[:：=＝])?\s*({_GRADE_RE.pattern})",
            segment,
        )
        if grade_match is None:
            grade_match = re.search(
                rf"评定为\s*({_GRADE_RE.pattern})",
                segment,
            )
        if score_match is None:
            parenthesized = re.search(
                rf"评定为\s*{_GRADE_RE.pattern}\s*[（(]\s*(\d+(?:\.\d+)?)\s*[）)]",
                segment,
            )
            score = parenthesized.group(1) if parenthesized is not None else ""
        else:
            score = score_match.group(1)
        grade = grade_match.group(1) if grade_match is not None else ""
        if score or grade:
            result[scope] = (score, grade)
    return result


def _normalise_field_value(field: str, value: str) -> str:
    if field == "bridge_name":
        return _normalise_bridge_name(value)
    cleaned = _clean(value).strip("：:=，,；;。．")
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
    if field in {"report_date", "inspection_date"}:
        return _date_value(cleaned)
    if field == "recommendation_count":
        match = re.search(r"\d+", cleaned)
        return match.group(0) if match else cleaned
    return cleaned


def _bridge_name_quality(value: str) -> tuple[int, int, int]:
    compact = _compact(value)
    terminal_penalty = 1 if compact.endswith("匝道") and not compact.endswith("匝道桥") else 0
    penalty = sum(1 for marker in (
        "检测项目", "检测类别", "委托检测", "外观检查",
        "专项检测", "结构验算", "荷载试验",
    ) if marker in compact)
    return terminal_penalty, penalty, len(compact)


def _conclusion_quality(value: str) -> tuple[int, int, int, int]:
    compact = _compact(value)
    defect_hits = sum(1 for marker in _RISK_DEFECT_MARKERS if marker in compact)
    component_hits = sum(1 for marker in (
        "桥面", "上部结构", "下部结构", "主梁", "梁体", "桥台",
        "桥墩", "支座", "栏杆", "护栏", "顶板", "侧墙", "翼墙",
    ) if marker in compact)
    fragment_penalty = (
        1
        if _looks_like_conclusion_fragment(value)
        and defect_hits == 0
        and component_hits <= 1
        else 0
    )
    heading_hits = sum(compact.count(marker) for marker in (
        "基本资料", "结构建模", "计算说明", "验算结果", "静载试验",
        "动载试验", "目录", "页码",
    ))
    numeric_headings = len(re.findall(r"(?:^|\s)\d+(?:\.\d+)+", value))
    toc_penalty = (
        1
        if (heading_hits >= 2 or numeric_headings >= 5) and defect_hits <= 1
        else 0
    )
    # Lower tuple is better: reject contents-like fragments and single-test
    # snippets, then prefer richer defect/component coverage.
    return toc_penalty, fragment_penalty, -(defect_hits + component_hits), len(compact)


def _date_candidate_priority(candidate: SummaryCandidate) -> int:
    """Prefer explicitly labelled report/sign dates over incidental cover text."""

    base = _DATE_PRIORITY.get(candidate.date_kind or "", 0)
    label = _compact(candidate.label)
    if any(marker in label for marker in ("报告日期", "报告出具", "出具日期", "签发日期", "签字日期")):
        base += 300
    elif any(marker in label for marker in ("检测日期", "检验日期", "检查日期")):
        base += 80
    elif candidate.label in {"封面日期", "封面中文日期"}:
        base += 20
    return base


_COMPONENT_SCORE_FIELDS = {"deck_score", "superstructure_score", "substructure_score"}
_COMPONENT_GRADE_FIELDS = {"deck_grade", "superstructure_grade", "substructure_grade"}
_BSI_EVIDENCE_SOURCE_KINDS = {"paired_score_grade"}


def _production_candidates(
    field: str, values: Sequence[SummaryCandidate]
) -> tuple[SummaryCandidate, ...]:
    """Return candidates eligible to populate the public Prediction field.

    V14 parsed BSI score/grade pairs correctly as report evidence but then
    treated those BSI structural-condition indices as the component BCI
    technical-condition fields.  V15 keeps BSI candidates in the trace/audit
    while excluding them from production component score/grade selection.
    """

    if field in _COMPONENT_SCORE_FIELDS | _COMPONENT_GRADE_FIELDS:
        return tuple(
            candidate
            for candidate in values
            if candidate.source_kind not in _BSI_EVIDENCE_SOURCE_KINDS
        )
    return tuple(values)


def _select_scoped_overall_value(
    field: str,
    values: Sequence[SummaryCandidate],
) -> str:
    """Return ``主桥…；引桥…`` only when both are the sole fact scopes."""

    nonempty = [
        candidate
        for candidate in _production_candidates(field, values)
        if candidate.value.strip()
    ]
    if not nonempty or any(
        not _is_scoped_overall_candidate(candidate) for candidate in nonempty
    ):
        return ""

    selected: list[str] = []
    for scope in ("主桥", "引桥"):
        scoped = [
            candidate
            for candidate in nonempty
            if _candidate_scope(candidate) == scope
        ]
        if not scoped:
            return ""
        scoped.sort(
            key=lambda candidate: (
                -_selection_priority(field, candidate),
                -candidate.priority,
                _source_sort_key(candidate.source),
                candidate.source_kind,
                candidate.value,
            )
        )
        selected.append(f"{scope}{scoped[0].value}")
    return "；".join(selected)


def _select_value(field: str, values: Sequence[SummaryCandidate]) -> str:
    values = _production_candidates(field, values)
    if not values:
        return ""
    ordered = sorted(
        values,
        key=lambda candidate: (
            0
            if field == "bridge_name" and _is_specific_facility_name(candidate.value)
            else 1
            if field == "bridge_name"
            else 0,
            # Explicit facility-name fields must beat incidental body mentions;
            # quality only breaks ties at the same source priority.
            -candidate.priority if field == "bridge_name" else 0,
            *(_bridge_name_quality(candidate.value) if field == "bridge_name" else (0, 0, 0)),
            *(_conclusion_quality(candidate.value) if field == "overall_conclusion" else (0, 0, 0, 0)),
            -(_DATE_PRIORITY.get(candidate.date_kind or "", 0) if field in {"report_date", "inspection_date"} else _selection_priority(field, candidate)),
            -candidate.priority,
            _source_sort_key(candidate.source),
            candidate.source_kind,
            candidate.value,
        ),
    )
    nonempty = [candidate for candidate in ordered if candidate.value.strip()]
    if field == "overall_score":
        unified = [
            candidate
            for candidate in nonempty
            if not _is_scoped_overall_score(candidate)
        ]
        if nonempty and not unified:
            return ""
        nonempty = unified
        if not nonempty:
            return ""
    selected_candidate = nonempty[0] if nonempty else ordered[0]
    selected = selected_candidate.value
    if field == "bridge_name" and selected_candidate.source_kind == "project_name":
        selected = _strip_project_road_prefix(selected)
    if field == "bridge_name" and selected_candidate.source_kind == "paragraph":
        project_candidates = [
            candidate
            for candidate in nonempty
            if candidate.source_kind == "project_name"
        ]
        if project_candidates:
            project = project_candidates[0].value
            if "匝道" in project and "匝道" not in selected:
                selected = _strip_project_road_prefix(project)
    if field.endswith("_score"):
        numeric = _numeric_value(selected)
        if numeric is not None:
            equivalent = [
                candidate
                for candidate in (nonempty or ordered)
                if _numeric_value(candidate.value) == numeric
            ]
            # When the final assessment table and the explicit BCI sentence
            # agree numerically, preserve the report's BCI spelling/precision
            # to avoid cosmetic v10->v11 changes such as 86.10 -> 86.1.
            bci_forms = [
                candidate.value
                for candidate in equivalent
                if candidate.source_kind == "bci" and "." in candidate.value
            ]
            decimal_forms = [
                candidate.value
                for candidate in equivalent
                if "." in candidate.value
            ]
            if bci_forms:
                selected = bci_forms[0]
            elif decimal_forms:
                selected = decimal_forms[0]
    return selected


def _is_scoped_overall_score(candidate: SummaryCandidate) -> bool:
    return _is_scoped_overall_candidate(candidate)


def _is_scoped_overall_candidate(candidate: SummaryCandidate) -> bool:
    return _candidate_scope(candidate) is not None


def _candidate_scope(candidate: SummaryCandidate) -> str | None:
    labelled = _scope_from_label(candidate.label)
    if labelled is not None:
        return labelled
    raw_text = _compact(candidate.source.raw_text if candidate.source is not None else "")
    has_main = "主桥" in raw_text
    has_approach = "引桥" in raw_text
    if has_main != has_approach:
        return "主桥" if has_main else "引桥"
    return None


def _scope_from_label(label: str) -> str | None:
    compact = _compact(label)
    for scope in ("主桥", "引桥"):
        if scope in compact:
            return scope
    return None


def _strip_project_road_prefix(value: str) -> str:
    return re.sub(r"^.*?路段(?=[\u3400-\u9fffA-Za-z0-9])", "", value)


def _numeric_value(value: str) -> float | None:
    match = _SCORE_RE.search(_clean(value))
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _selected_or_missing(field: str, values: Sequence[SummaryCandidate]) -> str:
    value = _select_value(field, values)
    if not value.strip() and field in _SCORE_FIELDS:
        return "无"
    return value


def _selection_priority(field: str, candidate: SummaryCandidate) -> int:
    if field not in _SCORE_FIELDS:
        return candidate.priority
    # V15 restores the V11 production semantics: component Prediction scores
    # are BCI technical-condition scores.  The final assessment table wins,
    # followed by explicit BCIm/BCIs/BCIx facts.  Component Dr remains an
    # explicit fallback for class-system reports, while paired BSI facts are
    # audit evidence only and are filtered by _production_candidates().
    if (
        field in _COMPONENT_SCORE_FIELDS
        and candidate.source_kind == "overall_assessment_table"
    ):
        return 650
    if field in _COMPONENT_SCORE_FIELDS and candidate.source_kind == "bci":
        return 500
    if field in _COMPONENT_SCORE_FIELDS and candidate.source_kind == "component_dr":
        return 450
    return {
        "paired_score_grade": 0,
        "component_dr": 450,
        "bci": 500,
        "overall_assessment_table": 400,
        "section_score_table": 300,
        "section_score": 280,
        "summary_page": 200,
        "underpass_conclusion": 520,
        "filename_grade": 60,
        "filename_history": 55,
        "previous_detection": 680,
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
