from __future__ import annotations

from src.contracts import (
    BridgeSummary,
    DefectObservation,
    DocumentModel,
    Recommendation,
)
from src.extraction.text_sections import extract_text_sections


def _facts() -> tuple[DefectObservation, ...]:
    return (
        DefectObservation("1", "桥面铺装", "裂缝", "桥面铺装出现裂缝"),
        DefectObservation("2", "主梁", "露筋锈蚀", "主梁露筋锈蚀"),
        DefectObservation("3", "桥墩", "渗水泛碱", "桥墩渗水泛碱"),
        DefectObservation("4", "栏杆", "破损", "栏杆局部破损"),
    )


def test_structured_facts_fill_text_sections_without_template() -> None:
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

    assert len(result.detailed_conclusion) == 4
    assert "86.5" in result.detailed_conclusion[0]
    assert "历史对比" in result.detailed_conclusion[1]
    assert all(label in result.detailed_conclusion[2] for label in ("桥面系", "上部结构", "下部结构", "附属设施"))
    assert len(result.causes) >= 3
    assert any("裂缝" in cause for cause in result.causes)
    assert any("露筋锈蚀" in cause for cause in result.causes)
    assert any("渗水泛碱" in cause for cause in result.causes)
    assert result.treatments == ("封闭裂缝", "清理露筋并修补")
    assert 3 <= len(result.safety_impact) <= 4
    assert all(
        any(label in paragraph for label in ("桥面系", "上部结构", "下部结构", "总体评估"))
        for paragraph in result.safety_impact
    )


def test_missing_unified_score_is_explicit() -> None:
    result = extract_text_sections(
        DocumentModel("无评分桥.docx", ()),
        (),
        (),
        BridgeSummary(),
        _facts()[:3],
    )

    assert len(result.detailed_conclusion) == 4
    assert "无统一全桥评分" in result.detailed_conclusion[0]
    assert "评分 无 分" not in result.detailed_conclusion[0]
