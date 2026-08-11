from src.contracts import BridgeSummary, DefectObservation, InspectionPrediction, Recommendation
from src.extraction.output_normalizer import (
    normalize_defect_description,
    normalize_overall_conclusion,
    normalize_public_overall_conclusion,
    normalize_public_summary_output,
    normalize_prediction_output,
    normalize_public_risk_points,
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


def test_v16_overall_conclusion_drops_trailing_maintenance_action_only():
    value = (
        "全桥的技术状况指数BCI为94.8,技术状况评定为A级,为完好状态,"
        "应进行日常保养"
    )
    assert normalize_public_overall_conclusion(value) == (
        "全桥的技术状况指数BCI为94.8，技术状况评定为A级，为完好状态"
    )


def test_v16_risk_rejects_cause_sentence_with_environment_impact_word():
    value = "护栏锈蚀是由于环境影响，或者防锈漆脱落所致"
    assert normalize_public_risk_points(value) == ""


def test_v16_public_risk_preserves_report_backed_defect_fallback_when_no_better_risk_exists():
    value = "桥面铺装局部磨损、破损、坑槽，桥面行车道线局部磨损，护栏局部破损露筋"
    assert normalize_public_risk_points(value) == value


def test_v16_risk_keeps_specific_defect_to_consequence_sentence():
    value = "梁底裂缝持续发展可能削弱构件耐久性"
    assert normalize_public_risk_points(value) == value


def test_v16_trend_cleans_machine_punctuation_without_adding_facts():
    from src.extraction.output_normalizer import normalize_trend

    value = (
        "上部结构:新增病害:局部渗水泛碱,部分主梁破损露筋。;"
        "下部结构:无;"
        "桥面系:新增病害:新增护栏破损露筋,部分病害已修复"
    )
    assert normalize_trend(value) == (
        "上部结构：新增局部渗水泛碱、部分主梁破损露筋；"
        "下部结构：无；"
        "桥面系：新增护栏破损露筋、部分病害已修复"
    )


def test_v16_prediction_hygiene_does_not_change_structured_or_detail_fields():
    prediction = InspectionPrediction(
        sample_id="v16-hygiene",
        summary=BridgeSummary(
            bridge_name="测试桥",
            report_date="2019年11月20日",
            overall_score="89.46",
            overall_grade="B级",
            superstructure_score="86.10",
            superstructure_grade="D级",
            substructure_score="93.68",
            substructure_grade="B级",
            deck_score="85.75",
            deck_grade="D级",
            previous_overall_score="85.38",
            previous_overall_grade="B级",
            trend="上部结构:新增病害:渗水,露筋;下部结构:无",
            overall_conclusion="总体技术状况为B级,应进行保养小修",
            risk_points="护栏锈蚀是由于环境影响，或者防锈漆脱落所致",
        ),
        detailed_conclusion=("段一。", "段二。", "段三。", "段四。"),
        recommendations=(Recommendation(index="1", category="尽快维修", content="修补裂缝", location="主梁"),),
        defects=(DefectObservation(index="1", location="主梁", defect_type="裂缝", description="裂缝宽0.2mm"),),
        causes=("裂缝由于温度变化导致。",),
        treatments=("修补裂缝。",),
        safety_impact=("裂缝长期发展会影响耐久性。",),
    )
    v15_normalized = normalize_prediction_output(prediction, facility_context={"facility_type": "bridge"})
    result = normalize_public_summary_output(v15_normalized)

    before = prediction.summary
    after = result.summary
    for field in (
        "bridge_name", "report_date", "overall_score", "overall_grade",
        "superstructure_score", "superstructure_grade",
        "substructure_score", "substructure_grade", "deck_score", "deck_grade",
        "previous_overall_score", "previous_overall_grade",
    ):
        assert getattr(after, field) == getattr(before, field)
    assert result.detailed_conclusion == prediction.detailed_conclusion
    assert result.defects == prediction.defects
    assert result.causes == prediction.causes
    assert result.treatments == prediction.treatments
    assert result.safety_impact == prediction.safety_impact


def test_v16_trend_turns_new_disease_none_into_no_new_disease():
    from src.extraction.output_normalizer import normalize_trend
    assert normalize_trend("上部结构:新增病害:无,部分病害已修复") == "上部结构：无新增病害、部分病害已修复"


def test_v16_public_risk_keeps_evidence_before_treatment_tail():
    value = "桥面裂缝均未超过最大允许裂缝宽度，不影响结构安全，可采用路面灌封胶进行处治"
    assert normalize_public_risk_points(value) == "桥面裂缝均未超过最大允许裂缝宽度，不影响结构安全"


def test_v16_summary_display_punctuation_is_full_width_without_rewriting_numbers():
    value = "桥面系破损较多,技术状况指数BCI=89.31,整体技术状况等级为B级"
    assert normalize_public_overall_conclusion(value) == "桥面系破损较多，技术状况指数BCI=89.31，整体技术状况等级为B级"


def test_v16_public_hygiene_is_separate_from_v15_pre_narrative_normalization():
    prediction = InspectionPrediction(
        summary=BridgeSummary(
            trend="上部结构:新增病害:裂缝",
            overall_conclusion="总体技术状况为B级,应进行保养小修",
            risk_points="护栏锈蚀是由于环境影响，或者防锈漆脱落所致",
        )
    )
    baseline = normalize_prediction_output(prediction)
    assert baseline.summary.trend == prediction.summary.trend
    assert "应进行保养小修" in baseline.summary.overall_conclusion
    assert "环境影响" in baseline.summary.risk_points

    public = normalize_public_summary_output(baseline)
    assert public.summary.trend == "上部结构：新增裂缝"
    assert "保养小修" not in public.summary.overall_conclusion
    assert "环境影响" not in public.summary.risk_points
