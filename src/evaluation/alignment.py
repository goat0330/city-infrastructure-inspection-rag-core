"""Manifest-backed provenance alignment for evaluation predictions."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


class AlignmentError(ValueError):
    """Raised when a manifest record cannot map to exactly one prediction."""


def _normalise_path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return PurePosixPath(text).as_posix() if text else ""


def _same_report_path(left: str, right: str) -> bool:
    return bool(left and right) and (
        left == right
        or left.endswith("/" + right)
        or right.endswith("/" + left)
    )


def align_prediction_records(
    manifest_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int | str]]:
    """Select and order predictions using manifest ``source_docx`` provenance.

    The raw prediction JSONL is never modified in place.  The returned records
    are evaluation views ordered like the manifest; only their ``sample_id`` is
    rewritten to the manifest id when the source path proves the match.
    """

    manifest = [dict(record) for record in manifest_records]
    predictions = [dict(record) for record in prediction_records]
    source_values = [str(record.get("source_docx", "")).strip() for record in manifest]

    if not manifest or not any(source_values):
        return predictions, {
            "mode": "sample-id",
            "manifest_count": len(manifest),
            "input_prediction_count": len(predictions),
            "aligned_prediction_count": len(predictions),
            "excluded_prediction_count": 0,
            "sample_id_rewritten_count": 0,
        }
    if not all(source_values):
        raise AlignmentError("manifest source_docx is incomplete; refusing partial alignment")

    indexed: list[tuple[int, dict[str, Any], str]] = []
    for index, record in enumerate(predictions):
        source_file = _normalise_path(record.get("source_file"))
        if not source_file:
            raise AlignmentError(f"prediction at index {index} has no source_file")
        indexed.append((index, record, source_file))

    aligned: list[dict[str, Any]] = []
    used_indices: set[int] = set()
    missing: list[str] = []
    ambiguous: list[str] = []
    rewritten = 0

    for item in manifest:
        sample_id = str(item.get("sample_id", "")).strip()
        source_docx = _normalise_path(item.get("source_docx"))
        matches = [
            (index, record)
            for index, record, source_file in indexed
            if _same_report_path(source_docx, source_file)
        ]
        if not matches:
            missing.append(sample_id or source_docx)
            continue
        if len(matches) > 1:
            ambiguous.append(sample_id or source_docx)
            continue
        index, record = matches[0]
        if index in used_indices:
            ambiguous.append(sample_id or source_docx)
            continue
        used_indices.add(index)
        evaluation_record = dict(record)
        if evaluation_record.get("sample_id") != sample_id:
            rewritten += 1
        evaluation_record["sample_id"] = sample_id
        aligned.append(evaluation_record)

    if missing or ambiguous:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing[:8]))
        if ambiguous:
            details.append("ambiguous=" + ",".join(ambiguous[:8]))
        raise AlignmentError("manifest prediction alignment failed: " + "; ".join(details))

    return aligned, {
        "mode": "manifest-source-docx",
        "manifest_count": len(manifest),
        "input_prediction_count": len(predictions),
        "aligned_prediction_count": len(aligned),
        "excluded_prediction_count": len(predictions) - len(aligned),
        "sample_id_rewritten_count": rewritten,
    }
