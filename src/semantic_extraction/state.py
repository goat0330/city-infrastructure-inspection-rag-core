from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from src.contracts.semantic_extraction import (
    ExtractionCandidate,
    SemanticDecision,
    SemanticExtractionGraphState,
)


class CandidateRetriever(Protocol):
    def __call__(
        self, candidate: ExtractionCandidate
    ) -> Sequence[Mapping[str, Any]]: ...


class SemanticDecider(Protocol):
    def __call__(
        self,
        candidate: ExtractionCandidate,
        evidence: Sequence[Mapping[str, Any]],
    ) -> SemanticDecision | Mapping[str, Any]: ...


RetrievalCallable = Callable[
    [ExtractionCandidate], Sequence[Mapping[str, Any]]
]
DecisionCallable = Callable[
    [ExtractionCandidate, Sequence[Mapping[str, Any]]],
    SemanticDecision | Mapping[str, Any],
]
GraphState = SemanticExtractionGraphState


def candidates_from_state(state: Mapping[str, Any]) -> list[ExtractionCandidate]:
    return [ExtractionCandidate.from_dict(value) for value in state.get("candidates", [])]


def decisions_from_state(state: Mapping[str, Any]) -> list[SemanticDecision]:
    return [SemanticDecision.from_dict(value) for value in state.get("decisions", [])]
