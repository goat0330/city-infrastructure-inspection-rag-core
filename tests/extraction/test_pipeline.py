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
    # No explicit cause evidence exists in the fixture; production must not
    # invent a generic engineering cause.
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


def test_extract_report_reads_grade_mode_environment_at_runtime(tmp_path: Path, monkeypatch) -> None:
    source = write_docx(
        tmp_path / "等级口径桥.docx",
        paragraph("桥梁概要"),
        table(
            row(cell("字段"), cell("内容")),
            row(cell("桥梁名称"), cell("等级口径桥")),
            row(cell("总体评分"), cell("92.0")),
            row(cell("总体等级"), cell("B级")),
        ),
    )

    monkeypatch.setenv("GRADE_MODE", "report")
    report = extract_report(source)
    monkeypatch.setenv("GRADE_MODE", "generic")
    generic = extract_report(source)

    assert report.prediction.summary.overall_score == generic.prediction.summary.overall_score == "92.0"
    assert report.prediction.summary.overall_grade == "B级"
    assert generic.prediction.summary.overall_grade == "A级"


def test_extract_report_official_summary_style_is_explicit_opt_in(tmp_path: Path, monkeypatch) -> None:
    source = _write_fixture(tmp_path / "摘要句式桥.docx")

    monkeypatch.setenv("SUMMARY_STYLE", "legacy")
    legacy = extract_report(source)
    monkeypatch.setenv("SUMMARY_STYLE", "official")
    official = extract_report(source)

    assert legacy.prediction.summary.overall_conclusion == ""
    assert official.prediction.summary.overall_conclusion.startswith("本次定检结果表明，桥梁")
    assert "桥面系存在裂缝" in official.prediction.summary.overall_conclusion


def test_deterministic_risk_points_keep_defect_consequence_pair_without_invention(tmp_path: Path) -> None:
    source = write_docx(
        tmp_path / "风险证据桥.docx",
        paragraph("桥梁概要"),
        _summary_table(),
        paragraph("病害明细表"),
        _defect_table(),
        paragraph("评估结论"),
        paragraph("桥面裂缝进一步发展可能影响结构耐久性。"),
    )

    result = extract_report(source)

    assert "裂缝" in result.prediction.summary.risk_points
    assert "耐久性" in result.prediction.summary.risk_points


def test_v16_public_summary_hygiene_runs_after_live_narrative(monkeypatch, tmp_path: Path) -> None:
    source = _write_fixture(tmp_path / "V16顺序桥.docx")
    from src.extraction import pipeline as pipeline_module

    events: list[str] = []
    real_public = pipeline_module.normalize_public_summary_output

    def fake_narrative(**kwargs):
        events.append("narrative")
        return {
            "enhanced_prediction": kwargs["baseline_prediction"],
            "field_results": {},
            "retrieval_results": [],
            "selection_reasons": {},
            "validation_errors": [],
            "field_fallbacks": [],
            "used_fallback": False,
            "retrieval_count": 0,
            "call_metrics": {},
        }

    def public_after(prediction):
        events.append("public")
        return real_public(prediction)

    monkeypatch.setattr(pipeline_module, "_run_live_narrative", fake_narrative)
    monkeypatch.setattr(pipeline_module, "normalize_public_summary_output", public_after)

    pipeline_module.extract_report(
        source,
        semantic_enabled=True,
        semantic_client=object(),
        semantic_index=object(),
    )

    assert events == ["narrative", "public"]


def test_v18_gold_schema_mode_is_opt_in_and_canonicalizes_field_granularity(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_docx(
        tmp_path / "V18粒度桥.docx",
        paragraph("桥梁概要"),
        _summary_table(),
        paragraph("病害明细表"),
        table(
            row(cell("序号"), cell("病害部位"), cell("病害类型"), cell("病害描述")),
            row(cell("1"), cell("拱腰"), cell("泛碱"), cell("2#孔右拱腰距左洞口33m～40m处局部泛碱")),
        ),
        paragraph("处置建议"),
        table(
            row(cell("序号"), cell("建议类别"), cell("建议内容"), cell("病害部位")),
            row(cell("1"), cell("尽快维修"), cell("对于盖梁露筋，挡块破损等病害，建议及时维修处理。"), cell("盖梁")),
        ),
    )

    monkeypatch.setenv("GOLD_SCHEMA_MODE", "legacy")
    legacy = extract_report(source)
    monkeypatch.setenv("GOLD_SCHEMA_MODE", "v18")
    v18 = extract_report(source)

    assert legacy.prediction.defects[0].location == "拱腰"
    assert v18.prediction.defects[0].location == "右拱腰"
    assert "33m" in v18.prediction.defects[0].description
    assert legacy.prediction.recommendations[0].location == "盖梁"
    assert v18.prediction.recommendations[0].location == "盖梁、挡块"


def test_v18_gold_schema_risk_rewrite_is_opt_in_and_runs_at_final_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    from src.extraction import pipeline as pipeline_module

    source = _write_fixture(tmp_path / "V18风险桥.docx")
    calls: list[tuple[str, int]] = []

    def fake_risk(current, defects, *, limit=5):
        calls.append((str(current), len(defects)))
        return "V18具体突出病害。"

    monkeypatch.setattr(pipeline_module, "compose_gold_risk_points", fake_risk)

    monkeypatch.setenv("GOLD_SCHEMA_MODE", "legacy")
    legacy = pipeline_module.extract_report(source)
    assert calls == []
    assert legacy.prediction.summary.risk_points != "V18具体突出病害。"

    monkeypatch.setenv("GOLD_SCHEMA_MODE", "v18")
    v18 = pipeline_module.extract_report(source)
    assert calls and calls[-1][1] == len(v18.prediction.defects)
    assert v18.prediction.summary.risk_points == "V18具体突出病害。"
