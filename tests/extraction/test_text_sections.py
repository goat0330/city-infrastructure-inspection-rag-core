from __future__ import annotations

from src.contracts import (
    BridgeSummary,
    DefectObservation,
    DocumentModel,
    ParagraphBlock,
    Recommendation,
    SourceAnchor,
)
from src.extraction.text_sections import extract_text_sections
from src.routing import SectionCategory, SectionRoute


def _paragraph(index: int, text: str, *, heading_level: int | None = None) -> ParagraphBlock:
    return ParagraphBlock(
        index,
        text,
        SourceAnchor("fixture.docx", index, text, paragraph_index=index),
        heading_level=heading_level,
        style_id="Heading1" if heading_level else None,
    )


def _facts() -> tuple[DefectObservation, ...]:
    return (
        DefectObservation("1", "桥面铺装", "裂缝", "桥面铺装出现裂缝"),
        DefectObservation("2", "主梁", "露筋锈蚀", "主梁露筋锈蚀"),
        DefectObservation("3", "桥墩", "渗水泛碱", "桥墩渗水泛碱"),
        DefectObservation("4", "栏杆", "破损", "栏杆局部破损"),
    )


def test_structured_facts_do_not_generate_component_count_paragraphs() -> None:
    summary = BridgeSummary(
        bridge_name="单样本桥",
        overall_score="86.5",
        overall_grade="B级",
        previous_overall_score="84.0",
        previous_overall_grade="B级",
        trend="病害总体稳定",
        risk_points="现有病害对行车安全影响较小，但需及时处置。",
    )
    recommendations = (
        Recommendation("1", "尽快维修", "封闭裂缝", "桥面铺装"),
        Recommendation("2", "立即处置", "清理露筋并修补", "主梁"),
    )

    result = extract_text_sections(
        DocumentModel("单样本桥.docx", ()),
        (),
        recommendations,
        summary,
        _facts(),
    )

    assert result.detailed_conclusion[0] == (
        "经综合评定，该桥总体技术状况评分 86.5 分，总体技术状况等级为 B级。"
    )
    assert any("上一周期总体评分为84.0分" in item for item in result.detailed_conclusion)
    assert any("病害总体稳定" in item for item in result.detailed_conclusion)
    assert any("处置重点为" in item for item in result.detailed_conclusion)
    assert not any("未提取到结构化病害记录" in item for item in result.detailed_conclusion)
    # Defect labels are not causal evidence.  With no explicit report cause,
    # the conservative production path leaves the field empty.
    assert result.causes == ()
    assert result.treatments == ("封闭裂缝", "清理露筋并修补")
    assert result.safety_impact == ()


def test_explicit_report_causes_and_safety_are_preserved_without_templates() -> None:
    heading = _paragraph(0, "6 安全性评估", heading_level=1)
    cause = _paragraph(1, "梁体裂缝主要由于温度收缩所致。")
    safety = _paragraph(2, "综合评定认为现有裂缝暂不影响结构承载能力。")
    document = DocumentModel("fixture.docx", (heading, cause, safety))
    route = SectionRoute(
        category=SectionCategory.SAFETY_ASSESSMENT,
        heading=heading,
        blocks=document.blocks,
        source=heading.source,
    )

    result = extract_text_sections(
        document,
        (route,),
        (),
        BridgeSummary(),
        _facts(),
    )

    assert result.causes == ("梁体裂缝主要由于温度收缩所致。",)
    assert result.safety_impact == ("综合评定认为现有裂缝暂不影响结构承载能力。",)


def test_missing_unified_score_does_not_generate_missing_score_sentence() -> None:
    result = extract_text_sections(
        DocumentModel("无评分桥.docx", ()),
        (),
        (),
        BridgeSummary(),
        _facts()[:3],
    )

    assert result.detailed_conclusion == ()


def test_detailed_conclusion_uses_only_clean_conclusion_evidence() -> None:
    heading = _paragraph(0, "5 检测结论", heading_level=1)
    disease = _paragraph(
        1,
        "经检查，桥面铺装存在裂缝、坑槽，主梁腹板存在竖向裂缝，现阶段对承载能力影响较小。",
    )
    caption = _paragraph(2, "图5.1.2-1 主梁病害分布示意图")
    calculation = _paragraph(3, "混凝土强度52.7MPa~59.2MPa，自振频率3.901Hz。")
    action = _paragraph(4, "建议及时封闭裂缝并清理排水孔。")
    stop = _paragraph(5, "5.2 病害成因分析", heading_level=2)
    cause = _paragraph(6, "裂缝主要由于温度收缩导致。")
    document = DocumentModel(
        "fixture.docx",
        (heading, disease, caption, calculation, action, stop, cause),
    )
    route = SectionRoute(
        category=SectionCategory.INSPECTION_CONCLUSION,
        heading=heading,
        blocks=document.blocks,
        source=heading.source,
    )

    result = extract_text_sections(
        document,
        (route,),
        (),
        BridgeSummary(overall_score="88", overall_grade="B级"),
        _facts(),
    )

    assert len(result.detailed_conclusion) == 3
    text = "\n".join(result.detailed_conclusion)
    assert "桥面铺装存在裂缝、坑槽" in text
    assert "主梁腹板存在竖向裂缝" in text
    assert "图5.1.2-1" not in text
    assert "混凝土强度" not in text
    assert "自振频率" not in text
    assert "建议及时" not in text
    assert "温度收缩" not in text
    assert "按结构部位归纳病害" not in text
    assert "记录4条" not in text


def test_official_summary_style_uses_fixed_opening_and_component_order() -> None:
    from src.extraction.text_sections import apply_summary_style
    from src.extraction.summary.facility_context import FacilityContext

    summary = BridgeSummary(overall_conclusion="原报告概述")
    defects = (
        DefectObservation("1", "桥面铺装", "裂缝", "桥面铺装存在裂缝"),
        DefectObservation("2", "桥墩", "渗水泛碱", "桥墩渗水泛碱"),
        DefectObservation("3", "主梁", "露筋", "主梁局部露筋"),
    )
    result = apply_summary_style(
        summary,
        defects,
        facility_context=FacilityContext(facility_name="测试桥", facility_type="bridge", facility_noun="桥梁"),
        style="official",
    )

    assert result.overall_conclusion.startswith("本次定检结果表明，桥梁")
    assert result.overall_conclusion.index("上部结构") < result.overall_conclusion.index("下部结构")
    assert result.overall_conclusion.index("下部结构") < result.overall_conclusion.index("桥面系")
    assert "露筋" in result.overall_conclusion
    assert "渗水泛碱" in result.overall_conclusion
    assert "裂缝" in result.overall_conclusion


def test_official_summary_style_cleans_mechanical_trend_prefixes() -> None:
    from src.extraction.text_sections import apply_summary_style

    summary = BridgeSummary(
        trend="上部结构:新增病害:局部渗水泛碱,部分主梁破损露筋。;下部结构:无;桥面系:新增病害:铺装裂缝"
    )
    result = apply_summary_style(summary, (), style="official")

    assert result.trend.startswith("与上一次定检相比，")
    assert "新增病害:" not in result.trend
    assert "下部结构无" not in result.trend
    assert ":" not in result.trend
    assert "上部结构新增局部渗水泛碱、部分主梁破损露筋" in result.trend
    assert "桥面系新增铺装裂缝" in result.trend


def test_legacy_summary_style_is_noop_and_preserves_risk_points() -> None:
    from src.extraction.text_sections import apply_summary_style

    summary = BridgeSummary(
        overall_conclusion="报告原句",
        trend="上部结构：新增裂缝",
        risk_points="裂缝进一步发展可能影响耐久性",
    )
    result = apply_summary_style(summary, _facts(), style="legacy")
    assert result == summary
