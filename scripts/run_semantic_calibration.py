#!/usr/bin/env python3
"""Run the bounded live semantic extraction calibration slots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.prepare_semantic_calibration import build_calibration, write_outputs  # noqa: E402
from scripts.run_semantic_pipeline import run_semantic_pipeline  # noqa: E402

BASELINE_PATH = ROOT / "runs/b2-night/baseline/aligned-predictions.jsonl"
CANDIDATES_PATH = ROOT / "runs/round2-semantic/86-gold-candidate-inventory/candidates.jsonl"
DEFAULT_OUTPUT = ROOT / "runs/round2-semantic/semantic-calibration-8"
CALIBRATION_INDEXES = {
    "2012年-杨公桥A叉口人行通道": ROOT / "runs/narrative-k46-20260805/calibration-5/indexes/01",
    "2012年-桂花新村大桥": ROOT / "runs/narrative-k46-20260805/calibration-5/indexes/02",
    "2012年-梨子湾大桥": ROOT / "runs/narrative-k46-20260805/calibration-5/indexes/03",
    "2012年-凤中主线桥": ROOT / "runs/narrative-k46-20260805/calibration-5/indexes/04",
    "2013年-12-035杨公桥立交EC匝道桥": ROOT / "runs/narrative-k46-20260805/calibration-5/indexes/05",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _index_path(sample_id: str) -> Path:
    return CALIBRATION_INDEXES.get(sample_id, ROOT / "runs/round2-semantic/indexes" / sample_id)


def run_calibration(output_dir: Path = DEFAULT_OUTPUT, timeout: float = 120) -> dict[str, Any]:
    manifest = build_calibration()
    write_outputs(manifest, output_dir)
    baselines = {str(row["sample_id"]): row for row in _load_jsonl(BASELINE_PATH)}
    candidates_by_sample: dict[str, list[dict[str, Any]]] = {}
    for row in _load_jsonl(CANDIDATES_PATH):
        candidates_by_sample.setdefault(str(row.get("sample_id", "")), []).append(row)

    results_dir = output_dir / "results"
    unique_results: dict[str, dict[str, Any]] = {}
    slot_results: list[dict[str, Any]] = []
    for slot in manifest["slots"]:
        sample_id = str(slot["sample_id"])
        if sample_id not in unique_results:
            if slot["index"]["status"] != "available":
                raise RuntimeError(f"missing index for calibration sample: {sample_id}")
            sample_dir = results_dir / f"{slot['slot']:02d}-{sample_id}"
            sample_dir.mkdir(parents=True, exist_ok=True)
            input_dir = output_dir / "inputs" / f"{slot['slot']:02d}-{sample_id}"
            input_dir.mkdir(parents=True, exist_ok=True)
            baseline_input = input_dir / "baseline.json"
            candidates_input = input_dir / "candidates.json"
            baseline_input.write_text(
                json.dumps(baselines[sample_id], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            candidates_input.write_text(
                json.dumps(candidates_by_sample[sample_id], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            output_path = sample_dir / "enhanced_prediction.json"
            unique_results[sample_id] = run_semantic_pipeline(
                baseline_input,
                candidates_input,
                output_path,
                live=True,
                index_dir=_index_path(sample_id),
                split=str(slot["split"]),
                timeout=timeout,
            )
        run_result = unique_results[sample_id]
        slot_results.append(
            {
                "slot": slot["slot"],
                "role": slot["role"],
                "sample_id": sample_id,
                "deduplicated_to_sample_run": sample_id,
                "prediction": run_result["prediction"],
                "trace": run_result["trace"],
            }
        )

    summary = {
        "schema_version": "semantic-calibration-live-8-v1",
        "slot_count": len(slot_results),
        "unique_sample_count": len(unique_results),
        "deduplicated_sample_ids": [sample_id for sample_id, count in {
            sample_id: sum(item["sample_id"] == sample_id for item in slot_results)
            for sample_id in unique_results
        }.items() if count > 1],
        "slot_results": slot_results,
        "unique_results": unique_results,
    }
    (output_dir / "experiment-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    summary = run_calibration(args.output_dir, args.timeout)
    print(json.dumps({
        "slot_count": summary["slot_count"],
        "unique_sample_count": summary["unique_sample_count"],
        "deduplicated_sample_ids": summary["deduplicated_sample_ids"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
