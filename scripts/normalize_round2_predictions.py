#!/usr/bin/env python3
"""Normalize an existing prediction JSONL and optionally rescore it."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.scorer import score_dataset  # noqa: E402
from src.extraction.output_normalizer import (  # noqa: E402
    normalize_defect_description,
    normalize_defect_type,
    normalize_report_date,
    normalize_recommendation_text,
)


def _records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return [dict(item) for item in value]
    if isinstance(value, Mapping):
        items = value.get("records", value.get("items", []))
        if isinstance(items, list):
            return [dict(item) for item in items]
    raise ValueError(f"cannot load records from {path}")


def _is_passage(record: Mapping[str, Any]) -> bool:
    summary = record.get("summary", {})
    name = summary.get("bridge_name", "") if isinstance(summary, Mapping) else ""
    identity = f"{record.get('sample_id', '')} {name}"
    return any(marker in identity for marker in ("人行通道", "人行地通道", "地下通道", "地通道"))


def normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(record))
    summary = result.get("summary")
    if isinstance(summary, dict):
        summary["report_date"] = normalize_report_date(summary.get("report_date", ""))
    preserve_refs = _is_passage(result)
    for defect in result.get("defects", []):
        if not isinstance(defect, dict):
            continue
        defect["defect_type"] = normalize_defect_type(defect.get("defect_type", ""))
        defect["description"] = normalize_defect_description(
            defect.get("description", ""),
            preserve_figure_refs=preserve_refs,
        )
    for recommendation in result.get("recommendations", []):
        if isinstance(recommendation, dict):
            recommendation["content"] = normalize_recommendation_text(
                recommendation.get("content", "")
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    before = _records(args.input)
    after = [normalize_record(record) for record in before]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in after),
        encoding="utf-8",
    )
    report: dict[str, Any] = {
        "status": "succeeded",
        "record_count": len(after),
        "input": str(args.input),
        "output": str(args.output),
    }
    if args.gold:
        gold = _records(args.gold)
        before_score = score_dataset(gold, before)
        after_score = score_dataset(gold, after)
        report["score"] = {
            "before_micro_total": before_score["micro_total_score"],
            "after_micro_total": after_score["micro_total_score"],
            "delta_micro_total": round(
                after_score["micro_total_score"] - before_score["micro_total_score"], 6
            ),
            "before_macro_total": before_score["macro_total_score"],
            "after_macro_total": after_score["macro_total_score"],
            "delta_macro_total": round(
                after_score["macro_total_score"] - before_score["macro_total_score"], 6
            ),
            "sections_before": {
                key: value["score"] for key, value in before_score["sections"].items()
            },
            "sections_after": {
                key: value["score"] for key, value in after_score["sections"].items()
            },
        }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
