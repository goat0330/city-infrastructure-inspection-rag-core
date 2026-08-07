from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.summarize_semantic_runs import build_summary, summarize_run_directory


ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "runs" / "round2-semantic"


def _require_live_run(name: str) -> Path:
    path = RUNS_ROOT / name
    if not path.is_dir():
        pytest.skip("live semantic run artifacts are not bundled in the source review package")
    return path


def _run(summary: dict[str, object], name: str) -> dict[str, object]:
    return next(item for item in summary["runs"] if item["run"] == name)  # type: ignore[index]


def test_real_live_runs_report_resolution_fallback_and_timeout() -> None:
    _require_live_run("semantic-live-12-027-v4")
    _require_live_run("semantic-live-12-030-v1")
    summary = build_summary(RUNS_ROOT)
    v4 = _run(summary, "semantic-live-12-027-v4")
    dc = _run(summary, "semantic-live-12-030-v1")

    assert v4["candidate_count"] == 5
    assert v4["resolved"] == 5
    assert v4["unresolved"] == 0
    assert v4["fallback_fields"] == []
    assert v4["timeout_or_error_count"] == 0
    assert v4["all_locked_fields_unchanged"] is True

    assert dc["candidate_count"] == 6
    assert dc["resolved"] == 5
    assert dc["unresolved"] == 1
    assert dc["fallback_fields"] == ["recommendations", "summary.recommendations_summary"]
    assert dc["timeout_count"] == 1
    assert dc["timeout_or_error_count"] == 1
    assert dc["all_locked_fields_unchanged"] is True
    assert summary["score_improvement_confirmed"] is False


def test_summarizer_compares_locked_fields_categories_sources_and_tokens(tmp_path: Path) -> None:
    run_dir = tmp_path / "semantic-live-test"
    run_dir.mkdir()
    baseline = {
        "sample_id": "sample",
        "source_file": "sample.docx",
        "defects": [{"description": "same"}],
        "detailed_conclusion": ["same"],
        "causes": ["same"],
        "treatments": ["same"],
        "safety_impact": ["same"],
        "recommendations": [{"category": "尽快维修"}],
    }
    enhanced = {**baseline, "recommendations": [{"category": "预防性养护"}]}
    (run_dir / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    (run_dir / "enhanced_prediction.json").write_text(json.dumps(enhanced), encoding="utf-8")
    (run_dir / "candidates.json").write_text(json.dumps([{"candidate_id": "c1"}]), encoding="utf-8")
    (run_dir / "enhanced_prediction.trace.json").write_text(
        json.dumps(
            {
                "fallback_fields": [],
                "validation_errors": [],
                "model_calls": [
                    {
                        "model": "test-model",
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "enhanced_prediction.decisions.jsonl").write_text(
        json.dumps({"candidate_id": "c1", "decision": "resolved"}) + "\n", encoding="utf-8"
    )
    (run_dir / "enhanced_prediction.retrieval.json").write_text(
        json.dumps({"c1": [{"kind": "report_evidence"}, {"kind": "knowledge_card"}]}),
        encoding="utf-8",
    )

    result = summarize_run_directory(run_dir)
    assert result["locked_field_invariance"] == {
        "sample_id": True,
        "source_file": True,
        "defects": True,
        "detailed_conclusion": True,
        "causes": True,
        "treatments": True,
        "safety_impact": True,
    }
    assert result["category_changes"] == [
        {"index": 0, "baseline": "尽快维修", "enhanced": "预防性养护"}
    ]
    assert result["source_kind_counts"] == {"knowledge_card": 1, "report_evidence": 1}
    assert result["tokens"]["input_tokens"] == 3
    assert result["tokens"]["output_tokens"] == 2
    assert result["tokens"]["total_tokens"] == 5
    assert result["model_names"] == ["test-model"]


def test_missing_enhanced_artifact_is_reported_without_crashing() -> None:
    run_dir = _require_live_run("semantic-live-12-027-v1")
    result = summarize_run_directory(run_dir)
    assert result["status"] == "incomplete"
    assert result["candidate_count"] == 5
    assert result["decision_count"] == 0
    assert result["all_locked_fields_unchanged"] is None
