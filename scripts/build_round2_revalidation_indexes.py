#!/usr/bin/env python3
"""Build fit-only RAG indexes for the Round2 narrative revalidation set."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_ENV = ("IAIC_API_BASE", "IAIC_API_KEY", "IAIC_EMBED_MODEL")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _summary(
    targets: list[str],
    base_entries: list[dict[str, Any]],
    target_data: dict[str, dict[str, Any]],
    output_dir: Path,
    *,
    built: dict[str, Any] | None,
    missing_env: list[str],
) -> dict[str, Any]:
    from scripts.build_round2_indexes import source_accounting

    detail: dict[str, Any] = {}
    for sample_id in targets:
        data = target_data[sample_id]
        detail[sample_id] = {
            "split": data["split"],
            "source_docx": data["source_docx"],
            "report_fact_count": data["report_fact_count"],
            "selected_report_evidence_count": data["selected_report_evidence_count"],
            "selected_sections": data["selected_sections"],
            "source_accounting": source_accounting(base_entries, data["entries"]),
            "index_dir": str(output_dir / sample_id) if built is not None else None,
            "index": None if built is None else built.get(sample_id),
        }
    return {
        "schema": "round2-narrative-revalidation-indexes-v1",
        "targets": targets,
        "target_count": len(targets),
        "model_environment": {
            "available": not missing_env,
            "missing_variables": missing_env,
        },
        "targets_detail": detail,
        "build": {
            "status": "built" if built is not None else "blocked",
            "reason": (
                "missing IAIC_* model environment: " + ", ".join(missing_env)
                if missing_env
                else "all indexes embedded and validated"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--eval-manifest", type=Path, required=True)
    parser.add_argument("--baseline-jsonl", type=Path, required=True)
    parser.add_argument("--base-index-dir", type=Path, required=True)
    parser.add_argument("--docx-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    from scripts.build_round2_indexes import (  # noqa: E402
        METADATA_FILENAME,
        assemble_targets,
        build_indexes,
    )

    manifest = json.loads(args.samples.read_text(encoding="utf-8"))
    targets = [str(item["sample_id"]) for item in manifest.get("samples", [])]
    if not targets:
        raise ValueError("sample manifest is empty")

    base_entries, target_data = assemble_targets(
        targets,
        manifest_path=args.eval_manifest,
        baseline_path=args.baseline_jsonl,
        base_metadata_path=args.base_index_dir / METADATA_FILENAME,
        docx_root=args.docx_root,
    )
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        result = _summary(
            targets,
            base_entries,
            target_data,
            args.output_dir,
            built=None,
            missing_env=missing,
        )
        _write(args.summary, result)
        print(json.dumps({"status": "blocked", "missing_variables": missing}, ensure_ascii=False))
        return 2

    from src.llm.client import OpenAIModelClient  # noqa: E402

    client = OpenAIModelClient(timeout=120, retry_delay=0.2)
    built = build_indexes(base_entries, target_data, client, args.output_dir)
    result = _summary(
        targets,
        base_entries,
        target_data,
        args.output_dir,
        built=built,
        missing_env=[],
    )
    _write(args.summary, result)
    print(json.dumps({"status": "built", "target_count": len(built)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
