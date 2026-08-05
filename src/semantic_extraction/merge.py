from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping, Sequence
from typing import Any

from src.contracts.semantic_extraction import (
    EvidenceSelectionResult,
    ExtractionCandidate,
    RecommendationCategoryResult,
    SemanticDecision,
)


def merge_decisions(
    baseline: Mapping[str, Any],
    candidates: Sequence[ExtractionCandidate],
    decisions: Sequence[SemanticDecision],
) -> tuple[dict[str, Any], dict[str, str], list[str], list[str]]:
    """Apply only safe semantic fields and preserve the deterministic shape."""

    prediction = deepcopy(dict(baseline))
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    field_states: dict[str, str] = {}
    fallback_fields: set[str] = set()
    completed: list[str] = []
    for decision in decisions:
        candidate = candidate_by_id[decision.candidate_id]
        if decision.decision == "unresolved" or decision.result is None:
            fallback_fields.update(decision.fallback_fields)
            for field in decision.fallback_fields:
                field_states[field] = "fallback"
            continue
        completed.append(candidate.candidate_id)
        if candidate.task_type == "recommendation_category":
            result = decision.result
            if isinstance(result, RecommendationCategoryResult):
                index = candidate.context.get("recommendation_index")
                recommendations = prediction.get("recommendations")
                if isinstance(recommendations, tuple):
                    recommendations = list(recommendations)
                    prediction["recommendations"] = recommendations
                if isinstance(index, int) and isinstance(recommendations, list) and 0 <= index < len(recommendations):
                    recommendations[index]["category"] = result.category
                field_states["recommendations"] = "enhanced"
        elif candidate.task_type in {"conclusion_evidence_selection", "risk_evidence_selection"}:
            result = decision.result
            if isinstance(result, EvidenceSelectionResult):
                summary = prediction.setdefault("summary", {})
                key = "overall_conclusion" if candidate.task_type == "conclusion_evidence_selection" else "risk_points"
                summary[key] = result.selected_text
                field_states[f"summary.{key}"] = "enhanced"
        else:
            # Defect row validation records the decision without deleting or rewriting rows.
            field_states["defects"] = "enhanced"
    return prediction, field_states, sorted(fallback_fields), completed
