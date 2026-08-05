#!/usr/bin/env python3
"""Prepare the bounded eight-slot semantic extraction calibration manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVAL_MANIFEST = ROOT / "runs/b2-night/eval-manifest.json"
DEFAULT_BASELINE = ROOT / "runs/b2-night/baseline/aligned-predictions.jsonl"
DEFAULT_CANDIDATES = ROOT / "runs/round2-semantic/86-gold-candidate-inventory/candidates.jsonl"
DEFAULT_SELECTION = ROOT / "runs/narrative-k46-20260805/calibration-5/selection.json"
DEFAULT_OUTPUT = ROOT / "runs/round2-semantic/semantic-calibration-8"

CALIBRATION_SAMPLE_IDS = [
    "2012年-杨公桥A叉口人行通道",
    "2012年-桂花新村大桥",
    "2012年-梨子湾大桥",
    "2012年-凤中主线桥",
    "2013年-12-035杨公桥立交EC匝道桥",
]
SLOTS = [
    (1, "semantic-primary", "2012年-杨公桥A叉口人行通道"),
    (2, "semantic-pedestrian-ec", "2012年-杨公桥EC匝道人行通道"),
    (3, "rag-calibration-01", "2012年-杨公桥A叉口人行通道"),
    (4, "rag-calibration-02", "2012年-桂花新村大桥"),
    (5, "rag-calibration-03", "2012年-梨子湾大桥"),
    (6, "rag-calibration-04", "2012年-凤中主线桥"),
    (7, "rag-calibration-05", "2013年-12-035杨公桥立交EC匝道桥"),
    (8, "long-high-defect", "2012年-茶亭大桥"),
]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {line_number} is not an object: {path}")
            records.append(value)
    return records


def _root_relative(path: str | Path) -> str:
    value = str(path).replace("\\", "/")
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    return value


def _resolve(path: str | Path) -> Path:
    candidate = Path(str(path).replace("\\", "/"))
    return candidate if candidate.is_absolute() else ROOT / candidate


def _index_info(sample_id: str, selection: dict[str, Any]) -> dict[str, Any]:
    selected = selection.get(sample_id, {})
    selected_path = selected.get("index_dir")
    candidates = []
    if selected_path:
        candidates.append(_resolve(selected_path))
    candidates.append(ROOT / "runs/round2-semantic/indexes" / sample_id)
    for path in candidates:
        if path.is_dir():
            return {"path": _root_relative(path), "status": "available"}
    preferred = candidates[-1] if candidates else ROOT / "runs/round2-semantic/indexes" / sample_id
    return {"path": _root_relative(preferred), "status": "missing"}


def build_calibration(
    *,
    eval_manifest_path: Path = DEFAULT_EVAL_MANIFEST,
    baseline_path: Path = DEFAULT_BASELINE,
    candidates_path: Path = DEFAULT_CANDIDATES,
    selection_path: Path = DEFAULT_SELECTION,
) -> dict[str, Any]:
    manifest = _load_json(eval_manifest_path)
    records = {str(item["sample_id"]): dict(item) for item in manifest["records"]}
    baseline_records = {str(item["sample_id"]): item for item in _load_jsonl(baseline_path)}
    candidate_records = _load_jsonl(candidates_path)
    candidate_by_sample: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidate_records:
        candidate_by_sample.setdefault(str(candidate.get("sample_id", "")), []).append(candidate)
    selection = _load_json(selection_path)

    slots: list[dict[str, Any]] = []
    for slot_number, role, sample_id in SLOTS:
        record = records.get(sample_id)
        if record is None:
            raise ValueError(f"sample is not in official eval manifest: {sample_id}")
        candidate_items = candidate_by_sample.get(sample_id, [])
        baseline_exists = sample_id in baseline_records
        source_path = _resolve(record["source_docx"])
        slots.append(
            {
                "slot": slot_number,
                "role": role,
                "sample_id": sample_id,
                "split": record.get("split", ""),
                "source_docx": _root_relative(record["source_docx"]),
                "source_exists": source_path.is_file(),
                "baseline_exists": baseline_exists,
                "candidate_count": len(candidate_items),
                "candidate_task_types": sorted({str(item.get("task_type", "")) for item in candidate_items}),
                "index": _index_info(sample_id, selection),
                "candidate_ids": [str(item.get("candidate_id", "")) for item in candidate_items],
            }
        )

    duplicate_sample_ids = sorted(
        sample_id for sample_id in {item["sample_id"] for item in slots}
        if sum(item["sample_id"] == sample_id for item in slots) > 1
    )
    return {
        "schema_version": "semantic-calibration-8-v1",
        "sample_selection_note": (
            "This manifest has eight experiment slots and seven unique sample_ids. "
            "The A-叉口 sample is intentionally retained twice with separate roles because "
            "it is both the requested semantic sample and calibration-5 sample 01."
        ),
        "slot_count": len(slots),
        "unique_sample_count": len({item["sample_id"] for item in slots}),
        "duplicate_sample_ids": duplicate_sample_ids,
        "all_sources_exist": all(item["source_exists"] for item in slots),
        "all_baselines_exist": all(item["baseline_exists"] for item in slots),
        "slots": slots,
    }


def write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": result["schema_version"],
                "slot_count": result["slot_count"],
                "unique_sample_count": result["unique_sample_count"],
                "duplicate_sample_ids": result["duplicate_sample_ids"],
                "missing_indexes": [
                    item["sample_id"] for item in result["slots"] if item["index"]["status"] == "missing"
                ],
                "candidate_count_total": sum(item["candidate_count"] for item in result["slots"]),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Semantic calibration 8-slot manifest",
        "",
        result["sample_selection_note"],
        "",
        f"- slots: {result['slot_count']}",
        f"- unique sample_id: {result['unique_sample_count']}",
        f"- duplicate sample_id: {', '.join(result['duplicate_sample_ids'])}",
        "- missing indexes are recorded as missing and are not fabricated.",
        "",
        "| slot | role | sample_id | candidates | index |",
        "|---:|---|---|---:|---|",
    ]
    for item in result["slots"]:
        lines.append(
            f"| {item['slot']} | {item['role']} | {item['sample_id']} | "
            f"{item['candidate_count']} | {item['index']['status']} |"
        )
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_calibration()
    write_outputs(result, args.output_dir)
    print(json.dumps(result["summary"] if "summary" in result else {
        "slot_count": result["slot_count"],
        "unique_sample_count": result["unique_sample_count"],
        "duplicate_sample_ids": result["duplicate_sample_ids"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
