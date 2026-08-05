from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.contracts.semantic_extraction import (
    ExtractionCandidate,
    SemanticDecision,
    validate_decision_for_candidate,
)


def _evidence_ids(items: Sequence[Mapping[str, Any]]) -> set[str]:
    result: set[str] = set()
    for item in items:
        for key in ("evidence_id", "id"):
            value = item.get(key)
            if value:
                result.add(str(value))
    return result


_FACILITY_TYPE_ALIASES = {
    "bridge": "bridge",
    "桥": "bridge",
    "桥梁": "bridge",
    "pedestrian_underpass": "pedestrian_underpass",
    "人行通道": "pedestrian_underpass",
    "人行地道": "pedestrian_underpass",
    "人行地通道": "pedestrian_underpass",
    "vehicle_underpass": "vehicle_underpass",
    "车行下穿道": "vehicle_underpass",
    "下穿道": "vehicle_underpass",
    "tunnel": "tunnel",
    "隧道": "tunnel",
    "culvert": "culvert",
    "涵洞": "culvert",
    "road": "road",
    "道路": "road",
    "underpass": "underpass",
    "通道": "underpass",
}


def _facility_type(value: object) -> str:
    text = " ".join(str(value or "").split()).strip().casefold()
    return _FACILITY_TYPE_ALIASES.get(text, text)


def _validate_retrieval_context(
    candidate: ExtractionCandidate,
    evidence: Sequence[Mapping[str, Any]],
) -> None:
    if any(item.get("_retrieval_error") for item in evidence):
        raise ValueError("retrieval failed before semantic decision")

    expected = _facility_type(
        candidate.facility_context.get("facility_type")
        or candidate.context.get("facility_type")
    )
    if not expected:
        return
    for item in evidence:
        actual_value = item.get("facility_type")
        if actual_value and _facility_type(actual_value) != expected:
            raise ValueError("retrieved evidence facility type mismatch")


def validate_decisions(
    candidates: Sequence[ExtractionCandidate],
    raw_decisions: Sequence[Mapping[str, Any] | SemanticDecision],
    retrieval_by_candidate: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[SemanticDecision], list[dict[str, str]], list[str]]:
    """Join, validate, and task-scope invalid decisions to fallback fields."""

    raw_by_id = {
        item.candidate_id if isinstance(item, SemanticDecision) else str(item.get("candidate_id", "")): item
        for item in raw_decisions
    }
    decisions: list[SemanticDecision] = []
    errors: list[dict[str, str]] = []
    fallback_fields: set[str] = set()
    for candidate in candidates:
        raw = raw_by_id.get(candidate.candidate_id)
        try:
            evidence = list(retrieval_by_candidate.get(candidate.candidate_id, ()))
            _validate_retrieval_context(candidate, evidence)
            decision = raw if isinstance(raw, SemanticDecision) else SemanticDecision.from_dict(raw or {})
            validate_decision_for_candidate(
                candidate,
                decision,
                available_evidence_ids=_evidence_ids(
                    evidence
                ),
            )
        except (TypeError, ValueError) as exc:
            errors.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "task_type": candidate.task_type,
                    "error": str(exc),
                }
            )
            decision = SemanticDecision.unresolved(
                candidate_id=candidate.candidate_id,
                task_type=candidate.task_type,
                selection_reason=f"validation failed: {exc}",
            )
        if decision.decision == "unresolved":
            fallback_fields.update(decision.fallback_fields)
        decisions.append(decision)
    return decisions, errors, sorted(fallback_fields)
