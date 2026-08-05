"""Batch-render manifest-named DOCX files with the production template."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..rendering import render_template_report
from .batch import load_manifest_entries, _prediction_aliases


def _load_jsonl(path: Path) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"prediction JSONL line {line_number} is not an object")
        records.append(value)
    return records


def render_prediction_batch_template(
    predictions_path: str | Path,
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    template_path: str | Path | None = None,
    fields_path: str | Path | None = None,
) -> dict[str, Any]:
    """Render every prediction to the manifest-authorised DOCX filename."""

    predictions_file = Path(predictions_path)
    if not predictions_file.is_file():
        raise FileNotFoundError(f"prediction JSONL not found: {predictions_file}")
    entries = load_manifest_entries(manifest_path)
    records = _load_jsonl(predictions_file)
    if len(records) != len(entries):
        raise ValueError(f"prediction/manifest count mismatch: {len(records)} != {len(entries)}")

    alias_map = {alias: entry for entry in entries for alias in entry.aliases}
    matched: dict[object, Mapping[str, Any]] = {}
    for record in records:
        aliases = _prediction_aliases(record)
        candidates = {alias_map[alias] for alias in aliases if alias in alias_map}
        if len(candidates) != 1:
            raise ValueError(
                f"prediction must match exactly one manifest row: {record.get('source_file') or record.get('sample_id')}"
            )
        entry = next(iter(candidates))
        if entry in matched:
            raise ValueError(f"duplicate prediction for manifest entry: {entry.key}")
        matched[entry] = record

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    rendered: list[str] = []
    for entry in entries:
        record = matched.get(entry)
        if record is None:
            raise ValueError(f"missing prediction for manifest entry: {entry.key}")
        target = output_root / entry.docx_name
        render_template_report(
            record,
            target,
            template_path=template_path,
            fields_path=fields_path,
        )
        rendered.append(entry.docx_name)

    return {
        "valid": True,
        "status": "succeeded",
        "input_count": len(records),
        "manifest_count": len(entries),
        "output_count": len(rendered),
        "files": sorted(rendered),
    }
