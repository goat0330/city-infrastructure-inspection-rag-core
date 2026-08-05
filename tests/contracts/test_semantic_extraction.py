from __future__ import annotations

import pytest

from src.contracts.semantic_extraction import (
    DefectRowValidationResult,
    EvidenceSelectionResult,
    ExtractionCandidate,
    RecommendationCategoryResult,
    SemanticDecision,
    fallback_fields_for,
    validate_decision_for_candidate,
)


def _candidate(task_type: str = "defect_row_validation") -> ExtractionCandidate:
    return ExtractionCandidate(
        candidate_id="sample-1:defect:7",
        sample_id="sample-1",
        task_type=task_type,  # type: ignore[arg-type]
        source_text="右洞口处，顶板车辆刮痕，见图2.1.1",
        evidence_ids=("table:2:row:7",),
        context_before="病害明细",
        context_after="照片1",
        rule_output={"location": "顶板", "defect_type": "刮痕"},
        facility_context={"facility_type": "pedestrian_underpass"},
        context={"table_headers": ["部位", "类型", "描述"]},
    )


def test_candidate_round_trip_and_fallback_fields() -> None:
    candidate = _candidate()

    restored = ExtractionCandidate.from_dict(candidate.to_dict())

    assert restored == candidate
    assert restored.fallback_fields == ("defects",)


def test_candidate_rejects_unknown_task_type() -> None:
    with pytest.raises(ValueError, match="task_type"):
        _candidate("unknown")


def test_resolved_defect_decision_round_trip() -> None:
    decision = SemanticDecision(
        candidate_id="sample-1:defect:7",
        task_type="defect_row_validation",
        decision="resolved",
        evidence_ids=("table:2:row:7",),
        confidence=0.97,
        selection_reason="该行包含具体构件、病害类型和位置，不是图题。",
        result=DefectRowValidationResult(
            is_defect=True,
            normalized_location="顶板",
            normalized_defect_type="车辆刮痕",
        ),
    )

    restored = SemanticDecision.from_dict(decision.to_dict())

    assert restored == decision
    assert restored.fallback_fields == ("defects",)


def test_non_defect_must_not_emit_normalized_fields() -> None:
    with pytest.raises(ValueError, match="non-defect"):
        DefectRowValidationResult(
            is_defect=False,
            normalized_location="顶板",
        )


def test_recommendation_category_is_closed_set() -> None:
    with pytest.raises(ValueError, match="category"):
        RecommendationCategoryResult(category="观察处理")  # type: ignore[arg-type]


def test_evidence_selection_requires_selected_ids_in_decision_evidence() -> None:
    with pytest.raises(ValueError, match="selected_evidence_ids"):
        SemanticDecision(
            candidate_id="sample-1:conclusion:1",
            task_type="conclusion_evidence_selection",
            decision="resolved",
            evidence_ids=("paragraph:8",),
            confidence=0.9,
            selection_reason="综合评定段优先于单项检测结果。",
            result=EvidenceSelectionResult(
                selected_evidence_ids=("paragraph:9",),
                selected_text="综合评定为一类，处于良好状态。",
            ),
        )


def test_unresolved_decision_has_task_scoped_fallback() -> None:
    decision = SemanticDecision.unresolved(
        candidate_id="sample-1:risk:1",
        task_type="risk_evidence_selection",
        selection_reason="候选证据不足。",
    )

    assert decision.result is None
    assert decision.fallback_fields == ("summary.risk_points",)
    assert fallback_fields_for("recommendation_category") == (
        "recommendations",
        "summary.recommendations_summary",
    )


def test_task_result_type_must_match() -> None:
    with pytest.raises(TypeError, match="RecommendationCategoryResult"):
        SemanticDecision(
            candidate_id="sample-1:recommendation:3",
            task_type="recommendation_category",
            decision="resolved",
            evidence_ids=("paragraph:31",),
            confidence=0.8,
            selection_reason="属于具体修复措施。",
            result=DefectRowValidationResult(is_defect=True),
        )


def test_cross_worktree_validation_rejects_unknown_evidence() -> None:
    candidate = _candidate()
    decision = SemanticDecision(
        candidate_id=candidate.candidate_id,
        task_type=candidate.task_type,
        decision="resolved",
        evidence_ids=("retrieval:unknown",),
        confidence=0.8,
        selection_reason="语义判别。",
        result=DefectRowValidationResult(is_defect=True),
    )

    with pytest.raises(ValueError, match="outside available evidence"):
        validate_decision_for_candidate(candidate, decision)

    validate_decision_for_candidate(
        candidate,
        decision,
        available_evidence_ids=("retrieval:unknown",),
    )
