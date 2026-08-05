from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from src.contracts.semantic_extraction import ExtractionCandidate


def retrieve_candidate_evidence(
    candidates: Sequence[ExtractionCandidate],
    retriever: Callable[[ExtractionCandidate], Sequence[Mapping[str, Any]]] | None,
    *,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Retrieve JSON-compatible evidence independently for each candidate."""

    results: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        try:
            raw_items = retriever(candidate) if retriever is not None else ()
        except Exception as exc:
            if errors is not None:
                errors.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "stage": "retrieval",
                        "error": f"retrieval failed: {type(exc).__name__}",
                    }
                )
            results[candidate.candidate_id] = [
                {"_retrieval_error": type(exc).__name__}
            ]
            continue
        items: list[dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, Mapping):
                items.append({str(key): value for key, value in item.items()})
            else:
                items.append({"evidence_id": str(item), "text": str(item)})
        results[candidate.candidate_id] = items
    return results
