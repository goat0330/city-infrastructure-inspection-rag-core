"""Batch rendering and final-DOC conversion for submission delivery."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..conversion import convert_docx_directory
from ..rendering import render_report


_KEY_FIELDS = ("sample_id", "id", "source_docx", "source_file", "gold_record")
_DOCX_FIELDS = ("docx_filename", "rendered_filename", "output_docx")
_DOC_FIELDS = ("doc_filename", "result_filename", "output_filename")
_GENERIC_FILENAME_FIELDS = (
    "filename",
    "file_name",
    "name",
    "报告文件名",
    "文件名",
)


@dataclass(frozen=True)
class ManifestEntry:
    """One manifest row and the names it authorises for delivery."""

    key: str
    aliases: tuple[str, ...]
    docx_name: str
    doc_name: str


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _alias_values(value: object) -> set[str]:
    raw = _text(value).replace("\\", "/")
    if not raw:
        return set()
    values = {raw.casefold()}
    path = PurePosixPath(raw)
    if path.name:
        values.add(path.name.casefold())
        values.add(path.stem.casefold())
    if len(path.parts) >= 2 and path.suffix:
        values.add(f"{path.parts[-2]}-{path.stem}".casefold())
    return values


def _first(row: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = _text(row.get(field))
        if value:
            return value
    return ""


def _root_filename(value: str, suffix: str) -> str:
    raw = value.replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or len(path.parts) != 1:
        raise ValueError(f"manifest filename must be a root filename: {value!r}")
    name = path.name
    if name in {".", ".."} or name.startswith((".", "~$")):
        raise ValueError(f"manifest filename is temporary or hidden: {value!r}")
    target_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    if path.suffix.casefold() != target_suffix.casefold():
        name = f"{path.stem}{target_suffix}"
    return name


def _rows_from_manifest(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            values = payload["records"]
        elif isinstance(payload, dict) and isinstance(payload.get("files"), list):
            values = payload["files"]
        elif isinstance(payload, list):
            values = payload
        elif isinstance(payload, dict):
            values = [payload]
        else:
            raise ValueError("JSON manifest must contain records or files")
        return [item if isinstance(item, dict) else {"filename": item} for item in values]
    if suffix in {".jsonl", ".ndjson"}:
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"manifest JSONL line {line_number} is not an object")
            rows.append(value)
        return rows
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return [dict(row) for row in csv.DictReader(stream)]
    return [{"filename": line.strip()} for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def load_manifest_entries(path: str | Path) -> tuple[ManifestEntry, ...]:
    """Load manifest rows and derive only manifest-authorised output names."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"manifest not found: {source}")
    entries: list[ManifestEntry] = []
    for row in _rows_from_manifest(source):
        generic = _first(row, _GENERIC_FILENAME_FIELDS)
        docx_value = _first(row, _DOCX_FIELDS)
        doc_value = _first(row, _DOC_FIELDS)
        source_value = _text(row.get("source_docx"))
        if not docx_value and generic.casefold().endswith(".docx"):
            docx_value = generic
        if not doc_value and generic.casefold().endswith(".doc"):
            doc_value = generic
        if not docx_value and not doc_value and source_value:
            docx_value = PurePosixPath(source_value.replace("\\", "/")).name
        if not docx_value and doc_value:
            docx_value = f"{PurePosixPath(doc_value).stem}.docx"
        if not doc_value and docx_value:
            doc_value = f"{PurePosixPath(docx_value).stem}.doc"
        if not docx_value or not doc_value:
            raise ValueError(f"manifest row has no usable output filename: {row!r}")

        docx_name = _root_filename(docx_value, ".docx")
        doc_name = _root_filename(doc_value, ".doc")
        aliases: set[str] = set()
        for field in _KEY_FIELDS + _DOCX_FIELDS + _DOC_FIELDS + _GENERIC_FILENAME_FIELDS:
            aliases.update(_alias_values(row.get(field)))
        aliases.update(_alias_values(docx_name))
        aliases.update(_alias_values(doc_name))
        if not aliases:
            raise ValueError(f"manifest row has no matching key: {row!r}")
        entries.append(ManifestEntry(sorted(aliases)[0], tuple(sorted(aliases)), docx_name, doc_name))

    names = [entry.doc_name for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("manifest contains duplicate final DOC filenames")
    docx_names = [entry.docx_name for entry in entries]
    if len(docx_names) != len(set(docx_names)):
        raise ValueError("manifest contains duplicate rendered DOCX filenames")
    return tuple(entries)


def _prediction_aliases(record: Mapping[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for field in _KEY_FIELDS:
        aliases.update(_alias_values(record.get(field)))
    return aliases


def _load_predictions(path: Path) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    records: list[Mapping[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise ValueError("prediction is not an object")
            records.append(value)
        except (json.JSONDecodeError, ValueError) as exc:
            failures.append({"line": line_number, "error": f"{type(exc).__name__}: {exc}"})
    return records, failures


def render_prediction_batch(
    predictions_path: str | Path,
    manifest_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Render JSONL predictions to manifest-named DOCX files."""

    predictions_file = Path(predictions_path)
    if not predictions_file.is_file():
        raise FileNotFoundError(f"prediction JSONL not found: {predictions_file}")
    entries = load_manifest_entries(manifest_path)
    predictions, parse_failures = _load_predictions(predictions_file)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    for path in output_root.iterdir():
        if path.is_file() and (
            path.suffix.casefold() == ".docx"
            or path.suffix.casefold() in {".tmp", ".lock"}
            or path.name.startswith(("~$", ".~lock.", ".~"))
        ):
            path.unlink()
    alias_map: dict[str, ManifestEntry] = {}
    for entry in entries:
        for alias in entry.aliases:
            previous = alias_map.get(alias)
            if previous is not None and previous != entry:
                raise ValueError(f"manifest matching alias is ambiguous: {alias}")
            alias_map[alias] = entry

    matched: dict[ManifestEntry, Mapping[str, Any]] = {}
    failures = list(parse_failures)
    unmatched_predictions = 0
    records_without_key = 0
    for record in predictions:
        candidates = {alias_map[alias] for alias in _prediction_aliases(record) if alias in alias_map}
        if len(candidates) == 1:
            entry = next(iter(candidates))
            if entry in matched:
                failures.append({"error": "duplicate prediction for manifest entry", "key": entry.key})
            else:
                matched[entry] = record
        else:
            unmatched_predictions += 1
            if not _prediction_aliases(record):
                records_without_key += 1

    if records_without_key == len(predictions) and len(predictions) == len(entries):
        matched = {entry: record for entry, record in zip(entries, predictions)}
        unmatched_predictions = 0

    rendered: list[str] = []
    for entry in entries:
        record = matched.get(entry)
        if record is None:
            failures.append({"error": "missing prediction for manifest entry", "key": entry.key})
            continue
        target = output_root / entry.docx_name
        try:
            render_report(record, target)
            rendered.append(entry.docx_name)
        except Exception as exc:
            failures.append({"file": entry.docx_name, "error": f"{type(exc).__name__}: {exc}"})

    valid = (
        not failures
        and unmatched_predictions == 0
        and len(predictions) == len(entries) == len(rendered)
    )
    return {
        "valid": valid,
        "status": "succeeded" if valid else "failed",
        "input_count": len(predictions),
        "manifest_count": len(entries),
        "output_count": len(rendered),
        "failed_count": len(failures),
        "unexpected_prediction_count": unmatched_predictions,
        "files": sorted(rendered),
        "failures": failures,
    }


def convert_docx_batch(
    input_dir: str | Path,
    output_dir: str | Path,
    manifest_path: str | Path,
    *,
    soffice_path: str | Path | None = None,
    timeout_seconds: float = 300.0,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Convert rendered DOCX files and enforce manifest-named DOC outputs."""

    entries = load_manifest_entries(manifest_path)
    batch = convert_docx_directory(
        input_dir,
        output_dir,
        soffice_path=soffice_path,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )
    output_root = Path(output_dir)
    actual = sorted(
        path.name for path in output_root.iterdir() if path.is_file() and path.suffix.casefold() == ".doc"
    ) if output_root.is_dir() else []
    expected = sorted(entry.doc_name for entry in entries)
    failures = [
        {"file": Path(record["source"]).name, "error": record["error"]}
        for record in batch.records
        if record["status"] == "failed"
    ]
    if actual != expected:
        failures.append({"error": "manifest/output filename mismatch", "missing": sorted(set(expected) - set(actual)), "unexpected": sorted(set(actual) - set(expected))})
    valid = not failures and actual == expected and len(batch.records) == len(entries)
    return {
        "valid": valid,
        "status": "succeeded" if valid else "failed",
        "input_count": len(batch.records),
        "manifest_count": len(entries),
        "output_count": len(actual),
        "failed_count": len(failures),
        "files": actual,
        "records": list(batch.records),
        "failures": failures,
    }
