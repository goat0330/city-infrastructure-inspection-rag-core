#!/usr/bin/env python3
"""Run the controlled semantic prediction sidecar, offline or with live models.

This command is deliberately separate from ``predict-batch``.  The live path
only handles bounded candidates and always merges through the frozen contract.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from collections.abc import Mapping
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.extraction.semantic_merge import merge_semantic_predictions  # noqa: E402
from src.contracts.semantic_extraction import ExtractionCandidate, SemanticDecision  # noqa: E402
from src.llm.client import OpenAIModelClient  # noqa: E402
from src.rag import LightRagIndex  # noqa: E402


def _load_json(path: Path, default: Any) -> Any:
    if not path:
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _load_decisions(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    value = _load_json(path, [])
    if isinstance(value, Mapping):
        value = value.get("decisions", [])
    if not isinstance(value, list):
        raise ValueError("decisions must be a JSON array or an object with decisions")
    return [dict(item) for item in value if isinstance(item, dict)]


def _live_prompt(candidate: ExtractionCandidate, evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    task_rules = {
        "defect_row_validation": "只判断是否为真实病害行、病害部位、病害类型和是否重复；不得改写原文描述、数量、尺寸、图号或日期。",
        "recommendation_category": "只在立即处置、尽快维修、预防性养护中选择；不得改写建议原文、部位或数量。",
        "conclusion_evidence_selection": "只从当前报告证据中选择总体结论依据，不补造事实。",
        "risk_evidence_selection": "只从当前报告证据中选择风险依据，安全影响必须保守。",
    }[candidate.task_type]
    payload = {
        "candidate": candidate.to_dict(),
        "retrieved_evidence": evidence,
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
            "content": "你是城市基础设施定检报告语义判别器。只输出一个JSON对象，不输出Markdown，不重写结构化报告。",
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _run_live_decisions(
    candidates: list[ExtractionCandidate],
    *,
    index_dir: Path,
    split: str,
    timeout: float = 120,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    client = OpenAIModelClient(timeout=timeout, retry_delay=0.2)
    index = LightRagIndex.load(index_dir, client=client)
    decisions: list[dict[str, Any]] = []
    retrieval: dict[str, list[dict[str, Any]]] = {}
    calls: list[dict[str, Any]] = []
    for candidate in candidates:
        query = " ".join((candidate.task_type, candidate.source_text, candidate.context_before, candidate.context_after)).strip()
        facility_type = candidate.facility_context.get("facility_type")
        try:
            evidence = index.retrieve(
                query,
                sample_id=candidate.sample_id,
                split=split,
                top_embedding=30,
                top_rerank=8,
                top_k=6,
                source_quota=True,
                facility_type=str(facility_type) if facility_type else None,
            )
        except Exception as exc:
            retrieval[candidate.candidate_id] = []
            decisions.append(
                SemanticDecision.unresolved(
                    candidate_id=candidate.candidate_id,
                    task_type=candidate.task_type,
                    selection_reason=f"retrieval failed: {type(exc).__name__}",
                ).to_dict()
            )
            calls.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "phase": "retrieval",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                }
            )
            continue
        retrieval[candidate.candidate_id] = evidence
        raw: dict[str, Any] | None = None
        try:
            result = client.chat_json(_live_prompt(candidate, evidence), max_tokens=1200)
            raw = dict(result.value)
            raw.setdefault("candidate_id", candidate.candidate_id)
            raw.setdefault("task_type", candidate.task_type)
            raw = _normalise_live_response(raw, candidate)
            decisions.append(SemanticDecision.from_dict(raw).to_dict())
            calls.append({
                "candidate_id": candidate.candidate_id,
                "model": result.model,
                "duration_ms": result.duration_ms,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
                "attempts": result.attempts,
            })
        except Exception as exc:
            decisions.append(
                SemanticDecision.unresolved(
                    candidate_id=candidate.candidate_id,
                    task_type=candidate.task_type,
                    selection_reason=f"live decision failed: {type(exc).__name__}",
                ).to_dict()
            )
            calls.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "error": str(exc)[:300],
                    "error_type": type(exc).__name__,
                    "raw_response": raw,
                }
            )
    return decisions, retrieval, calls


def _write_jsonl(path: Path, records: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _normalise_live_response(raw: dict[str, Any], candidate: ExtractionCandidate) -> dict[str, Any]:
    """Project the observed model envelope onto the frozen evidence contract."""

    if candidate.task_type == "recommendation_category":
        result = raw.get("result")
        if not isinstance(result, Mapping):
            raw["result"] = {"category": str(result or raw.get("category") or "")}
        return raw

    if candidate.task_type not in {"conclusion_evidence_selection", "risk_evidence_selection"}:
        return raw
    result = raw.get("result")
    result = result if isinstance(result, Mapping) else {}
    selected_ids = result.get("selected_evidence_ids") or raw.get("selected_evidence_ids") or raw.get("evidence_ids")
    selected_text = result.get("selected_text") or raw.get("selected_text")
    if not selected_text:
        field = "overall_conclusion" if candidate.task_type == "conclusion_evidence_selection" else "risk_points"
        selected_text = result.get(field) or candidate.source_text
    raw["result"] = {
        "selected_evidence_ids": list(selected_ids or []),
        "selected_text": str(selected_text or ""),
    }
    return raw


def run_semantic_pipeline(
    baseline_path: str | Path,
    candidates_path: str | Path,
    output_path: str | Path,
    *,
    decisions_path: str | Path | None = None,
    retrieval_path: str | Path | None = None,
    semantic_enabled: bool = True,
    live: bool = False,
    index_dir: str | Path | None = None,
    split: str = "holdout",
    timeout: float = 120,
) -> dict[str, Any]:
    baseline = _load_json(Path(baseline_path), {})
    candidates = _load_json(Path(candidates_path), [])
    if not isinstance(baseline, dict) or not isinstance(candidates, list):
        raise ValueError("baseline must be an object and candidates must be an array")
    retrieval = _load_json(Path(retrieval_path), {}) if retrieval_path else {}
    if not isinstance(retrieval, dict):
        raise ValueError("retrieval must be an object keyed by candidate_id")
    if live:
        if index_dir is None:
            raise ValueError("--live requires --index-dir")
        candidate_models = [ExtractionCandidate.from_dict(item) for item in candidates if isinstance(item, dict)]
        decisions, retrieval, model_calls = _run_live_decisions(
            candidate_models, index_dir=Path(index_dir), split=split, timeout=timeout
        )
    else:
        decisions = _load_decisions(Path(decisions_path) if decisions_path else None)
        model_calls = []
    prediction, trace = merge_semantic_predictions(
        baseline,
        [dict(item) for item in candidates if isinstance(item, dict)],
        decisions,
        retrieval,
        semantic_enabled=semantic_enabled,
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(prediction, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    trace_path = output.with_name(output.stem + ".trace.json")
    decisions_output = output.with_name(output.stem + ".decisions.jsonl")
    retrieval_output = output.with_name(output.stem + ".retrieval.json")
    if live:
        _write_jsonl(decisions_output, decisions)
        retrieval_output.write_text(json.dumps(retrieval, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        trace["live"] = True
        trace["model_calls"] = model_calls
        trace["decisions_output"] = str(decisions_output)
        trace["retrieval_output"] = str(retrieval_output)
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"prediction": str(output), "trace": str(trace_path), **trace}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--retrieval", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic-disabled", action="store_true")
    parser.add_argument("--live", action="store_true", help="call the configured chat, embedding, and reranker models")
    parser.add_argument("--index-dir", type=Path, help="fit-only LightRagIndex directory for --live")
    parser.add_argument("--split", default="holdout")
    parser.add_argument("--timeout", type=float, default=120, help="per model request timeout in seconds")
    args = parser.parse_args(argv)
    result = run_semantic_pipeline(
        args.baseline,
        args.candidates,
        args.output,
        decisions_path=args.decisions,
        retrieval_path=args.retrieval,
        semantic_enabled=not args.semantic_disabled,
        live=args.live,
        index_dir=args.index_dir,
        split=args.split,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
