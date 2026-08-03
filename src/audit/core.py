"""Small, deterministic audit primitives for locally held Word datasets."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
import unicodedata


SUPPORTED_SUFFIXES = (".doc", ".docx")
_EMPTY_MARKERS = {"", "无", "暂无", "不适用", "none", "null", "n/a"}


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
    """Remove known label-only suffixes while retaining the facility identity."""

    value = clean_name(stem)
    suffixes = (
        r"[-_ ]*无对比年度(?:的信息提取报告|定检报告)$",
        r"[-_ ]*(?:的信息提取报告|信息提取报告|定检报告)$",
        r"[-_ ]*检测报告$",
        r"[-_ ]*报告$",
    )
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            updated = re.sub(suffix, "", value, flags=re.IGNORECASE).strip(" -_．。")
            if updated != value:
                value = updated
                changed = True
    return value


def normalise_name(value: str) -> str:
    """Create a conservative comparison key for Chinese/Latin filenames."""

    value = label_base(value).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _year_bucket(relative: str) -> str:
    match = re.search(r"(?:19|20)\d{2}", relative)
    return match.group(0) if match else "unknown"


def file_statistics(paths: list[Path], root: Path | str) -> dict[str, object]:
    root = Path(root)
    extension_counts = {suffix: 0 for suffix in SUPPORTED_SUFFIXES}
    extension_bytes = {suffix: 0 for suffix in SUPPORTED_SUFFIXES}
    year_counts: Counter[str] = Counter()
    for path in paths:
        suffix = path.suffix.casefold()
        extension_counts[suffix] += 1
        extension_bytes[suffix] += path.stat().st_size
        year_counts[_year_bucket(relative_path(path, root))] += 1
    return {
        "total_files": len(paths),
        "total_bytes": sum(extension_bytes.values()),
        "by_extension": extension_counts,
        "bytes_by_extension": extension_bytes,
        "by_year": dict(sorted(year_counts.items())),
        "relative_paths": [relative_path(path, root) for path in paths],
    }


def _pair_for_label(
    label: Path,
    labels_root: Path,
    reports: list[Path],
    reports_root: Path,
    label_key_counts: Counter[str],
) -> dict[str, object]:
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


def pair_documents(
    labels: list[Path],
    reports: list[Path],
    labels_root: Path | str,
    reports_root: Path | str,
) -> dict[str, object]:
    labels_root = Path(labels_root)
    reports_root = Path(reports_root)
    label_key_counts = Counter(normalise_name(label_base(path.stem)) for path in labels)
    entries = [
        _pair_for_label(path, labels_root, reports, reports_root, label_key_counts)
        for path in labels
    ]
    status_counts = Counter(str(entry["status"]) for entry in entries)
    match_counts = Counter(str(entry["match_type"]) for entry in entries)

    candidate_usage: dict[str, list[str]] = defaultdict(list)
    unambiguously_paired: set[str] = set()
    for entry in entries:
        label_path = str(entry["label_relative_path"])
        for report_path in entry["report_relative_paths"]:  # type: ignore[index]
            candidate_usage[str(report_path)].append(label_path)
        if entry["status"] in {"paired_exact", "paired_fuzzy"}:
            unambiguously_paired.add(str(entry["report_relative_paths"][0]))  # type: ignore[index]

    report_relative_paths = {relative_path(path, reports_root) for path in reports}
    unmatched_reports = sorted(report_relative_paths - set(candidate_usage), key=str.casefold)
    unresolved_reports = sorted(report_relative_paths - unambiguously_paired, key=str.casefold)
    report_usage_conflicts = {
        report_path: sorted(label_paths, key=str.casefold)
        for report_path, label_paths in sorted(candidate_usage.items())
        if len(label_paths) > 1
    }
    return {
        "label_count": len(labels),
        "report_count": len(reports),
        "status_counts": {
            key: status_counts.get(key, 0)
            for key in ("paired_exact", "paired_fuzzy", "missing", "duplicate", "ambiguous")
        },
        "match_type_counts": {key: match_counts.get(key, 0) for key in ("exact", "fuzzy", "none")},
        "duplicate_label_key_counts": {
            key: count for key, count in sorted(label_key_counts.items()) if count > 1
        },
        "report_usage_conflicts": report_usage_conflicts,
        "unambiguously_paired_report_count": len(unambiguously_paired),
        "unmatched_report_count": len(unmatched_reports),
        "unmatched_report_relative_paths": unmatched_reports,
        "unresolved_report_count": len(unresolved_reports),
        "unresolved_report_relative_paths": unresolved_reports,
        "entries": entries,
    }


def _is_empty(value: object) -> bool:
    return clean_name(str(value)).casefold() in _EMPTY_MARKERS


def _recommendation_summary_counts(value: object) -> dict[str, int]:
    text = clean_name(str(value))
    result: dict[str, int] = {}
    patterns = {
        "立即": r"(\d+)\s*条[^，,、；;。]*立即",
        "尽快": r"(\d+)\s*条[^，,、；;。]*尽快",
        "预防": r"(\d+)\s*条[^，,、；;。]*预防",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            result[key] = int(match.group(1))
    return result


def label_quality_flags(record: dict[str, object]) -> list[dict[str, object]]:
    """Return deterministic label inconsistencies that should enter the error book."""

    flags: list[dict[str, object]] = []
    summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
    trend = summary.get("trend", "")  # type: ignore[union-attr]
    previous_score = summary.get("previous_overall_score", "")  # type: ignore[union-attr]
    previous_grade = summary.get("previous_overall_grade", "")  # type: ignore[union-attr]
    detailed = "".join(str(item) for item in record.get("detailed_conclusion", []))

    if _is_empty(previous_score) and _is_empty(previous_grade) and not _is_empty(trend):
        flags.append(
            {
                "code": "trend_without_previous_score",
                "message": "历史评分和等级为空/无，但趋势字段包含实质内容。",
            }
        )
    if ("首次" in detailed or "无往年" in detailed) and not _is_empty(trend):
        flags.append(
            {
                "code": "first_inspection_with_trend",
                "message": "详细结论称首次检测或无往年数据，但趋势字段包含实质内容。",
            }
        )

    recommendations = record.get("recommendations") if isinstance(record.get("recommendations"), list) else []
    summary_counts = _recommendation_summary_counts(summary.get("recommendations_summary", ""))  # type: ignore[union-attr]
    if summary_counts:
        actual = Counter()
        for item in recommendations:
            category = str(item.get("category", "")) if isinstance(item, dict) else ""
            if "立即" in category:
                actual["立即"] += 1
            elif "尽快" in category:
                actual["尽快"] += 1
            elif "预防" in category:
                actual["预防"] += 1
        mismatches = {
            key: {"summary": expected, "rows": actual.get(key, 0)}
            for key, expected in summary_counts.items()
            if expected != actual.get(key, 0)
        }
        if mismatches:
            flags.append(
                {
                    "code": "recommendation_count_mismatch",
                    "message": "建议汇总数量与建议明细行不一致。",
                    "details": mismatches,
                }
            )
    return flags


def _parse_label_status(
    labels: list[Path], labels_root: Path, pairing: dict[str, object]
) -> dict[str, object]:
    from ..gold.parser import LabelParseError, parse_label_docx

    pairing_by_path = {
        str(entry["label_relative_path"]): entry
        for entry in pairing["entries"]  # type: ignore[index]
    }
    entries: list[dict[str, object]] = []
    flag_counts: Counter[str] = Counter()
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
            record = parse_label_docx(label, labels_root, source_report_relative_path=source_report)
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
        except Exception as exc:  # pragma: no cover - malformed external OOXML boundary
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
            flags = label_quality_flags(record)
            flag_counts.update(str(flag["code"]) for flag in flags)
            entries.append(
                {
                    "label_relative_path": label_relative,
                    "format": ".docx",
                    "status": "succeeded",
                    "source_report_relative_path": source_report,
                    "quality_flags": flags,
                }
            )
    status_counts = Counter(str(entry["status"]) for entry in entries)
    return {
        "total": len(entries),
        "succeeded": status_counts.get("succeeded", 0),
        "failed": status_counts.get("failed", 0),
        "quality_flag_count": sum(flag_counts.values()),
        "quality_flag_counts": dict(sorted(flag_counts.items())),
        "status_counts": {
            "succeeded": status_counts.get("succeeded", 0),
            "failed": status_counts.get("failed", 0),
        },
        "entries": entries,
    }


def audit_dataset(labels_dir: Path | str, reports_dir: Path | str) -> dict[str, object]:
    """Scan labels/reports, pair names, parse-check labels, and surface conflicts."""

    labels_root = Path(labels_dir)
    reports_root = Path(reports_dir)
    labels = document_files(labels_root)
    reports = document_files(reports_root)
    pairing = pair_documents(labels, reports, labels_root, reports_root)
    return {
        "audit_version": 2,
        "file_statistics": {
            "labels": file_statistics(labels, labels_root),
            "reports": file_statistics(reports, reports_root),
        },
        "pairing": pairing,
        "label_parsing": _parse_label_status(labels, labels_root, pairing),
    }


def render_audit_markdown(report: dict[str, object]) -> str:
    """Render a compact, human-readable audit summary without absolute paths."""

    labels = report["file_statistics"]["labels"]  # type: ignore[index]
    reports = report["file_statistics"]["reports"]  # type: ignore[index]
    pairing = report["pairing"]  # type: ignore[index]
    parsing = report["label_parsing"]  # type: ignore[index]
    lines = [
        "# 数据审计报告",
        "",
        f"- 标签文件：{labels['total_files']}（.docx {labels['by_extension']['.docx']}，.doc {labels['by_extension']['.doc']}）",
        f"- 原始报告：{reports['total_files']}（.docx {reports['by_extension']['.docx']}，.doc {reports['by_extension']['.doc']}）",
        f"- 精确配对：{pairing['status_counts']['paired_exact']}",
        f"- 模糊配对：{pairing['status_counts']['paired_fuzzy']}",
        f"- 缺失/歧义/重复：{pairing['status_counts']['missing']}/{pairing['status_counts']['ambiguous']}/{pairing['status_counts']['duplicate']}",
        f"- 标签解析成功/失败：{parsing['succeeded']}/{parsing['failed']}",
        f"- 标签质量标记：{parsing['quality_flag_count']}",
        "",
        "## 年份分布",
        "",
        f"- 标签：{labels['by_year']}",
        f"- 报告：{reports['by_year']}",
        "",
        "## 质量标记统计",
        "",
        f"{parsing['quality_flag_counts'] or '无'}",
        "",
    ]
    return "\n".join(lines)
