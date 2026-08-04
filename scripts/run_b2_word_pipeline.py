#!/usr/bin/env python3
"""Run the Word-first prediction, provenance alignment, and B2 benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.extraction import predict_batch  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path, help="converted DOCX directory")
    parser.add_argument("--gold", required=True, type=Path, help="Gold JSON/JSONL path")
    parser.add_argument("--manifest", required=True, type=Path, help="B2 eval-manifest.json path")
    parser.add_argument("--output-dir", required=True, type=Path, help="run artifact directory")
    parser.add_argument("--weights", type=Path, help="optional score weights JSON")
    parser.add_argument("--commit", default="", help="commit recorded in benchmark artifacts")
    parser.add_argument("--config", default="b2-word-pipeline", help="benchmark config label")
    parser.add_argument("--notes", default="", help="benchmark note")
    return parser


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_predictions = output_dir / "raw-predictions.jsonl"
    prediction_report = output_dir / "prediction-report.json"
    benchmark_dir = output_dir / "benchmark"

    try:
        prediction_result = predict_batch(
            args.input_dir,
            raw_predictions,
            report_path=prediction_report,
        )
    except (OSError, TypeError, ValueError) as error:
        _parser().error(str(error))
        return 2
    if prediction_result.get("failed_count"):
        _write_json(output_dir / "pipeline-result.json", prediction_result)
        return 1

    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "scripts" / "run_b2_benchmark.py"),
        "--gold",
        str(args.gold),
        "--predictions",
        str(raw_predictions),
        "--manifest",
        str(args.manifest),
        "--output-dir",
        str(benchmark_dir),
        "--commit",
        args.commit,
        "--config",
        args.config,
        "--notes",
        args.notes,
    ]
    if args.weights is not None:
        command.extend(("--weights", str(args.weights)))
    benchmark = subprocess.run(command, cwd=REPOSITORY_ROOT, check=False)
    result = {
        "status": "succeeded" if benchmark.returncode == 0 else "failed",
        "prediction_report": str(prediction_report),
        "raw_predictions": str(raw_predictions),
        "benchmark_dir": str(benchmark_dir),
        "benchmark_exit_code": benchmark.returncode,
    }
    _write_json(output_dir / "pipeline-result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return benchmark.returncode


if __name__ == "__main__":
    raise SystemExit(main())
