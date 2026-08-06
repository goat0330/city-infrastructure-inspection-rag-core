from src.contracts.prediction import BridgeSummary, DefectObservation, Recommendation
from src.extraction.official_answer_composer import compose_official_answers
from src.extraction.summary.facility_context import FacilityContext


def _bridge_context(name: str = "测试大桥") -> FacilityContext:
    return FacilityContext(
        facility_name=name,
        facility_type="bridge",
        facility_noun="桥梁",
        report_date="2012年12月",
    )


def _bridge_defects() -> tuple[DefectObservation, ...]:
    return (
        DefectObservation(
            location="主梁",
            defect_type="露筋锈蚀",
            description="主梁局部露筋锈蚀",
        ),
        DefectObservation(
            location="伸缩缝",
            defect_type="堵塞",
            description="伸缩缝沉积物堵塞",
        ),
    )


def test_official_answers_use_sample_structure_without_gold_fixed_tail():
    answers = compose_official_answers(
        summary=BridgeSummary(
            overall_score="89.46",
            overall_grade="B级",
            superstructure_score="86.10",
            superstructure_grade="B级",
        ),
        defects=_bridge_defects(),
        recommendations=(
            Recommendation(
                category="尽快维修",
                content="对露筋锈蚀部位进行修复",
                location="主梁",
            ),
        ),
        facility_context=_bridge_context(),
        document_text="本报告未提供历史检测对比资料。",
    )
    assert answers.overall_conclusion.startswith("本次定检结果表明")
    assert answers.detailed_conclusion[0].startswith(
        "经综合评定，该桥总体技术状况评分 89.46 分"
    )
    assert answers.detailed_conclusion[1].startswith(
        "本次报告未提供往年检测评分及病害对比数据"
    )
    assert answers.detailed_conclusion[2].startswith("目前，该桥")
    assert answers.detailed_conclusion[3].startswith("综上，该桥")
    assert "较突出病害" in answers.detailed_conclusion[3]
    assert "建议结合建议明细" in answers.detailed_conclusion[3]
    assert "该桥梁" not in "".join(answers.detailed_conclusion)
    assert "未提取到" not in "".join(answers.detailed_conclusion)
    assert "建议" not in answers.risk_points
    assert len(answers.detailed_conclusion) == 4


def test_first_inspection_requires_report_evidence():
    missing = compose_official_answers(
        summary=BridgeSummary(overall_grade="B级"),
        defects=_bridge_defects(),
        recommendations=(),
        facility_context=_bridge_context(),
        document_text="本报告未提供历史检测资料。",
    )
    first = compose_official_answers(
        summary=BridgeSummary(overall_grade="B级"),
        defects=_bridge_defects(),
        recommendations=(),
        facility_context=_bridge_context(),
        document_text="本次为该桥首次定期检测。",
    )
    assert missing.history_status == "historical_comparison_missing"
    assert missing.detailed_conclusion[1].startswith("本次报告未提供")
    assert first.history_status == "first_inspection_confirmed"
    assert first.detailed_conclusion[1].startswith("本次为桥梁首次定期检测")


def test_historical_comparison_uses_available_branch():
    answers = compose_official_answers(
        summary=BridgeSummary(
            overall_grade="B级",
            previous_overall_grade="C级",
            trend="与上次检测相比，主要病害未见明显扩展",
        ),
        defects=_bridge_defects(),
        recommendations=(),
        facility_context=_bridge_context(),
        document_text="报告对上次检测结果进行了比较。",
    )
    assert answers.history_status == "historical_comparison_available"
    assert answers.detailed_conclusion[1].startswith("与上次检测相比")


def test_pedestrian_overpass_uses_bridge_pronoun():
    answers = compose_official_answers(
        summary=BridgeSummary(overall_score="89.95", overall_grade="B级"),
        defects=(
            DefectObservation(
                location="梁体",
                defect_type="裂缝",
                description="梁体腹板竖向裂缝",
            ),
        ),
        recommendations=(),
        facility_context=FacilityContext(
            facility_name="上界路K38+576人行天桥",
            facility_type="bridge",
            facility_noun="人行天桥",
        ),
        document_text="首次定期检测。",
    )
    text = "".join(answers.detailed_conclusion)
    assert "该桥" in text
    assert "该人行天桥" not in text


def test_pedestrian_underpass_keeps_its_facility_pronoun():
    answers = compose_official_answers(
        summary=BridgeSummary(overall_grade="一类"),
        defects=(
            DefectObservation(
                location="侧墙",
                defect_type="裂缝",
                description="侧墙竖向裂缝",
            ),
        ),
        recommendations=(),
        facility_context=FacilityContext(
            facility_name="测试通道",
            facility_type="pedestrian_underpass",
            facility_noun="人行通道",
        ),
        document_text="首次定期检测。",
    )
    text = "".join(answers.detailed_conclusion)
    assert "该人行通道" in text
    assert "该桥" not in text
    assert answers.detailed_conclusion[1].startswith("本次为人行通道首次定期检测")


def test_defect_prose_does_not_repeat_location_type_description():
    answers = compose_official_answers(
        summary=BridgeSummary(overall_grade="B级"),
        defects=(
            DefectObservation(
                location="右幅桥面",
                defect_type="破损",
                description="界石侧桥台路面破损，见照片5.1.1-1",
            ),
            DefectObservation(
                location="右幅伸缩缝",
                defect_type="开裂",
                description="右幅伸缩缝内沉积物阻塞",
            ),
        ),
        recommendations=(),
        facility_context=_bridge_context(),
    )
    narrative = " ".join(answers.detailed_conclusion)
    assert "右幅桥面，破损" not in narrative
    assert "桥面铺装破损" in narrative
    assert "伸缩缝沉积物堵塞" in narrative or "伸缩缝堵塞" in narrative
    assert "其他部位" not in narrative


def test_causes_reject_evaluation_and_dictionary_noise():
    answers = compose_official_answers(
        summary=BridgeSummary(overall_grade="B级"),
        defects=(
            DefectObservation(
                location="梁体",
                defect_type="露筋锈蚀",
                description="梁体露筋锈蚀",
            ),
        ),
        recommendations=(),
        facility_context=_bridge_context(),
        source_causes=(
            "桥面系目前能够满足功能要求。",
            "露筋锈蚀：混凝土保护层破损或剥落。",
            "主要结论 外观检测结果显示梁体露筋锈蚀。",
        ),
    )
    text = " ".join(answers.causes)
    assert "满足功能要求" not in text
    assert "主要结论" not in text
    assert "露筋锈蚀：" not in text
    assert "主要是由于" in text


def test_safety_impact_is_dynamic_official_language():
    answers = compose_official_answers(
        summary=BridgeSummary(overall_grade="B级"),
        defects=(
            DefectObservation(
                location="桥面铺装",
                defect_type="破损",
                description="桥面铺装局部破损",
            ),
            DefectObservation(
                location="梁体腹板",
                defect_type="裂缝",
                description="梁体腹板竖向裂缝",
            ),
            DefectObservation(
                location="支座垫石",
                defect_type="破损",
                description="支座垫石局部破损",
            ),
        ),
        recommendations=(),
        facility_context=_bridge_context(),
    )
    assert answers.safety_impact[0].startswith("桥面系现状病害主要表现为")
    assert answers.safety_impact[1].startswith("上部结构主要存在")
    assert answers.safety_impact[2].startswith("下部结构主要存在")
    assert answers.safety_impact[-1].startswith("综合来看")
    all_text = "".join(answers.safety_impact)
    assert "其他部位" not in all_text
    assert "已有证据为" not in all_text
