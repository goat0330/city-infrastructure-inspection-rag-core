from src.contracts import BridgeSummary, DefectObservation, InspectionPrediction, Recommendation
from src.extraction.output_normalizer import (
    normalize_defect_description,
    normalize_overall_conclusion,
    normalize_prediction_output,
    normalize_recommendations_summary,
    normalize_risk_points,
    normalize_safety_impacts,
)


def test_bridge_photo_tail_is_removed_but_measurements_remain():
    value = "第1跨梁底裂缝，长1.0m，宽0.20mm，见照片5.1.2-3"
    assert normalize_defect_description(value, preserve_figure_refs=False) == "第1跨梁底裂缝，长1.0m，宽0.20mm"


def test_passage_figure_reference_is_preserved():
    value = "右洞口处，顶板车辆刮痕，见图2.1.1"
    assert normalize_defect_description(value, preserve_figure_refs=True) == value


def test_recommendation_summary_keeps_source_display_style():
    recommendations = (
        Recommendation(category="尽快维修"),
        Recommendation(category="尽快维修"),
        Recommendation(category="预防性养护"),
    )
    assert normalize_recommendations_summary(
        recommendations,
        source_summary="2条尽快维修、1条预防性养护建议",
    ) == "0条立即处置、2条尽快维修、1条预防性养护"


def test_prediction_normalization_changes_only_display_noise():
    prediction = InspectionPrediction(
        sample_id="2012年-丁家院大桥",
        summary=BridgeSummary(report_date="2012年06月02日"),
        recommendations=(Recommendation(index="1", category="尽快维修", content="1、修补裂缝", location="主梁"),),
        defects=(DefectObservation(index="1", location="主梁", defect_type="裂缝,渗水", description="裂缝宽0.2mm，见照片1.1-1"),),
    )
    result = normalize_prediction_output(
        prediction,
        facility_context={"facility_type": "bridge"},
        source_recommendations_summary="0条立即处置、1条尽快维修、0条预防性养护建议",
    )
    assert result.summary.report_date == "2012年6月2日"
    assert result.summary.recommendations_summary == "0条立即处置、1条尽快维修、0条预防性养护"
    assert result.recommendations[0].content == "修补裂缝"
    assert result.defects[0].defect_type == "裂缝,渗水"
    assert result.defects[0].description == "裂缝宽0.2mm"


def test_overall_conclusion_is_short_and_excludes_actions_and_raw_data():
    value = (
        "经综合评定，该桥总体技术状况等级为B级。"
        "主梁存在竖向裂缝，支座存在锈蚀。"
        "混凝土强度52.7MPa、56.1MPa、59.2MPa。"
        "建议及时修复裂缝并更换支座。"
    )
    result = normalize_overall_conclusion(value)
    assert "总体技术状况等级为B级" in result
    assert "主梁存在竖向裂缝" in result
    assert "混凝土强度" not in result
    assert "建议" not in result
    assert len(result) <= 250


def test_risk_points_require_defect_and_consequence_without_advice():
    value = (
        "于2014年进行了外观检查。"
        "建议及时修复梁底裂缝。"
        "梁底裂缝持续发展可能削弱构件耐久性。"
    )
    assert normalize_risk_points(value) == "梁底裂缝持续发展可能削弱构件耐久性"


def test_final_summary_limits_include_join_separator():
    value = "总体技术状况" + "良" * 176 + "。" + "主梁裂缝" + "安" * 70
    result = normalize_overall_conclusion(value)
    assert len(result) <= 250


def test_treatment_sentence_is_not_a_risk_point():
    value = "裂缝不影响结构安全，可直接用环氧砂浆封闭"
    assert normalize_risk_points(value) == ""


def test_safety_normalizer_removes_meta_text_and_same_topic_conflict():
    result = normalize_safety_impacts(
        (
            "上部结构：已有证据为裂缝1条；报告未明确该类病害影响。",
            "局部裂缝影响结构承载能力。",
            "综合评定认为现有裂缝暂不影响结构承载能力。",
        )
    )
    assert result == ("综合评定认为现有裂缝暂不影响结构承载能力。",)
