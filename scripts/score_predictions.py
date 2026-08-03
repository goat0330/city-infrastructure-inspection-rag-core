#!/usr/bin/env python3
"""CLI for deterministic JSON/JSONL prediction scoring."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.evaluation import load_records, load_weights, score_dataset  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path, help="gold JSON or JSONL path")
    parser.add_argument("--predictions", required=True, type=Path, help="prediction JSON or JSONL path")
    parser.add_argument("--output", type=Path, help="output JSON/JSONL path; stdout when omitted")
    parser.add_argument("--weights", type=Path, help="official score-weights JSON path")
    parser.add_argument(
        "--output-format",
        choices=("json", "jsonl"),
        default="json",
        help="JSON object or JSONL records followed by one aggregate object",
    )
    return parser


def _encode(result: dict[str, object], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    lines = [
        json.dumps({"type": "record", **record}, ensure_ascii=False, separators=(",", ":"))
        for record in result["records"]  # type: ignore[index]
    ]
    aggregate = {key: value for key, value in result.items() if key != "records"}
    lines.append(json.dumps({"type": "aggregate", **aggregate}, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        gold = load_records(args.gold)
        predictions = load_records(args.predictions)
        weights = load_weights(args.weights)
        result = score_dataset(gold, predictions, weights)
        encoded = _encode(result, args.output_format)
        if args.output is None:
            sys.stdout.write(encoded)
        else:
            args.output.write_text(encoded, encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        _parser().error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
