"""Controlled merge adapter for the optional semantic experiment path.

The Word-first prediction is authoritative.  This module only supplies the
small runtime adapters needed by the optional candidate -> retrieval -> live
decision path and projects its result back onto the explicitly allowed fields.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable, Mapping, Sequence
import json
from typing import Any

from src.contracts.semantic_extraction import ExtractionCandidate, SemanticDecision
from src.semantic_extraction import run_graph


def _live_prompt(
    candidate: ExtractionCandidate,
    evidence: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    task_rules = {
        "defect_row_validation": (
            "只判断是否为真实病害行、病害部位、病害类型和是否重复；"
            "不得改写原文描述、数量、尺寸、图号或日期。"
        ),
        "recommendation_category": (
            "只在立即处置、尽快维修、预防性养护中选择；"
            "不得改写建议原文、部位或数量。"
        ),
        "conclusion_evidence_selection": "只从当前报告证据中选择总体结论依据，不补造事实。",
        "risk_evidence_selection": "只从当前报告证据中选择风险依据，安全影响必须保守。",
    }[candidate.task_type]
    payload = {
        "candidate": candidate.to_dict(),
        "retrieved_evidence": [dict(item) for item in evidence],
        "task_rule": task_rules,
        "output_contract": {
            "candidate_id": candidate.candidate_id,
            "task_type": candidate.task_type,
            "decision": "resolved or unresolved",
            "evidence_ids": "引用的候选或检索证据ID数组",
            "confidence": "0到1之间的数字",
            "selection_reason": "简短依据",
            "result": "严格使用该task_type对应的合同字段",
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "你是城市基础设施定检报告语义判别器。"
                "只输出一个JSON对象，不输出Markdown，不重写结构化报告。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _normalise_live_response(
    value: Mapping[str, Any],
    candidate: ExtractionCandidate,
) -> dict[str, Any]:
    """Accept the small envelope variants returned by Qwen-compatible clients."""

    raw = dict(value)
    raw.setdefault("candidate_id", candidate.candidate_id)
    raw.setdefault("task_type", candidate.task_type)
    result = raw.get("result")
    result = dict(result) if isinstance(result, Mapping) else {}
    if candidate.task_type == "recommendation_category":
        result["category"] = result.get("category", raw.get("category", ""))
    elif candidate.task_type in {
        "conclusion_evidence_selection",
        "risk_evidence_selection",
    }:
        selected_ids = result.get("selected_evidence_ids")
        if selected_ids is None:
            selected_ids = raw.get("selected_evidence_ids", raw.get("evidence_ids", ()))
        selected_text = result.get("selected_text", raw.get("selected_text", ""))
        result = {
            "selected_evidence_ids": selected_ids,
            "selected_text": selected_text,
        }
    raw["result"] = result
    return raw


def _live_decider(client: Any) -> Callable[..., Mapping[str, Any]]:
    def decide(
        candidate: ExtractionCandidate,
        evidence: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if not evidence:
            raise ValueError("no retrieval evidence")
        response = client.chat_json(_live_prompt(candidate, evidence), max_tokens=1200)
        value = getattr(response, "value", response)
        if not isinstance(value, Mapping):
            raise TypeError("live decision must be a JSON object")
        return _normalise_live_response(value, candidate)

    return decide


def _index_retriever(index: Any, split: str) -> Callable[..., Sequence[Mapping[str, Any]]]:
    def retrieve(candidate: ExtractionCandidate) -> Sequence[Mapping[str, Any]]:
        facility_type = candidate.facility_context.get("facility_type")
        query = " ".join(
            part
            for part in (
                candidate.task_type,
                candidate.source_text,
                candidate.context_before,
                candidate.context_after,
            )
            if str(part).strip()
        )
        return index.retrieve(
            query,
            sample_id=candidate.sample_id,
            split=split,
            top_embedding=30,
            top_rerank=8,
            top_k=6,
            source_quota=True,
            facility_type=str(facility_type) if facility_type else None,
        )

    return retrieve


def _decision_map(
    decisions: Sequence[Mapping[str, Any]],
) -> Callable[..., Mapping[str, Any]]:
    by_id = {
        str(item.get("candidate_id", "")): dict(item)
        for item in decisions
        if isinstance(item, Mapping)
    }

    def decide(candidate: ExtractionCandidate, _evidence: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        return by_id.get(
            candidate.candidate_id,
            SemanticDecision.unresolved(
                candidate_id=candidate.candidate_id,
                task_type=candidate.task_type,
                selection_reason="semantic decision not supplied",
            ).to_dict(),
        )

    return decide


def _project_allowed_fields(
    original: Mapping[str, Any],
    merged: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep the original prediction shape and project only safe semantic fields."""

    safe = deepcopy(dict(original))

    original_summary = original.get("summary")
    merged_summary = merged.get("summary")
    if isinstance(original_summary, Mapping):
        summary = deepcopy(dict(original_summary))
        if isinstance(merged_summary, Mapping):
            for key in ("overall_conclusion", "risk_points"):
                if key in original_summary and key in merged_summary:
                    summary[key] = deepcopy(merged_summary[key])
        safe["summary"] = summary

    original_recommendations = original.get("recommendations")
    merged_recommendations = merged.get("recommendations")
    if isinstance(original_recommendations, Sequence) and not isinstance(
        original_recommendations,
        (str, bytes, bytearray),
    ):
        recommendations = deepcopy(original_recommendations)
        if isinstance(merged_recommendations, Sequence) and not isinstance(
            merged_recommendations,
            (str, bytes, bytearray),
        ):
            for index, item in enumerate(recommendations):
                if not isinstance(item, Mapping) or index >= len(merged_recommendations):
                    continue
                merged_item = merged_recommendations[index]
                if isinstance(merged_item, Mapping) and "category" in merged_item:
                    item["category"] = deepcopy(merged_item["category"])
        safe["recommendations"] = (
            tuple(recommendations)
            if isinstance(original_recommendations, tuple)
            else recommendations
        )

    return safe


def _fallback_reasons(result: Mapping[str, Any]) -> list[dict[str, str]]:
    def safe_reason(value: object) -> str:
        reason = " ".join(str(value or "").split())
        known_prefixes = (
            "retrieval failed:",
            "decision failed:",
            "validation failed:",
            "semantic decider is not configured",
            "semantic decision not supplied",
        )
        if not reason.startswith(known_prefixes):
            return "semantic decision unresolved"
        return (reason or "semantic decision unresolved")[:160]

    reasons: dict[str, dict[str, str]] = {}
    for raw in result.get("decisions", ()):
        if not isinstance(raw, Mapping) or raw.get("decision") != "unresolved":
            continue
        candidate_id = str(raw.get("candidate_id", ""))
        if not candidate_id:
            continue
        reasons[candidate_id] = {
            "candidate_id": candidate_id,
            "task_type": str(raw.get("task_type", "")),
            "reason": safe_reason(raw.get("selection_reason", "")),
        }
    for raw in result.get("validation_errors", ()):
        if not isinstance(raw, Mapping):
            continue
        candidate_id = str(raw.get("candidate_id", ""))
        if not candidate_id:
            continue
        reasons.setdefault(
            candidate_id,
            {
                "candidate_id": candidate_id,
                "task_type": str(raw.get("task_type", "")),
                "reason": safe_reason(raw.get("error", "")),
            },
        )
    return list(reasons.values())


def _failure_trace(
    candidates: Sequence[ExtractionCandidate],
    reason: str,
) -> dict[str, Any]:
    return {
        "semantic_enabled": True,
        "candidate_count": len(candidates),
        "field_states": {},
        "fallback_fields": sorted(
            {field for candidate in candidates for field in candidate.fallback_fields}
        ),
        "validation_errors": [{"stage": "semantic_graph", "error": reason}],
        "fallback_reasons": [
            {"candidate_id": candidate.candidate_id, "task_type": candidate.task_type, "reason": reason}
            for candidate in candidates
        ],
        "completed_candidate_ids": [],
        "used_fallback": bool(candidates),
    }


def merge_semantic_predictions(
    baseline: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]] = (),
    retrieval_by_candidate: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    *,
    semantic_enabled: bool = False,
    retriever: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
    decider: Callable[..., SemanticDecision | Mapping[str, Any]] | None = None,
    index: Any = None,
    client: Any = None,
    split: str = "holdout",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the semantic graph while keeping the structured baseline authoritative.

    ``semantic_enabled=False`` is intentionally the default and returns before
    candidate parsing or any runtime adapter can be called.  Live callers pass
    the existing ``LightRagIndex`` and ``OpenAIModelClient`` instances; tests
    can inject a retriever and decider without network access.
    """

    original = deepcopy(dict(baseline))
    if not semantic_enabled:
        return original, {
            "semantic_enabled": False,
            "candidate_count": 0,
            "field_states": {},
            "fallback_fields": [],
            "fallback_reasons": [],
            "validation_errors": [],
            "completed_candidate_ids": [],
            "used_fallback": False,
        }

    try:
        candidate_models = [ExtractionCandidate.from_dict(item) for item in candidates]
    except (TypeError, ValueError) as exc:
        return original, _failure_trace((), f"candidate contract failed: {type(exc).__name__}")

    if not candidate_models:
        return original, {
            "semantic_enabled": True,
            "candidate_count": 0,
            "field_states": {},
            "fallback_fields": [],
            "fallback_reasons": [],
            "validation_errors": [],
            "completed_candidate_ids": [],
            "used_fallback": False,
        }

    retrieval = {
        str(key): [dict(item) for item in values if isinstance(item, Mapping)]
        for key, values in (retrieval_by_candidate or {}).items()
    }
    if retriever is None:
        if index is not None:
            retriever = _index_retriever(index, split)
        else:
            retriever = lambda candidate: retrieval.get(candidate.candidate_id, ())

    if decider is None:
        if client is not None:
            decider = _live_decider(client)
        else:
            decider = _decision_map(decisions)

    try:
        result = run_graph(
            {
                "sample_id": str(baseline.get("sample_id", "")),
                "baseline_prediction": original,
                "candidates": [candidate.to_dict() for candidate in candidate_models],
                "retrieval_by_candidate": retrieval,
                "decisions": [dict(decision) for decision in decisions],
            },
            retriever=retriever,
            decider=decider,
        )
    except Exception as exc:
        return original, _failure_trace(
            candidate_models,
            f"semantic graph failed: {type(exc).__name__}",
        )

    merged = _project_allowed_fields(
        original,
        result.get("merged_prediction", original),
    )
    fallback = list(result.get("fallback_fields", []))
    validation_errors = list(result.get("validation_errors", []))
    fallback_reasons = _fallback_reasons(result)
    return merged, {
        "semantic_enabled": True,
        "candidate_count": len(candidate_models),
        "field_states": dict(result.get("field_states", {})),
        "fallback_fields": fallback,
        "fallback_reasons": fallback_reasons,
        "validation_errors": validation_errors,
        "completed_candidate_ids": list(result.get("completed_candidate_ids", [])),
        "used_fallback": bool(fallback or validation_errors or fallback_reasons),
    }


__all__ = ["merge_semantic_predictions"]
