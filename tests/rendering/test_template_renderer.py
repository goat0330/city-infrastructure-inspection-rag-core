from __future__ import annotations

from pathlib import Path
import re

from docx import Document

from src.contracts import BridgeSummary, DefectObservation, InspectionPrediction, Recommendation
from src.rendering import build_submission_document, render_template_report


TEMPLATE = Path("assets/templates/information_extraction_v1.docx")
FIELDS = Path("assets/templates/template_fields.json")


def _prediction() -> InspectionPrediction:
    return InspectionPrediction(
        sample_id="synthetic",
        source_file="synthetic.docx",
        summary=BridgeSummary(
            bridge_name="测试桥",
            report_date="2026年8月",
            overall_score="88.00",
            overall_grade="B级",
            superstructure_score="90.00",
            superstructure_grade="A级",
            substructure_score="86.00",
            substructure_grade="B级",
            deck_score="80.00",
            deck_grade="C级",
            previous_overall_score="无",
            previous_overall_grade="无",
            trend="无",
            overall_conclusion="总体状况良好。",
            risk_points="支座开裂影响耐久性。",
            recommendations_summary="1条尽快维修、1条预防性养护",
        ),
        detailed_conclusion=("评分段。", "历史段。", "状态段。", "综合段。"),
        recommendations=(
            Recommendation(index="8", category="尽快维修", content="修复支座。", location="支座"),
            Recommendation(index="20", category="预防性养护", content="加强巡检。", location="桥梁"),
        ),
        defects=(
            DefectObservation(index="3", location="支座", defect_type="开裂", description="第一处", is_new="否", previous_status="无", development="无"),
            DefectObservation(index="3", location="支座", defect_type="开裂", description="第二处", is_new="否", previous_status="无", development="无"),
            DefectObservation(index="9", location="桥面", defect_type="破损", description="第三处", is_new="否", previous_status="无", development="无"),
        ),
        causes=("材料老化。",),
        treatments=("修复支座。", "加强巡检。"),
        safety_impact=("可能影响耐久性。",),
    )


def _all_text(document: Document) -> str:
    values = [p.text for p in document.paragraphs]
    values.extend(c.text for t in document.tables for r in t.rows for c in r.cells)
    return "\n".join(values)


def test_production_template_is_dynamic_prototype() -> None:
    document = Document(TEMPLATE)
    assert len(document.tables) == 3
    assert [len(table.rows) for table in document.tables] == [17, 2, 2]
    text = _all_text(document)
    assert "{{recommendation.content}}" in text
    assert "{{defect.description}}" in text
    assert "{{cause.text}}" in text
    assert not re.search(r"_[1-9]\}\}", text)


def test_renders_dynamic_rows_and_no_placeholders(tmp_path: Path) -> None:
    output = render_template_report(
        _prediction(),
        tmp_path / "rendered.docx",
        template_path=TEMPLATE,
        fields_path=FIELDS,
    )
    document = Document(output)
    text = _all_text(document)
    assert "{{" not in text
    assert document.tables[0].rows[1].cells[1].text == "测试桥"
    assert len(document.tables[1].rows) == 3
    assert [r.cells[0].text for r in document.tables[1].rows[1:]] == ["1", "2"]
    assert len(document.tables[2].rows) == 4
    assert document.tables[2].rows[1].cells[0].text == "1"
    assert document.tables[2].rows[3].cells[0].text == "2"
    assert "（1）材料老化。" in text
    assert "（2）加强巡检。" in text


def test_empty_dynamic_arrays_leave_headers_only(tmp_path: Path) -> None:
    prediction = InspectionPrediction(summary=BridgeSummary(bridge_name="空样本"))
    output = render_template_report(
        prediction,
        tmp_path / "empty.docx",
        template_path=TEMPLATE,
        fields_path=FIELDS,
    )
    document = Document(output)
    assert len(document.tables[1].rows) == 1
    assert len(document.tables[2].rows) == 1
    assert "{{" not in _all_text(document)


def test_submission_mapping_uses_existing_contract_names() -> None:
    submission = build_submission_document(_prediction())
    assert submission.scalars["deck_system_score"] == "80.00"
    assert submission.scalars["major_risks"] == "支座开裂影响耐久性。"
    assert submission.scalars["report_title"] == "测试桥·无对比年度的信息提取报告"
