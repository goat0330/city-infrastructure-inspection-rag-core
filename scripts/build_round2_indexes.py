"""Build fit-only Narrative RAG indexes for the two missing long reports.

Deterministic assembly only.  The common part of every index is the fit-only
Gold label example set plus the professional knowledge cards from the frozen
K46 index, followed by current-report evidence selected from the target report
itself.  A target's own Gold labels never enter its index.

When the IAIC_* model environment is unavailable, the script still assembles
and validates the deterministic entries, writes an honest build summary, and
reports the exact missing variables without fabricating vectors.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_narrative_enhancement import _report_facts, _select_context_facts  # noqa: E402
from src.llm.client import OpenAIModelClient  # noqa: E402
from src.rag import LightRagIndex  # noqa: E402
from src.rag.index import METADATA_FILENAME, VECTORS_FILENAME, _embed_in_batches, _write_metadata  # noqa: E402

TARGETS = [
    "2013年-12-027杨公桥立交DA-ED匝道桥",
    "2013年-12-030杨公桥DC匝道桥",
]
BASE_INDEX_DIR = ROOT / "runs/narrative-k46-20260804/rag-index"
EVAL_MANIFEST_PATH = ROOT / "runs/b2-night/eval-manifest.json"
BASELINE_PATH = ROOT / "runs/b2-night/baseline/aligned-predictions.jsonl"
OUTPUT_DIR = ROOT / "runs/round2-semantic/indexes"
SUMMARY_PATH = ROOT / "runs/round2-semantic/index-build-summary.json"
REQUIRED_ENV = ("IAIC_API_BASE", "IAIC_API_KEY", "IAIC_EMBED_MODEL")
GOLD_KIND = "gold_label"
KNOWLEDGE_KIND = "knowledge_card"
EVIDENCE_KIND = "report_evidence"
MAX_EVIDENCE_CHARS = 1600


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {line_number}: {path}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"entry at line {line_number} is not a JSON object: {path}")
            records.append(record)
    return records


def load_base_entries(
    base_metadata_path: Path,
    excluded_sample_ids: set[str],
) -> list[dict[str, Any]]:
    """Fit-only Gold label examples plus knowledge cards, minus excluded targets."""
    entries: list[dict[str, Any]] = []
    for record in load_jsonl(base_metadata_path):
        kind = str(record.get("kind", ""))
        if kind == GOLD_KIND:
            if str(record.get("split", "")).lower() != "fit":
                continue
            if str(record.get("sample_id", "")) in excluded_sample_ids:
                continue
        elif kind != KNOWLEDGE_KIND:
            continue
        entries.append(dict(record))
    return entries


def select_report_evidence(
    docx_path: Path,
    source_file: str,
    baseline: dict[str, Any],
    sample_id: str,
    split: str,
    *,
    max_items: int = 24,
    max_chars: int = 12000,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deterministically select current-report evidence for one target."""
    facts = _report_facts(docx_path, source_file)
    selected = _select_context_facts(facts, baseline, max_items=max_items, max_chars=max_chars)
    entries: list[dict[str, Any]] = []
    for fact in selected:
        evidence_id = str(fact["evidence_id"])
        text = str(fact.get("text", ""))
        if len(text) > MAX_EVIDENCE_CHARS:
            text = text[: MAX_EVIDENCE_CHARS - 1] + "…"
        entries.append(
            {
                "evidence_id": evidence_id,
                "id": f"report:{evidence_id}",
                "kind": EVIDENCE_KIND,
                "sample_id": sample_id,
                "section": fact.get("section", "unrouted"),
                "split": split,
                "text": text,
            }
        )
    stats = {
        "report_fact_count": len(facts),
        "selected_report_evidence_count": len(entries),
        "selected_sections": [entry["section"] for entry in entries],
    }
    return entries, stats


def assemble_targets(
    targets: list[str],
    *,
    manifest_path: Path = EVAL_MANIFEST_PATH,
    baseline_path: Path = BASELINE_PATH,
    base_metadata_path: Path = BASE_INDEX_DIR / METADATA_FILENAME,
    docx_root: Path = ROOT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load shared base entries and per-target current-report evidence."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_by_id = {item["sample_id"]: item for item in manifest["records"]}
    baselines = {item["sample_id"]: item for item in load_jsonl(baseline_path)}
    base_entries = load_base_entries(base_metadata_path, set(targets))

    target_data: dict[str, dict[str, Any]] = {}
    for sample_id in targets:
        record = manifest_by_id.get(sample_id)
        baseline = baselines.get(sample_id)
        if record is None or baseline is None:
            raise ValueError(f"missing manifest or baseline record for {sample_id}")
        docx_path = docx_root / record["source_docx"]
        source_file = str(baseline.get("source_file") or record["source_docx"])
        entries, stats = select_report_evidence(
            docx_path,
            source_file,
            baseline,
            sample_id,
            str(record["split"]),
        )
        target_data[sample_id] = {
            "split": record["split"],
            "source_docx": record["source_docx"],
            "entries": entries,
            **stats,
        }
    return base_entries, target_data


def source_accounting(base_entries: list[dict[str, Any]], evidence_entries: list[dict[str, Any]]) -> dict[str, Any]:
    fit_gold_count = sum(
        1 for record in base_entries
        if record.get("kind") == GOLD_KIND and str(record.get("split", "")).lower() == "fit"
    )
    knowledge_count = sum(1 for record in base_entries if record.get("kind") == KNOWLEDGE_KIND)
    evidence_count = len(evidence_entries)
    return {
        "fit_gold": {"count": fit_gold_count, "kind": GOLD_KIND, "split": "fit"},
        "label_example": {
            "count": fit_gold_count,
            "kind": GOLD_KIND,
            "note": "public retrieval name for the fit Gold label examples",
        },
        "domain_knowledge": {"count": knowledge_count, "kind": KNOWLEDGE_KIND},
        "report_evidence": {
            "count": evidence_count,
            "kind": EVIDENCE_KIND,
            "note": "selected from the target report only",
        },
        "total": fit_gold_count + knowledge_count + evidence_count,
    }


def validate_index_dir(index_dir: Path) -> dict[str, Any]:
    index = LightRagIndex.load(index_dir)
    vectors = np.asarray(index.vectors, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[0] != len(index.metadata):
        raise ValueError(f"metadata and vectors must have the same row count: {index_dir}")
    if vectors.shape[1] == 0 or not np.isfinite(vectors).all():
        raise ValueError(f"vectors must be finite 2-D with a positive dimension: {index_dir}")
    return {"metadata_rows": len(index.metadata), "vector_shape": list(vectors.shape)}


def _reuse_base_vectors(base_entries: list[dict[str, Any]], base_index_dir: Path) -> np.ndarray | None:
    """Reuse aligned vectors from the frozen base index when metadata matches."""
    metadata_path = base_index_dir / METADATA_FILENAME
    vectors_path = base_index_dir / VECTORS_FILENAME
    if not metadata_path.is_file() or not vectors_path.is_file():
        return None
    metadata = load_jsonl(metadata_path)
    vectors = np.asarray(np.load(vectors_path, allow_pickle=False), dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[0] != len(metadata):
        return None
    by_id: dict[str, tuple[str, np.ndarray]] = {}
    for record, vector in zip(metadata, vectors):
        entry_id = str(record.get("id", ""))
        if not entry_id or entry_id in by_id:
            return None
        by_id[entry_id] = (str(record.get("text", "")), vector)
    selected: list[np.ndarray] = []
    for entry in base_entries:
        match = by_id.get(str(entry.get("id", "")))
        if match is None or match[0] != str(entry.get("text", "")):
            return None
        selected.append(match[1])
    if not selected:
        return np.empty((0, vectors.shape[1]), dtype=np.float32)
    return np.ascontiguousarray(np.vstack(selected), dtype=np.float32)


def build_indexes(
    base_entries: list[dict[str, Any]],
    target_data: dict[str, dict[str, Any]],
    client: Any,
    output_dir: Path,
    base_index_dir: Path = BASE_INDEX_DIR,
) -> dict[str, Any]:
    """Embed shared base plus per-target evidence and persist row-aligned indexes."""
    base_vectors = _reuse_base_vectors(base_entries, base_index_dir)
    if base_vectors is None:
        base_vectors = _embed_in_batches(client, [record["text"] for record in base_entries])
    built: dict[str, Any] = {}
    for sample_id, data in target_data.items():
        target_dir = output_dir / sample_id
        target_dir.mkdir(parents=True, exist_ok=True)
        entries = data["entries"]
        if entries:
            vectors = np.vstack([base_vectors, _embed_in_batches(client, [entry["text"] for entry in entries])])
        else:
            vectors = np.empty((0, base_vectors.shape[1]), dtype=np.float32)
        _write_metadata(target_dir / METADATA_FILENAME, base_entries + entries)
        np.save(target_dir / VECTORS_FILENAME, np.ascontiguousarray(vectors, dtype=np.float32), allow_pickle=False)
        built[sample_id] = validate_index_dir(target_dir)
    return built


def build_summary(
    base_entries: list[dict[str, Any]],
    target_data: dict[str, dict[str, Any]],
    *,
    built: dict[str, Any] | None,
    missing_env: list[str],
    targets: list[str] | None = None,
) -> dict[str, Any]:
    target_names = list(targets or target_data)
    targets: dict[str, Any] = {}
    for sample_id, data in target_data.items():
        accounting = source_accounting(base_entries, data["entries"])
        targets[sample_id] = {
            "split": data["split"],
            "source_docx": data["source_docx"],
            "report_fact_count": data["report_fact_count"],
            "selected_report_evidence_count": data["selected_report_evidence_count"],
            "selected_sections": data["selected_sections"],
            "source_accounting": accounting,
            "index_dir": None if built is None else str((OUTPUT_DIR / sample_id).relative_to(ROOT)),
            "index": None if built is None else built.get(sample_id),
        }
    return {
        "schema": "round2-fit-only-index-build-v1",
        "targets": target_names,
        "base_source": str(BASE_INDEX_DIR.relative_to(ROOT)),
        "model_environment": {
            "available": not missing_env,
            "missing_variables": missing_env,
        },
        "targets_detail": targets,
        "build": {
            "status": "built" if built is not None else "blocked",
            "reason": (
                "missing IAIC_* model environment: " + ", ".join(missing_env)
                if missing_env
                else "all target indexes embedded and validated"
            ),
            "model_calls": 0 if built is None else None,
        },
    }


def write_summary(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--summary-path", type=Path, default=SUMMARY_PATH)
    parser.add_argument(
        "--target",
        dest="targets",
        action="append",
        help="target sample_id; repeat to build additional samples",
    )
    args = parser.parse_args(argv)

    targets = list(args.targets or TARGETS)
    base_entries, target_data = assemble_targets(targets)
    missing_env = [name for name in REQUIRED_ENV if not os.getenv(name)]

    if missing_env:
        summary = build_summary(
            base_entries, target_data, built=None, missing_env=missing_env, targets=targets
        )
        write_summary(summary, args.summary_path)
        print(json.dumps({"status": "blocked", "missing_variables": missing_env}, ensure_ascii=False))
        return 2

    client = OpenAIModelClient(timeout=120, retry_delay=0.2)
    built = build_indexes(base_entries, target_data, client, args.output_dir)
    summary = build_summary(base_entries, target_data, built=built, missing_env=[], targets=targets)
    write_summary(summary, args.summary_path)
    print(json.dumps({"status": "built", "targets": built}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
