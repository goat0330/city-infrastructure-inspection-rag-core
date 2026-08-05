from __future__ import annotations

import json
from pathlib import Path

from src.extraction import extract_report, predict_batch
from tests.fixtures.word.ooxml_factory import cell, paragraph, row, table, write_docx


def _summary_table() -> str:
    return table(
        row(cell("字段"), cell("内容")),
        row(cell("桥梁名称"), cell("集成测试桥")),
        row(cell("桥梁编号"), cell("")),
        row(cell("报告日期"), cell("2013年12月")),
        row(cell("总体评分"), cell("88.0")),
        row(cell("总体等级"), cell("B级")),
    )


def _defect_table() -> str:
    return table(
        row(cell("序号"), cell("病害部位"), cell("病害类型"), cell("病害描述")),
        row(cell("1"), cell("桥面"), cell("裂缝"), cell("局部裂缝")),
    )


def _recommendation_table() -> str:
    return table(
        row(cell("序号"), cell("建议类别"), cell("建议内容"), cell("病害部位")),
        row(cell("1"), cell("尽快维修"), cell("封闭裂缝"), cell("桥面")),
    )


def _uncategorized_recommendation_table() -> str:
    return table(
        row(cell("序号"), cell("建议类别"), cell("建议内容"), cell("病害部位")),
        row(cell("1"), cell(""), cell("封闭裂缝"), cell("桥面")),
    )


def _write_fixture(path: Path) -> Path:
    return write_docx(
        path,
        paragraph("桥梁概要",),
        _summary_table(),
        paragraph("病害明细表"),
        _defect_table(),
        paragraph("处置建议"),
        _recommendation_table(),
    )


def _write_uncategorized_fixture(path: Path) -> Path:
    return write_docx(
        path,
        paragraph("桥梁概要"),
        _summary_table(),
        paragraph("病害明细表"),
        _defect_table(),
        paragraph("处置建议"),
        _uncategorized_recommendation_table(),
    )


def test_extract_report_assembles_prediction_and_side_metadata(tmp_path: Path) -> None:
    source = _write_fixture(tmp_path / "集成测试桥.docx")

    result = extract_report(source, source_file="2013年/集成测试桥.docx")

    assert result.prediction.sample_id == "2013年/集成测试桥"
    assert result.prediction.source_file == "2013年/集成测试桥.docx"
    assert result.prediction.summary.bridge_name == "集成测试桥"
    assert len(result.prediction.defects) == 1
    assert len(result.prediction.recommendations) == 1
    assert result.prediction.summary.trend == "无"
    assert result.prediction.summary.recommendations_summary == "0条立即处置、1条尽快维修、0条预防性养护"
    assert result.facility_context.facility_name == "集成测试桥"
    assert result.prediction.defects[0].evidence
    assert result.prediction.recommendations[0].evidence
    assert result.route_count > 0
    assert result.prediction.causes == ()


def test_extract_report_semantic_off_keeps_baseline_without_client_calls(tmp_path: Path) -> None:
    source = _write_fixture(tmp_path / "语义关闭桥.docx")

    class ExplodingAdapter:
        def __getattr__(self, name: str):
            raise AssertionError(f"semantic adapter called while off: {name}")

    baseline = extract_report(source)
    disabled = extract_report(
        source,
        semantic_enabled=False,
        semantic_client=ExplodingAdapter(),
        semantic_index=ExplodingAdapter(),
    )

    assert disabled.prediction.to_dict() == baseline.prediction.to_dict()
    assert disabled.semantic_trace == {}


def test_extract_report_can_enter_injected_semantic_candidate_path(tmp_path: Path) -> None:
    source = _write_uncategorized_fixture(tmp_path / "语义桥.docx")
    calls: list[str] = []

    def retrieve(candidate):
        calls.append(f"retrieve:{candidate.candidate_id}")
        return [{"evidence_id": candidate.evidence_ids[0], "text": candidate.source_text}]

    def decide(candidate, evidence):
        calls.append(f"decide:{candidate.candidate_id}")
        return {
            "candidate_id": candidate.candidate_id,
            "task_type": candidate.task_type,
            "decision": "resolved",
            "evidence_ids": [evidence[0]["evidence_id"]],
            "confidence": 0.9,
            "selection_reason": "测试语义类别",
            "result": {"category": "预防性养护"},
        }

    result = extract_report(
        source,
        semantic_enabled=True,
        semantic_retriever=retrieve,
        semantic_decider=decide,
    )

    assert calls
    assert result.prediction.recommendations[0].category == "预防性养护"
    assert result.semantic_trace["semantic_enabled"] is True
    assert result.semantic_trace["completed_candidate_ids"]


def test_predict_batch_keeps_successes_when_one_docx_fails(tmp_path: Path) -> None:
    input_dir = tmp_path / "converted"
    input_dir.mkdir()
    _write_fixture(input_dir / "valid.docx")
    (input_dir / "broken.docx").write_bytes(b"not-a-docx")
    output = tmp_path / "predictions.jsonl"
    report = tmp_path / "run-report.json"

    result = predict_batch(input_dir, output, report_path=report)

    assert result["input_count"] == 2
    assert result["prediction_count"] == 1
    assert result["failed_count"] == 1
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["schema_version"] == "prediction-v1"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert {item["status"] for item in payload["records"]} == {"succeeded", "failed"}
