from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

try:
    from langgraph.graph import END, START, StateGraph
except ModuleNotFoundError:  # deterministic/injected tests do not need LangGraph
    END = START = StateGraph = None  # type: ignore[assignment]

from src.contracts.semantic_extraction import (
    ExtractionCandidate,
    SemanticDecision,
    SemanticExtractionGraphState,
)

from .merge import merge_decisions
from .retrieve import retrieve_candidate_evidence
from .state import DecisionCallable, RetrievalCallable, candidates_from_state
from .validate import validate_decisions


def build_graph(
    *,
    retriever: RetrievalCallable | None = None,
    decider: DecisionCallable | None = None,
):
    """Build the five-node semantic extraction subgraph."""

    def load_candidates(state: SemanticExtractionGraphState) -> dict[str, Any]:
        candidates = [candidate.to_dict() for candidate in candidates_from_state(state)]
        return {
            "candidates": candidates,
            "retrieval_by_candidate": {},
            "decisions": [],
            "validation_errors": [],
            "fallback_fields": [],
            "completed_candidate_ids": [],
        }

    def retrieve_node(state: SemanticExtractionGraphState) -> dict[str, Any]:
        candidates = candidates_from_state(state)
        errors = list(state.get("validation_errors", []))
        return {
            "retrieval_by_candidate": retrieve_candidate_evidence(
                candidates,
                retriever,
                errors=errors,
            ),
            "validation_errors": errors,
        }

    def decide_node(state: SemanticExtractionGraphState) -> dict[str, Any]:
        candidates = candidates_from_state(state)
        retrieved = state.get("retrieval_by_candidate", {})
        decisions: list[dict[str, Any]] = []
        errors = list(state.get("validation_errors", []))
        for candidate in candidates:
            evidence = list(retrieved.get(candidate.candidate_id, []))
            retrieval_error = next(
                (
                    item.get("_retrieval_error")
                    for item in evidence
                    if isinstance(item, Mapping) and item.get("_retrieval_error")
                ),
                None,
            )
            if retrieval_error:
                decisions.append(
                    SemanticDecision.unresolved(
                        candidate_id=candidate.candidate_id,
                        task_type=candidate.task_type,
                        selection_reason=f"retrieval failed: {retrieval_error}",
                    ).to_dict()
                )
                continue
            try:
                raw = decider(candidate, evidence) if decider is not None else SemanticDecision.unresolved(
                    candidate_id=candidate.candidate_id,
                    task_type=candidate.task_type,
                    selection_reason="semantic decider is not configured",
                )
                decision = raw if isinstance(raw, SemanticDecision) else SemanticDecision.from_dict(raw)
                decisions.append(decision.to_dict())
            except Exception as exc:
                errors.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "stage": "decision",
                        "error": f"decision failed: {type(exc).__name__}",
                    }
                )
                decisions.append(
                    SemanticDecision.unresolved(
                        candidate_id=candidate.candidate_id,
                        task_type=candidate.task_type,
                        selection_reason=f"decision failed: {type(exc).__name__}",
                    ).to_dict()
                )
        return {"decisions": decisions, "validation_errors": errors}

    def validate_node(state: SemanticExtractionGraphState) -> dict[str, Any]:
        candidates = candidates_from_state(state)
        decisions, errors, fallback = validate_decisions(
            candidates,
            state.get("decisions", []),
            state.get("retrieval_by_candidate", {}),
        )
        return {
            "decisions": [decision.to_dict() for decision in decisions],
            "validation_errors": list(state.get("validation_errors", [])) + errors,
            "fallback_fields": fallback,
        }

    def finalize_node(state: SemanticExtractionGraphState) -> dict[str, Any]:
        candidates = candidates_from_state(state)
        decisions = [SemanticDecision.from_dict(item) for item in state.get("decisions", [])]
        prediction, field_states, fallback, completed = merge_decisions(
            state.get("baseline_prediction", {}), candidates, decisions
        )
        return {
            "merged_prediction": prediction,
            "field_states": field_states,
            "fallback_fields": sorted(set(state.get("fallback_fields", [])) | set(fallback)),
            "completed_candidate_ids": completed,
        }

    if StateGraph is None:
        raise ImportError("build_graph requires the optional 'langgraph' dependency")
    builder = StateGraph(SemanticExtractionGraphState)
    builder.add_node("load_candidates", load_candidates)
    builder.add_node("retrieve_candidate_evidence", retrieve_node)
    builder.add_node("llm_semantic_decision", decide_node)
    builder.add_node("validate_decision", validate_node)
    builder.add_node("finalize_decisions", finalize_node)
    builder.add_edge(START, "load_candidates")
    builder.add_edge("load_candidates", "retrieve_candidate_evidence")
    builder.add_edge("retrieve_candidate_evidence", "llm_semantic_decision")
    builder.add_edge("llm_semantic_decision", "validate_decision")
    builder.add_edge("validate_decision", "finalize_decisions")
    builder.add_edge("finalize_decisions", END)
    return builder.compile()


def _run_direct(
    state: SemanticExtractionGraphState,
    *,
    retriever: RetrievalCallable | None = None,
    decider: DecisionCallable | None = None,
) -> dict[str, Any]:
    """Execute the same five stages without the optional LangGraph runtime."""

    candidates = candidates_from_state(state)
    errors: list[dict[str, Any]] = []
    retrieved = retrieve_candidate_evidence(candidates, retriever, errors=errors)
    raw_decisions: list[dict[str, Any]] = []
    for candidate in candidates:
        evidence = list(retrieved.get(candidate.candidate_id, []))
        retrieval_error = next(
            (
                item.get("_retrieval_error")
                for item in evidence
                if isinstance(item, Mapping) and item.get("_retrieval_error")
            ),
            None,
        )
        if retrieval_error:
            raw_decisions.append(
                SemanticDecision.unresolved(
                    candidate_id=candidate.candidate_id,
                    task_type=candidate.task_type,
                    selection_reason=f"retrieval failed: {retrieval_error}",
                ).to_dict()
            )
            continue
        try:
            raw = (
                decider(candidate, evidence)
                if decider is not None
                else SemanticDecision.unresolved(
                    candidate_id=candidate.candidate_id,
                    task_type=candidate.task_type,
                    selection_reason="semantic decider is not configured",
                )
            )
            decision = raw if isinstance(raw, SemanticDecision) else SemanticDecision.from_dict(raw)
            raw_decisions.append(decision.to_dict())
        except Exception as exc:
            errors.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "stage": "decision",
                    "error": f"decision failed: {type(exc).__name__}",
                }
            )
            raw_decisions.append(
                SemanticDecision.unresolved(
                    candidate_id=candidate.candidate_id,
                    task_type=candidate.task_type,
                    selection_reason=f"decision failed: {type(exc).__name__}",
                ).to_dict()
            )

    decisions, validation_errors, fallback = validate_decisions(
        candidates, raw_decisions, retrieved
    )
    prediction, field_states, merge_fallback, completed = merge_decisions(
        state.get("baseline_prediction", {}), candidates, decisions
    )
    return {
        **dict(state),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "retrieval_by_candidate": retrieved,
        "decisions": [decision.to_dict() for decision in decisions],
        "validation_errors": errors + validation_errors,
        "fallback_fields": sorted(set(fallback) | set(merge_fallback)),
        "merged_prediction": prediction,
        "field_states": field_states,
        "completed_candidate_ids": completed,
    }


def run_graph(
    state: SemanticExtractionGraphState,
    *,
    retriever: RetrievalCallable | None = None,
    decider: DecisionCallable | None = None,
) -> dict[str, Any]:
    if StateGraph is None:
        return _run_direct(state, retriever=retriever, decider=decider)
    return dict(build_graph(retriever=retriever, decider=decider).invoke(state))
