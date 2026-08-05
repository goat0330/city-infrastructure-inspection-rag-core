from __future__ import annotations

from src.contracts.semantic_extraction import (
    DefectRowValidationResult,
    EvidenceSelectionResult,
    ExtractionCandidate,
    RecommendationCategoryResult,
    SemanticDecision,
)
from src.semantic_extraction import run_graph


def _candidate(candidate_id: str, task_type: str, **context) -> dict[str, object]:
    return ExtractionCandidate(
        candidate_id=candidate_id,
        sample_id="sample-1",
        task_type=task_type,
        source_text="原始报告证据",
        evidence_ids=(f"source:{candidate_id}",),
        context=context,
        rule_output={"original": True},
    ).to_dict()


def test_graph_supports_all_four_tasks_and_preserves_locked_defect_fields() -> None:
    baseline = {
        "defects": [{"description": "原始描述", "location": "原位置", "defect_type": "裂缝"}],
        "recommendations": [{"category": "尽快维修", "content": "原建议"}],
        "summary": {"overall_conclusion": "原结论", "risk_points": "原风险"},
    }
    candidates = [
        _candidate("d1", "defect_row_validation", defect_index=0),
        _candidate("r1", "recommendation_category", recommendation_index=0),
        _candidate("c1", "conclusion_evidence_selection"),
        _candidate("s1", "risk_evidence_selection"),
    ]

    def retrieve(candidate):
        return [{"evidence_id": f"retrieved:{candidate.candidate_id}", "text": "证据"}]

    def decide(candidate, evidence):
        evidence_id = evidence[0]["evidence_id"]
        if candidate.task_type == "defect_row_validation":
            result = DefectRowValidationResult(
                is_defect=True,
                normalized_location="新位置",
                normalized_defect_type="新类型",
            )
        elif candidate.task_type == "recommendation_category":
            result = RecommendationCategoryResult(category="预防性养护")
        else:
            result = EvidenceSelectionResult((evidence_id,), f"选中的{candidate.candidate_id}证据")
        return SemanticDecision(
            candidate_id=candidate.candidate_id,
            task_type=candidate.task_type,
            decision="resolved",
            evidence_ids=(evidence_id,),
            confidence=0.9,
            selection_reason="fake decision",
            result=result,
        )

    result = run_graph({"baseline_prediction": baseline, "candidates": candidates}, retriever=retrieve, decider=decide)

    assert result["completed_candidate_ids"] == ["d1", "r1", "c1", "s1"]
    assert result["merged_prediction"]["defects"][0] == baseline["defects"][0]
    assert result["merged_prediction"]["recommendations"][0]["category"] == "预防性养护"
    assert result["merged_prediction"]["summary"]["overall_conclusion"] == "选中的c1证据"
    assert result["merged_prediction"]["summary"]["risk_points"] == "选中的s1证据"


def test_invalid_evidence_is_task_scoped_fallback() -> None:
    baseline = {"summary": {"risk_points": "原风险"}, "defects": []}
    candidate = _candidate("risk-1", "risk_evidence_selection")

    def decide(candidate, evidence):
        return {
            "candidate_id": candidate.candidate_id,
            "task_type": candidate.task_type,
            "decision": "resolved",
            "evidence_ids": ["unknown:evidence"],
            "confidence": 0.8,
            "selection_reason": "invalid test decision",
            "result": {"selected_evidence_ids": ["unknown:evidence"], "selected_text": "错误"},
        }

    result = run_graph({"baseline_prediction": baseline, "candidates": [candidate]}, decider=decide)

    assert result["merged_prediction"] == baseline
    assert result["fallback_fields"] == ["summary.risk_points"]
    assert result["field_states"]["summary.risk_points"] == "fallback"
    assert result["validation_errors"]


def test_missing_decider_falls_back_without_changing_baseline() -> None:
    baseline = {"summary": {"overall_conclusion": "原结论"}}
    candidate = _candidate("conclusion-1", "conclusion_evidence_selection")

    result = run_graph({"baseline_prediction": baseline, "candidates": [candidate]})

    assert result["merged_prediction"] == baseline
    assert result["fallback_fields"] == ["summary.overall_conclusion"]
