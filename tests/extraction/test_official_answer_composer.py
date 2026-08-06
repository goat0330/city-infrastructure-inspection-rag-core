from src.contracts.prediction import BridgeSummary, DefectObservation, Recommendation
from src.extraction.official_answer_composer import compose_official_answers
from src.extraction.summary.facility_context import FacilityContext


def _bridge_context() -> FacilityContext:
    return FacilityContext(
        facility_name="测试大桥", facility_type="bridge", facility_noun="桥梁"
    )


def test_composer_keeps_only_source_summary_and_explicit_evidence():
    answers = compose_official_answers(
        summary=BridgeSummary(
            overall_grade="B级",
            overall_conclusion="报告明确结论：该桥总体技术状况为B级。",
            risk_points="梁体裂缝长期发展会影响耐久性。",
        ),
        defects=(DefectObservation(location="梁体", defect_type="裂缝", description="梁体裂缝"),),
        recommendations=(Recommendation(content="修复裂缝", category="尽快维修"),),
        facility_context=_bridge_context(),
        source_causes=("梁体裂缝主要由于温度收缩造成。",),
        document_text="梁体裂缝长期发展会影响结构耐久性。",
    )
    assert answers.overall_conclusion == "报告明确结论：该桥总体技术状况为B级。"
    assert answers.risk_points == "梁体裂缝长期发展会影响耐久性。"
    assert answers.causes == ("梁体裂缝主要由于温度收缩造成",)
    assert answers.treatments == ("修复裂缝",)
    assert answers.safety_impact == ("梁体裂缝长期发展会影响结构耐久性。",)


def test_composer_does_not_fill_missing_fields_with_templates():
    answers = compose_official_answers(
        summary=BridgeSummary(overall_grade="B级"),
        defects=(DefectObservation(location="梁体", defect_type="裂缝", description="梁体裂缝"),),
        recommendations=(),
        facility_context=_bridge_context(),
        document_text="本报告记录梁体裂缝。",
    )
    assert answers.overall_conclusion == ""
    assert answers.risk_points == ""
    assert answers.causes == ()
    assert answers.safety_impact == ()
    assert all(not item for item in answers.detailed_conclusion)


def test_composer_rejects_calculation_fragments_from_safety_impacts():
    answers = compose_official_answers(
        summary=BridgeSummary(),
        defects=(),
        recommendations=(),
        facility_context=_bridge_context(),
        document_text=(
            "截面号18，实测值10.30MPa，理论值46.87MPa，校验系数0.211，承载能力满足。"
            "梁体裂缝暂不影响承载能力。"
        ),
    )
    assert answers.safety_impact == ("梁体裂缝暂不影响承载能力。",)
