"""Small, deterministic audit primitives for locally held Word datasets."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import unicodedata


SUPPORTED_SUFFIXES = (".doc", ".docx")


def document_files(root: Path | str) -> list[Path]:
    """Return all candidate Word files below *root* in stable path order."""

    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"document directory does not exist: {root}")
    paths = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
    ]
    return sorted(paths, key=lambda path: relative_path(path, root).casefold())


def relative_path(path: Path, root: Path) -> str:
    """Format a path relative to a user-supplied root without machine paths."""

    return path.relative_to(root).as_posix()


def clean_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def label_base(stem: str) -> str:
    """Remove known label-only suffixes while retaining the bridge identity."""

    value = clean_name(stem)
    suffixes = (
        r"[-_ ]*无对比年度(?:的信息提取报告|定检报告)$",
        r"[-_ ]*(?:的信息提取报告|信息提取报告|定检报告)$",
        r"[-_ ]*报告$",
    )
    for suffix in suffixes:
        value = re.sub(suffix, "", value, flags=re.IGNORECASE).strip(" -_．。")
    return value


def normalise_name(value: str) -> str:
    """Create a conservative comparison key for Chinese/Latin filenames."""

    value = label_base(value).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def file_statistics(paths: list[Path], root: Path | str) -> dict[str, object]:
    root = Path(root)
    extension_counts = {suffix: 0 for suffix in SUPPORTED_SUFFIXES}
    extension_bytes = {suffix: 0 for suffix in SUPPORTED_SUFFIXES}
    for path in paths:
        suffix = path.suffix.casefold()
        extension_counts[suffix] += 1
        extension_bytes[suffix] += path.stat().st_size
    return {
        "total_files": len(paths),
        "total_bytes": sum(extension_bytes.values()),
        "by_extension": extension_counts,
        "bytes_by_extension": extension_bytes,
        "relative_paths": [relative_path(path, root) for path in paths],
    }


def _pair_for_label(label: Path, labels_root: Path, reports: list[Path], reports_root: Path, label_key_counts: Counter[str]) -> dict[str, object]:
    target = normalise_name(label_base(label.stem))
    exact: list[Path] = []
    fuzzy: list[Path] = []
    for report in reports:
        candidate = normalise_name(report.stem)
        if target and candidate == target:
            exact.append(report)
        elif target and candidate and (target in candidate or candidate in target):
            fuzzy.append(report)

    if len(exact) == 1:
        status = "paired_exact"
        match_type = "exact"
        candidates = exact
    elif len(exact) > 1:
        status = "duplicate"
        match_type = "exact"
        candidates = exact
    elif len(fuzzy) == 1:
        status = "paired_fuzzy"
        match_type = "fuzzy"
        candidates = fuzzy
    elif len(fuzzy) > 1:
        status = "ambiguous"
        match_type = "fuzzy"
        candidates = fuzzy
    else:
        status = "missing"
        match_type = "none"
        candidates = []

    candidates = sorted(candidates, key=lambda path: relative_path(path, reports_root).casefold())
    return {
        "label_relative_path": relative_path(label, labels_root),
        "label_extension": label.suffix.casefold(),
        "label_key": target,
        "duplicate_label_count": label_key_counts[target],
        "status": status,
        "match_type": match_type,
        "candidate_count": len(candidates),
        "report_relative_paths": [relative_path(path, reports_root) for path in candidates],
    }


def pair_documents(labels: list[Path], reports: list[Path], labels_root: Path | str, reports_root: Path | str) -> dict[str, object]:
    labels_root = Path(labels_root)
    reports_root = Path(reports_root)
    label_key_counts = Counter(normalise_name(label_base(path.stem)) for path in labels)
    entries = [
        _pair_for_label(path, labels_root, reports, reports_root, label_key_counts)
        for path in labels
    ]
    status_counts = Counter(str(entry["status"]) for entry in entries)
    match_counts = Counter(str(entry["match_type"]) for entry in entries)
    referenced_reports = {
        report_path
        for entry in entries
        for report_path in entry["report_relative_paths"]  # type: ignore[index]
    }
    report_relative_paths = {
        relative_path(path, reports_root) for path in reports
    }
    unmatched_reports = sorted(report_relative_paths - referenced_reports, key=str.casefold)
    return {
        "label_count": len(labels),
        "report_count": len(reports),
        "status_counts": {key: status_counts.get(key, 0) for key in ("paired_exact", "paired_fuzzy", "missing", "duplicate", "ambiguous")},
        "match_type_counts": {key: match_counts.get(key, 0) for key in ("exact", "fuzzy", "none")},
        "duplicate_label_key_counts": {
            key: count for key, count in sorted(label_key_counts.items()) if count > 1
        },
        "unmatched_report_count": len(unmatched_reports),
        "unmatched_report_relative_paths": unmatched_reports,
        "entries": entries,
    }


def _parse_label_status(labels: list[Path], labels_root: Path, pairing: dict[str, object]) -> dict[str, object]:
    from ..gold.parser import LabelParseError, parse_label_docx

    pairing_by_path = {
        str(entry["label_relative_path"]): entry
        for entry in pairing["entries"]  # type: ignore[index]
    }
    entries: list[dict[str, object]] = []
    for label in labels:
        label_relative = relative_path(label, labels_root)
        pair = pairing_by_path[label_relative]
        source_report = None
        if pair["status"] in {"paired_exact", "paired_fuzzy"}:
            source_report = pair["report_relative_paths"][0]  # type: ignore[index]
        if label.suffix.casefold() == ".doc":
            entries.append(
                {
                    "label_relative_path": label_relative,
                    "format": ".doc",
                    "status": "failed",
                    "error_code": "legacy_doc_unsupported",
                    "error": "legacy .doc labels must be converted to .docx before parsing",
                }
            )
            continue
        try:
            parse_label_docx(label, labels_root, source_report_relative_path=source_report)
        except LabelParseError as exc:
            entries.append(
                {
                    "label_relative_path": label_relative,
                    "format": ".docx",
                    "status": "failed",
                    "error_code": exc.code,
                    "error": str(exc),
                }
            )
        except Exception as exc:  # pragma: no cover - defensive boundary for malformed OOXML
            entries.append(
                {
                    "label_relative_path": label_relative,
                    "format": ".docx",
                    "status": "failed",
                    "error_code": "unexpected_parse_error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            entries.append(
                {
                    "label_relative_path": label_relative,
                    "format": ".docx",
                    "status": "succeeded",
                    "source_report_relative_path": source_report,
                }
            )
    status_counts = Counter(str(entry["status"]) for entry in entries)
    return {
        "total": len(entries),
        "succeeded": status_counts.get("succeeded", 0),
        "failed": status_counts.get("failed", 0),
        "status_counts": {
            "succeeded": status_counts.get("succeeded", 0),
            "failed": status_counts.get("failed", 0),
        },
        "entries": entries,
    }


def audit_dataset(labels_dir: Path | str, reports_dir: Path | str) -> dict[str, object]:
    """Scan labels/reports, pair names, and parse-check every label candidate."""

    labels_root = Path(labels_dir)
    reports_root = Path(reports_dir)
    labels = document_files(labels_root)
    reports = document_files(reports_root)
    pairing = pair_documents(labels, reports, labels_root, reports_root)
    return {
        "audit_version": 1,
        "file_statistics": {
            "labels": file_statistics(labels, labels_root),
            "reports": file_statistics(reports, reports_root),
        },
        "pairing": pairing,
        "label_parsing": _parse_label_status(labels, labels_root, pairing),
    }
