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

    assert result.detailed_conclusion == (
        "经综合评定，该桥总体技术状况评分 86.5 分，总体技术状况等级为 B级。",
    )
    assert not any("记录" in item for item in result.detailed_conclusion)
    assert not any("未提取到结构化病害记录" in item for item in result.detailed_conclusion)
    assert len(result.causes) >= 3
    assert any("裂缝" in cause for cause in result.causes)
    assert any("露筋锈蚀" in cause for cause in result.causes)
    assert any("渗水泛碱" in cause for cause in result.causes)
    assert result.treatments == ("封闭裂缝", "清理露筋并修补")
    assert 3 <= len(result.safety_impact) <= 4


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

    assert len(result.detailed_conclusion) == 2
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
