from __future__ import annotations

from src.contracts import (
    BridgeSummary,
    DocumentModel,
    InspectionPrediction,
    ParagraphBlock,
    SourceAnchor,
)
from src.extraction.output_normalizer import normalize_prediction_output
from src.extraction.summary.extractor import extract_summary
from src.rendering.submission_document import build_submission_document


def _paragraph(index: int, text: str) -> ParagraphBlock:
    return ParagraphBlock(
        index,
        text,
        SourceAnchor("fixture.docx", index, text, paragraph_index=index),
    )


def test_report_grade_beats_conflicting_filename_grade() -> None:
    document = DocumentModel(
        "一处-测试桥报告（原B级，现A级）.docx",
        (_paragraph(0, "总体技术状况等级：B级"),),
    )
    result = extract_summary(document, routes=())
    assert result.summary.overall_grade == "B级"
    assert result.summary.previous_overall_grade == "B级"
    assert result.summary.trend == ""


def test_report_name_spelling_beats_filename_alias() -> None:
    document = DocumentModel(
        "二处-界石立交主线III号桥报告（原B级，现B级）.docx",
        (_paragraph(0, "桥梁名称：界石立交主线Ⅲ号桥(K12+300)"),),
    )
    result = extract_summary(document, routes=())
    assert result.summary.bridge_name == "界石立交主线Ⅲ号桥(K12+300)"


def test_final_normalizer_removes_generated_text_and_keeps_explicit_evidence() -> None:
    prediction = InspectionPrediction(
        summary=BridgeSummary(
            overall_conclusion=(
                "经综合评定，该桥总体技术状况等级为B级。"
                "混凝土强度52.7MPa、56.1MPa、59.2MPa。"
                "建议及时修复裂缝。"
            ),
            risk_points=(
                "于2014年进行了外观检查。"
                "梁底裂缝持续发展可能削弱构件耐久性。"
            ),
        ),
        detailed_conclusion=(
            "本次为桥梁定期检测，无往年检测评分、病害对比数据，不存在既有病害扩展情况。",
            "经检查，主梁存在裂缝。",
            "综上，报告建议修复裂缝。",
        ),
        causes=("裂缝可能与构件受力、材料收缩或温度变化有关。",),
        safety_impact=(
            "上部结构：已有证据为裂缝1条；报告未明确该类病害影响。",
            "综合评定认为现有裂缝暂不影响结构承载能力。",
        ),
    )
    result = normalize_prediction_output(prediction)
    assert result.summary.overall_conclusion == "经综合评定，该桥总体技术状况等级为B级"
    assert result.summary.risk_points == "梁底裂缝持续发展可能削弱构件耐久性"
    assert result.detailed_conclusion == ("经检查，主梁存在裂缝。",)
    assert result.causes == ()
    assert result.safety_impact == ("综合评定认为现有裂缝暂不影响结构承载能力。",)


def test_renderer_uses_facility_specific_subject_and_no_missing_prose() -> None:
    record = {
        "summary": {
            "bridge_name": "上界路K38+576人行天桥",
            "overall_score": "",
            "overall_grade": "B级",
            "trend": "",
            "overall_conclusion": "经综合评定，该人行天桥总体状况为B级。",
            "risk_points": "",
        },
        "detailed_conclusion": [],
    }
    document = build_submission_document(record)
    assert "该人行天桥" in document.score_and_grade
    assert "该文档无" not in "\n".join(
        (
            document.score_and_grade,
            document.history_and_defects,
            document.current_structure_state,
            document.comprehensive_judgement,
        )
    )


def test_score_paragraph_does_not_repeat_grade_or_echo_labels() -> None:
    from src.extraction.text_sections import _structured_score_paragraph

    paragraph = _structured_score_paragraph(
        {
            "bridge_name": "测试桥",
            "overall_score": "86.05",
            "overall_grade": "B级",
            "superstructure_score": "上部结构评分",
            "superstructure_grade": "B级",
            "substructure_score": "",
            "substructure_grade": "",
            "deck_score": "桥面系评分",
            "deck_grade": "",
        }
    )
    assert paragraph.count("总体技术状况等级为 B级") == 1
    assert "上部结构评分 上部结构评分 分" not in paragraph
    assert "桥面系评分 桥面系评分 分" not in paragraph


def test_legacy_generic_risk_template_is_removed() -> None:
    from src.extraction.output_normalizer import normalize_risk_points

    assert normalize_risk_points(
        "桥面铺装裂缝，若不及时处理，会影响使用功能并降低构件耐久性"
    ) == ""


def test_bridge_passage_uses_its_own_noun_in_context() -> None:
    from src.extraction.summary.facility_context import infer_facility_semantics
    from src.extraction.text_sections import _structured_score_paragraph

    raw, facility_type, noun = infer_facility_semantics("官方院子桥式通道")
    assert (raw, facility_type, noun) == ("桥式通道", "bridge", "桥式通道")
    paragraph = _structured_score_paragraph(
        {"bridge_name": "官方院子桥式通道", "overall_score": "86.05", "overall_grade": "B级"},
        facility_context={"facility_type": facility_type, "facility_noun": noun},
    )
    assert "该桥式通道" in paragraph
    assert "该桥总体" not in paragraph


def test_platform_gate_flags_dominant_report_date_without_overwriting_it(tmp_path) -> None:
    import json
    from pathlib import Path
    from scripts.check_platform_consistency import build_report

    path = Path(tmp_path) / "predictions.jsonl"
    rows = []
    for index in range(10):
        rows.append({
            "sample_id": str(index),
            "source_file": f"sample-{index}.docx",
            "summary": {
                "report_date": "2019年11月20日" if index < 9 else "2019年10月30日",
                "recommendations_summary": "0条立即处置、0条尽快维修、0条预防性养护",
                "overall_conclusion": "",
                "risk_points": "",
            },
            "recommendations": [],
            "detailed_conclusion": [],
            "causes": [],
            "safety_impact": [],
        })
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    report = build_report(path)
    assert report["valid"] is True
    assert report["dataset_warnings"] == [{
        "code": "dominant_report_date_requires_source_check",
        "detail": "'2019年11月20日' appears in 9/10 records",
    }]


def test_pedestrian_overpass_taxonomy_is_consistent() -> None:
    from src.extraction.summary.facility_context import infer_facility_semantics

    assert infer_facility_semantics("上界路K38+576人行天桥") == (
        "人行天桥", "pedestrian_overpass", "人行天桥"
    )
