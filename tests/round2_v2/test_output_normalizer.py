from src.contracts import BridgeSummary, DefectObservation, InspectionPrediction, Recommendation
from src.extraction.output_normalizer import (
    normalize_defect_description,
    normalize_prediction_output,
    normalize_recommendations_summary,
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
    ) == "2条尽快维修、1条预防性养护建议"


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
    assert result.summary.recommendations_summary.endswith("建议")
    assert result.recommendations[0].content == "修补裂缝"
    assert result.defects[0].defect_type == "裂缝,渗水"
    assert result.defects[0].description == "裂缝宽0.2mm"
