from dataclasses import replace
import os
from pathlib import Path

from src.contracts import BridgeSummary, DocumentModel, InspectionPrediction, Recommendation
from src.extraction.output_normalizer import normalize_prediction_output
from src.extraction.summary.extractor import extract_summary
from src.rendering.submission_document import build_submission_document


def test_filename_facts_restore_previous_and_current_grade_and_trend():
    document = DocumentModel(
        "一处-官方院子桥式通道报告(原B级，现A级）.docx",
        (),
    )
    result = extract_summary(document, routes=())
    assert result.summary.bridge_name == "官方院子桥式通道"
    assert result.summary.previous_overall_grade == "B级"
    assert result.summary.overall_grade == "A级"
    assert "由B级变为A级" in result.summary.trend


def test_filename_facility_keeps_source_roman_numeral_and_drops_post_report_chainage():
    roman = extract_summary(
        DocumentModel("二处-界石立交主线III号桥报告（原B级，现B级）.docx", ()),
        routes=(),
    )
    chainage = extract_summary(
        DocumentModel("三处-华陶跨线桥报告（K42+350）（原A级，现B级）.docx", ()),
        routes=(),
    )
    assert roman.summary.bridge_name == "界石立交主线III号桥"
    assert chainage.summary.bridge_name == "华陶跨线桥"


def test_prediction_resolves_categories_before_rebuilding_summary():
    prediction = InspectionPrediction(
        summary=BridgeSummary(recommendations_summary="0条立即处置、0条尽快维修、0条预防性养护"),
        recommendations=(
            Recommendation(content="修复梁底裂缝", category=""),
            Recommendation(content="加强日常检查", category=""),
        ),
    )
    result = normalize_prediction_output(prediction)
    assert [item.category for item in result.recommendations] == ["尽快维修", "预防性养护"]
    assert result.summary.recommendations_summary == "0条立即处置、1条尽快维修、1条预防性养护"


def test_renderer_summary_uses_same_resolved_categories_as_rows():
    record = {
        "summary": {"bridge_name": "测试桥", "recommendations_summary": "0条立即处置、0条尽快维修、0条预防性养护"},
        "recommendations": [
            {"index": "1", "category": "", "content": "修复梁底裂缝", "location": "梁底"},
            {"index": "2", "category": "", "content": "加强日常检查", "location": "桥梁"},
        ],
    }
    document = build_submission_document(record)
    assert document.scalars["recommendations_summary"] == "0条立即处置、1条尽快维修、1条预防性养护"
    assert [row["category"] for row in document.recommendations] == ["尽快维修", "预防性养护"]


def test_platform_consistency_gate_accepts_resolved_record(tmp_path):
    import json
    import subprocess
    import sys

    payload = {
        "sample_id": "一处-测试桥报告（原B级，现A级）",
        "source_file": "一处-测试桥报告（原B级，现A级）.docx",
        "summary": {
            "overall_grade": "A级",
            "previous_overall_grade": "B级",
            "trend": "与上一次定检相比，总体技术状况等级由B级变为A级。",
            "recommendations_summary": "0条立即处置、1条尽快维修、0条预防性养护",
            "overall_conclusion": "报告明确结论。",
            "risk_points": "",
        },
        "recommendations": [
            {"category": "尽快维修", "content": "修复裂缝", "location": "梁底"}
        ],
        "detailed_conclusion": ["报告明确结论。"],
        "causes": [],
        "safety_impact": [],
    }
    input_path = tmp_path / "prediction.jsonl"
    input_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "check_platform_consistency.py"),
            "--input",
            str(input_path),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_platform_consistency_gate_rejects_visible_contradictions(tmp_path):
    import json
    import subprocess
    import sys

    payload = {
        "sample_id": "一处-测试桥报告（原B级，现A级）",
        "source_file": "一处-测试桥报告（原B级，现A级）.docx",
        "summary": {
            "overall_grade": "B级",
            "previous_overall_grade": "无",
            "trend": "无",
            "recommendations_summary": "0条立即处置、0条尽快维修、0条预防性养护",
            "overall_conclusion": "",
            "risk_points": "",
        },
        "recommendations": [
            {"category": "", "content": "修复裂缝", "location": "梁底"}
        ],
        "detailed_conclusion": ["无。本次检测病害具体表现为裂缝。"],
        "causes": [],
        "safety_impact": [],
    }
    input_path = tmp_path / "prediction.jsonl"
    input_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "check_platform_consistency.py"),
            "--input",
            str(input_path),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "filename_current_grade_conflict" in result.stdout
    assert "recommendation_summary_mismatch" in result.stdout


def test_official_composer_source_contains_no_generic_engineering_templates():
    source = (Path(__file__).resolve().parents[2] / "src" / "extraction" / "official_answer_composer.py").read_text(encoding="utf-8")
    forbidden = (
        "车辆荷载长期作用、温度变化及材料老化共同影响",
        "混凝土保护层破损、施工密实性不足及长期环境侵蚀",
        "可能削弱结构整体性",
        "若不及时处理，会影响使用功能并降低构件耐久性",
    )
    assert not any(text in source for text in forbidden)


def test_production_pipeline_keeps_official_composer_disabled_by_default():
    import inspect
    from src.extraction.pipeline import extract_report

    assert inspect.signature(extract_report).parameters["official_composer_enabled"].default is False
