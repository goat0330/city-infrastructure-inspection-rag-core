#!/usr/bin/env python3
"""Deterministic, privacy-safe B2 benchmark orchestration.

Thin orchestration only: it loads a fixed Gold set and a prediction JSONL,
aligns them through the official strict scorer, and produces the four nightly
artifacts plus per fit/holdout/stress summaries.  It does not change any score
weight, the official scorer, the prediction/gold schema, or the existing CLI
contracts of the diagnostics and leaderboard modules.

Outputs (in ``--output-dir``):
  - ``score.json``       official ``score_dataset`` output (source of truth)
  - ``diagnostics.json`` summary-only B2 diagnostics (privacy-safe)
  - ``summaries.json``   fit/holdout/stress aggregate views
  - ``errorbook.md``     summary-only B2 errorbook (no Gold text, no paths)
  - ``leaderboard.csv``  deterministic experiment leaderboard row

No OCR, RAG, agent, or LLM is used anywhere in this path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.evaluation import (  # noqa: E402
    AlignmentError,
    align_prediction_records,
    load_records,
    load_weights,
    score_dataset,
)
from src.evaluation.diagnostics import (  # noqa: E402
    diagnose_records,
    subset_summaries,
    write_diagnostics,
)
from src.errorbook.b2 import (  # noqa: E402
    LEADERBOARD_FIELDS,
    entry_from_diagnostics,
    write_leaderboard,
    write_b2_errorbook,
)

_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/|\\\\\?\\)")
_RAW_TEXT_KEY = "raw_text"
_ABS_PATH_IN_TEXT = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\[^\s,;]+\\[^\s,;]+)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path, help="fixed Gold JSON/JSONL path")
    parser.add_argument("--predictions", required=True, type=Path, help="prediction JSONL path")
    parser.add_argument("--manifest", required=True, type=Path, help="eval-manifest.json path")
    parser.add_argument("--output-dir", required=True, type=Path, help="directory for produced artifacts")
    parser.add_argument("--commit", default="", help="short git commit id recorded on errorbook/leaderboard")
    parser.add_argument("--config", default="run", help="experiment config label")
    parser.add_argument("--notes", default="", help="optional leaderboard note (paths are redacted)")
    parser.add_argument("--weights", type=Path, help="optional official score-weights JSON path")
    parser.add_argument("--skip-verify", action="store_true", help="do not run the output verification pass")
    return parser


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _manifest_meta(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = manifest.get("records", [])
    if not isinstance(records, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, Mapping):
            continue
        sample_id = str(item.get("sample_id", "")).strip()
        if not sample_id:
            continue
        tags = [str(tag).strip() for tag in item.get("stress_tags", []) if isinstance(tag, str) and tag.strip()]
        result[sample_id] = {"split": str(item.get("split", "fit")).strip() or "fit", "stress_tags": tags}
    return result


def _build_groups(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    metadata = _manifest_meta(manifest)
    splits: dict[str, list[str]] = {}
    stress: dict[str, list[str]] = {}
    for sample_id, meta in metadata.items():
        splits.setdefault(meta["split"], []).append(sample_id)
        for tag in meta["stress_tags"]:
            stress.setdefault(tag, []).append(sample_id)
    return metadata, splits, stress


def _scan_strings(value: Any, violations: list[str]) -> None:
    if isinstance(value, str):
        if _ABSOLUTE_PATH.match(value):
            violations.append("absolute-path")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == _RAW_TEXT_KEY:
                violations.append("raw_text-key")
            _scan_strings(item, violations)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _scan_strings(item, violations)


def _verify(
    output_dir: Path,
    *,
    diagnostics: Mapping[str, Any],
    splits: Mapping[str, Sequence[str]],
    stress: Mapping[str, Sequence[str]],
    expected_gold: int,
    expected_predictions: int,
) -> list[str]:
    failures: list[str] = []

    diagnostic_records = diagnostics.get("records")
    if not isinstance(diagnostic_records, list):
        failures.append("diagnostics missing records list")
        diagnostic_records = []

    aligned_count = len(diagnostic_records)
    if aligned_count != expected_gold:
        failures.append(f"aligned count {aligned_count} != gold count {expected_gold}")

    for label, ids in splits.items():
        actual = sum(1 for record in diagnostic_records if record.get("sample_id") in set(ids))
        if actual != len(ids):
            failures.append(f"split {label!r} count {actual} != manifest {len(ids)}")
    for label, ids in stress.items():
        actual = sum(1 for record in diagnostic_records if record.get("sample_id") in set(ids))
        if actual != len(ids):
            failures.append(f"stress {label!r} count {actual} != manifest {len(ids)}")

    missing = diagnostics.get("missing_sample_ids", [])
    extra = diagnostics.get("extra_sample_ids", [])
    if missing:
        failures.append(f"unmatched gold records: {len(missing)}")
    if extra:
        failures.append(f"extra prediction records: {len(extra)}")

    leaderboard_path = output_dir / "leaderboard.csv"
    if leaderboard_path.exists():
        with leaderboard_path.open("r", encoding="utf-8", newline="") as handle:
            import csv

            header = tuple(next(csv.reader(handle)))
        if header != LEADERBOARD_FIELDS:
            failures.append(f"leaderboard CSV header mismatch: {header}")

    for name, path in (
        ("diagnostics", output_dir / "diagnostics.json"),
        ("errorbook", output_dir / "errorbook.md"),
        ("summaries", output_dir / "summaries.json"),
    ):
        text = path.read_text(encoding="utf-8")
        if _ABS_PATH_IN_TEXT.search(text):
            failures.append(f"{name} contains an absolute path")
    for payload, name in (
        (diagnostics, "diagnostics"),
        (json.loads((output_dir / "summaries.json").read_text(encoding="utf-8")), "summaries"),
    ):
        violations: list[str] = []
        _scan_strings(payload, violations)
        if violations:
            failures.append(f"{name} payload has {sorted(set(violations))}")

    return failures


def _manifest_sample_ids(manifest: Mapping[str, Any]) -> list[str]:
    records = manifest.get("records", [])
    if not isinstance(records, list):
        return []
    return [
        str(item["sample_id"])
        for item in records
        if isinstance(item, Mapping) and item.get("sample_id")
    ]


def _report_summary(
    *,
    elapsed: float,
    diagnostics: Mapping[str, Any],
    failures: list[str],
    output_dir: Path,
) -> None:
    print("B2 benchmark")
    print(f"  gold records        : {diagnostics.get('gold_record_count')}")
    print(f"  prediction records  : {diagnostics.get('prediction_record_count')}")
    print(f"  aligned records     : {diagnostics.get('record_count')}")
    print(f"  failed documents    : {diagnostics.get('failed_documents')}")
    print(f"  internal micro total: {diagnostics.get('weighted_total')}")
    print(f"  runtime             : {elapsed:.3f}s")
    print(f"  outputs             : {output_dir}")
    if failures:
        print("  verify              : FAILED")
        for failure in failures:
            print(f"    - {failure}")
    else:
        print("  verify              : OK")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    started = time.perf_counter()
    try:
        gold = load_records(args.gold)
        raw_predictions = load_records(args.predictions)
        weights = load_weights(args.weights)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, Mapping):
            raise ValueError("manifest must be a JSON object")
        manifest_records = manifest.get("records", [])
        if not isinstance(manifest_records, list):
            raise ValueError("manifest records must be a list")
        predictions, alignment = align_prediction_records(manifest_records, raw_predictions)
    except (OSError, ValueError, json.JSONDecodeError, AlignmentError) as error:
        _parser().error(str(error))
        return 2

    metadata, splits, stress = _build_groups(manifest)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _write_json(output_dir / "alignment.json", alignment)
    _write_jsonl(output_dir / "aligned-predictions.jsonl", predictions)
    score = score_dataset(gold, predictions, weights)
    _write_json(output_dir / "score.json", score)

    diagnostics = write_diagnostics(gold, predictions, output_dir / "diagnostics.json", metadata=metadata)
    all_summaries = subset_summaries(
        diagnostics["records"],
        {"all": _manifest_sample_ids(manifest)},
    )
    _write_json(
        output_dir / "summaries.json",
        {
            "all": all_summaries.get("all"),
            "split": subset_summaries(diagnostics["records"], splits),
            "stress": subset_summaries(diagnostics["records"], stress),
        },
    )

    write_b2_errorbook(diagnostics, output_dir / "errorbook.md", commit=args.commit, config=args.config)
    runtime = time.perf_counter() - started
    entry = entry_from_diagnostics(
        diagnostics,
        commit=args.commit or "unknown",
        config=args.config,
        runtime=runtime,
        notes=args.notes,
    )
    write_leaderboard(output_dir / "leaderboard.csv", [entry])

    failures: list[str] = []
    if not args.skip_verify:
        failures = _verify(
            output_dir,
            diagnostics=diagnostics,
            splits=splits,
            stress=stress,
            expected_gold=len(gold),
            expected_predictions=len(raw_predictions),
        )

    _report_summary(
        elapsed=runtime,
        diagnostics=diagnostics,
        failures=failures,
        output_dir=output_dir,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
