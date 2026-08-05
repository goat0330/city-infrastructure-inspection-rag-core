#!/usr/bin/env python3
"""Revalidate the current structured baseline against D-group Narrative RAG.

The runner calls only the evidence-grounded D path.  The deterministic
prediction is always retained as group A and as the fallback for any failed
sample.  No credential or prompt text is written to artifacts.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGET_FIELDS = ("detailed_conclusion", "causes", "treatments", "safety_impact")
REQUIRED_ENV = (
    "IAIC_API_BASE",
    "IAIC_API_KEY",
    "IAIC_CHAT_MODEL",
    "IAIC_EMBED_MODEL",
    "IAIC_RERANK_MODEL",
)


def _load_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return [dict(item) for item in value]
    if isinstance(value, Mapping) and isinstance(value.get("records"), list):
        return [dict(item) for item in value["records"]]
    raise ValueError(f"records not found in {path}")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _index_dir(root: Path, sample_id: str) -> Path:
    for candidate in (root / sample_id, root / "reports" / sample_id):
        if (candidate / "metadata.jsonl").is_file() and (candidate / "vectors.npy").is_file():
            return candidate
    return root / sample_id


def _locked_differences(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    missing = object()
    result: list[str] = []
    for key in sorted(set(left) | set(right)):
        if key in TARGET_FIELDS:
            continue
        if left.get(key, missing) != right.get(key, missing):
            result.append(str(key))
    return result


def _split(value: object) -> str:
    return "holdout" if str(value).lower() in {"validation", "holdout", "val"} else "fit"


def _preflight(samples: list[dict[str, Any]], reports_root: Path, indexes_root: Path) -> dict[str, Any]:
    missing_env = [name for name in REQUIRED_ENV if not os.getenv(name)]
    rows: list[dict[str, Any]] = []
    for item in samples:
        docx = reports_root / str(item["converted_docx_relative_path"])
        index = _index_dir(indexes_root, str(item["sample_id"]))
        rows.append(
            {
                "sample_id": item["sample_id"],
                "docx": str(docx),
                "docx_exists": docx.is_file(),
                "index_dir": str(index),
                "index_exists": (index / "metadata.jsonl").is_file() and (index / "vectors.npy").is_file(),
            }
        )
    return {
        "status": "ready" if not missing_env and all(row["docx_exists"] and row["index_exists"] for row in rows) else "blocked",
        "missing_environment": missing_env,
        "samples": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--indexes-root", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-jsonl", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    sample_manifest = json.loads(args.samples.read_text(encoding="utf-8"))
    samples = [dict(item) for item in sample_manifest.get("samples", [])]
    preflight = _preflight(samples, args.reports_root, args.indexes_root)
    _write(args.output_dir / "preflight.json", preflight)
    if args.preflight_only or preflight["status"] != "ready":
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return 0 if args.preflight_only else 2

    from scripts.run_narrative_enhancement import (  # noqa: E402
        StaticRetriever,
        TrackingClient,
        _merge_retrieval_hits,
        _report_facts,
        _retrieve_task_hits,
        _select_context_facts,
        _task_queries,
    )
    from src.agent.narrative import run_narrative_enhancement  # noqa: E402
    from src.evaluation.scorer import score_dataset  # noqa: E402
    from src.extraction import extract_report  # noqa: E402
    from src.llm.client import OpenAIModelClient  # noqa: E402
    from src.rag import LightRagIndex  # noqa: E402

    gold_by_id = {record["sample_id"]: record for record in _load_records(args.gold)}
    baseline_by_id = (
        {record["sample_id"]: record for record in _load_records(args.baseline_jsonl)}
        if args.baseline_jsonl
        else {}
    )
    client = TrackingClient(OpenAIModelClient(timeout=120, retry_delay=0.2))
    a_predictions: list[dict[str, Any]] = []
    d_predictions: list[dict[str, Any]] = []
    gold_records: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for item in samples:
        sample_id = str(item["sample_id"])
        sample_dir = args.output_dir / sample_id
        status_path = sample_dir / "run_status.json"
        if args.resume and status_path.is_file():
            old = json.loads(status_path.read_text(encoding="utf-8"))
            if old.get("status") in {"success", "fallback"}:
                continue
        docx = args.reports_root / str(item["converted_docx_relative_path"])
        try:
            if sample_id in baseline_by_id:
                baseline = deepcopy(baseline_by_id[sample_id])
                extraction = extract_report(docx, source_file=str(item["converted_docx_relative_path"]))
                baseline["facility_context"] = extraction.facility_context.to_dict()
                baseline["field_states"] = dict(extraction.field_states)
            else:
                extraction = extract_report(docx, source_file=str(item["converted_docx_relative_path"]))
                baseline = extraction.prediction.to_dict()
                baseline["facility_context"] = extraction.facility_context.to_dict()
                baseline["field_states"] = dict(extraction.field_states)

            facts = _report_facts(docx, str(baseline.get("source_file") or docx.name))
            context_facts = _select_context_facts(facts, baseline)
            facility_context = baseline.get("facility_context", {})
            facility_type = facility_context.get("facility_type") if isinstance(facility_context, Mapping) else None
            task_queries = _task_queries(baseline, facility_context, facts)
            index = LightRagIndex.load(_index_dir(args.indexes_root, sample_id), client=client)
            task_hits, task_errors = _retrieve_task_hits(
                index,
                task_queries,
                sample_id=sample_id,
                split=_split(item.get("split")),
                facility_type=str(facility_type or ""),
            )
            retrieval_hits = _merge_retrieval_hits(task_hits)
            narrative = run_narrative_enhancement(
                baseline,
                sample_id,
                str(baseline.get("source_file") or docx.name),
                context_facts,
                client,
                retriever=StaticRetriever(retrieval_hits),
                split=_split(item.get("split")),
                facility_context=facility_context,
                field_states=baseline.get("field_states", {}),
            )
            enhanced = deepcopy(narrative.get("enhanced_prediction", baseline))
            locked = _locked_differences(enhanced, baseline)
            used_fallback = bool(narrative.get("used_fallback"))
            status = "fallback" if used_fallback else "success"
            if locked:
                status = "failed"
            _write(sample_dir / "baseline_prediction.json", baseline)
            _write(sample_dir / "enhanced_prediction.json", enhanced)
            _write(sample_dir / "retrieval_trace.json", {"task_queries": task_queries, "task_hits": task_hits, "task_errors": task_errors, "hits": retrieval_hits})
            _write(sample_dir / "field_results.json", {"used_fallback": used_fallback, "validation_errors": narrative.get("validation_errors", []), "locked_differences": locked})
            _write(status_path, {"status": status, "sample_id": sample_id})

            gold = gold_by_id[sample_id]
            a_score = score_dataset([gold], [baseline])
            d_score = score_dataset([gold], [enhanced])
            results.append(
                {
                    "sample_id": sample_id,
                    "group": item.get("group"),
                    "status": status,
                    "used_fallback": used_fallback,
                    "locked_differences": locked,
                    "a_total": a_score["micro_total_score"],
                    "d_total": d_score["micro_total_score"],
                    "delta": round(d_score["micro_total_score"] - a_score["micro_total_score"], 6),
                    "a_text_25": round(sum(a_score["sections"][field]["score"] for field in ("detailed_conclusion", "causes", "safety_impact")), 6),
                    "d_text_25": round(sum(d_score["sections"][field]["score"] for field in ("detailed_conclusion", "causes", "safety_impact")), 6),
                }
            )
            a_predictions.append(baseline)
            d_predictions.append(enhanced)
            gold_records.append(gold)
        except Exception as error:
            _write(status_path, {"status": "failed", "sample_id": sample_id, "error": " ".join(str(error).split())[:300]})
            results.append({"sample_id": sample_id, "group": item.get("group"), "status": "failed", "error": " ".join(str(error).split())[:300]})

    summary: dict[str, Any] = {
        "schema_version": "round2-narrative-revalidation-v1",
        "sample_count": len(samples),
        "completed_count": len(gold_records),
        "results": results,
    }
    if gold_records:
        score_a = score_dataset(gold_records, a_predictions)
        score_d = score_dataset(gold_records, d_predictions)
        summary["aggregate"] = {
            "A": {"micro_total": score_a["micro_total_score"], "macro_total": score_a["macro_total_score"], "sections": {key: value["score"] for key, value in score_a["sections"].items()}},
            "D": {"micro_total": score_d["micro_total_score"], "macro_total": score_d["macro_total_score"], "sections": {key: value["score"] for key, value in score_d["sections"].items()}},
            "delta_micro_total": round(score_d["micro_total_score"] - score_a["micro_total_score"], 6),
            "delta_text_25": round(sum(score_d["sections"][field]["score"] - score_a["sections"][field]["score"] for field in ("detailed_conclusion", "causes", "safety_impact")), 6),
        }
    _write(args.output_dir / "revalidation-summary.json", summary)
    with (args.output_dir / "sample-results.jsonl").open("w", encoding="utf-8") as stream:
        for result in results:
            stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(item.get("status") in {"success", "fallback"} for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
