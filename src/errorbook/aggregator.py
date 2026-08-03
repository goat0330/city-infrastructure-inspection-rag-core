"""Deterministic, summary-only Gate 0 errorbook aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
import re
from typing import Any


def _object(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: object) -> Sequence[object]:
    return value if isinstance(value, (list, tuple)) else ()


def _as_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return max(int(value), 0)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        if math.isfinite(parsed) and parsed.is_integer():
            return max(int(parsed), 0)
    return None


def _as_score(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return parsed if math.isfinite(parsed) else None


def _first_count(*values: object) -> int:
    for value in values:
        parsed = _as_count(value)
        if parsed is not None:
            return parsed
    return 0


def _first_score(*values: object) -> float | None:
    for value in values:
        parsed = _as_score(value)
        if parsed is not None:
            return parsed
    return None


def _count_map(value: object) -> dict[str, int]:
    result: dict[str, int] = {}
    if not isinstance(value, Mapping):
        return result
    for key, raw_count in value.items():
        count = _as_count(raw_count)
        if count is None or count == 0:
            continue
        category = _safe_code(key)
        result[category] = result.get(category, 0) + count
    return result


def _safe_code(value: object) -> str:
    """Keep category identifiers while refusing path-like or free-text data."""

    if not isinstance(value, str):
        return "untrusted_category"
    text = value.strip()
    if (
        not text
        or len(text) > 80
        or re.search(r"[\\/]", text)
        or re.search(r"^[A-Za-z]:", text)
        or "://" in text
    ):
        return "untrusted_category"
    if not re.fullmatch(r"[0-9A-Za-z_.:-]{1,80}", text):
        return "untrusted_category"
    return text


def _add_category(categories: dict[str, int], name: str, count: object) -> None:
    parsed = _as_count(count)
    if parsed:
        categories[name] = categories.get(name, 0) + parsed


def _quality_flag_counts(audit: Mapping[str, Any], gold: Mapping[str, Any]) -> dict[str, int]:
    parsing = _object(audit.get("label_parsing"))
    counts = _count_map(parsing.get("quality_flag_counts"))
    if counts:
        return counts

    derived: dict[str, int] = {}
    for entry in _items(parsing.get("entries")):
        for flag in _items(_object(entry).get("quality_flags")):
            code = _safe_code(_object(flag).get("code"))
            derived[code] = derived.get(code, 0) + 1
    if derived:
        return derived

    for record in _items(gold.get("records")):
        for flag in _items(_object(record).get("quality_flags")):
            code = _safe_code(_object(flag).get("code"))
            derived[code] = derived.get(code, 0) + 1
    return derived


def _gold_failure_counts(gold: Mapping[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    failed = gold.get("failed")
    if isinstance(failed, Mapping):
        return _count_map(failed)
    for item in _items(failed):
        code = _safe_code(_object(item).get("error_code", "unknown"))
        result[code] = result.get(code, 0) + 1
    return result


def _conversion_status(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().casefold()
    if normalized in {"complete", "completed", "ok", "success"}:
        return "complete"
    if normalized in {"incomplete", "missing", "failed", "not_completed"}:
        return "incomplete"
    return "unknown"


def _conversion_counts(state: Mapping[str, Any] | None) -> dict[str, int]:
    if state is None or "records" not in state:
        return {}
    records = _items(state.get("records"))
    status_counts = {"success": 0, "skipped": 0, "failed": 0}
    usable_counts = {"true": 0, "false": 0, "missing": 0}
    for record in records:
        item = _object(record)
        status = item.get("status")
        if isinstance(status, str) and status in status_counts:
            status_counts[status] += 1
        usable = item.get("target_is_usable")
        if usable is True or (isinstance(usable, str) and usable.casefold() == "true"):
            usable_counts["true"] += 1
        elif usable is False or (isinstance(usable, str) and usable.casefold() == "false"):
            usable_counts["false"] += 1
        else:
            usable_counts["missing"] += 1
    return {
        "record_count": len(records),
        "success_count": status_counts["success"],
        "skipped_count": status_counts["skipped"],
        "failed_count": status_counts["failed"],
        "target_is_usable_true_count": usable_counts["true"],
        "target_is_usable_false_count": usable_counts["false"],
        "target_is_usable_missing_count": usable_counts["missing"],
    }


def aggregate_errorbook(
    audit_report: Mapping[str, Any] | None,
    gold: Mapping[str, Any] | None,
    self_score: Mapping[str, Any] | None,
    *,
    conversion_status: str | None = None,
    conversion_state: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Aggregate three JSON payloads without retaining document-level data."""

    audit = _object(audit_report)
    gold_data = _object(gold)
    score = _object(self_score)
    pairing = _object(audit.get("pairing"))
    pairing_status = _object(pairing.get("status_counts"))
    pairing_match = _object(pairing.get("match_type_counts"))
    parsing = _object(audit.get("label_parsing"))
    parsing_status = _object(parsing.get("status_counts"))
    file_statistics = _object(audit.get("file_statistics"))
    report_statistics = _object(file_statistics.get("reports"))
    gold_statistics = _object(gold_data.get("statistics"))

    failed_count = _first_count(
        parsing.get("failed"),
        parsing_status.get("failed"),
        gold_statistics.get("failed_count"),
        len(_items(gold_data.get("failed"))),
    )
    quality_counts = _quality_flag_counts(audit, gold_data)
    quality_total = _first_count(
        parsing.get("quality_flag_count"),
        gold_statistics.get("quality_flag_count"),
        sum(quality_counts.values()),
    )
    unresolved_count = _first_count(
        pairing.get("unresolved_report_count"),
        pairing.get("unmatched_report_count"),
        len(_items(pairing.get("unresolved_report_relative_paths"))),
    )
    conversion = _conversion_status(conversion_status)
    conversion_counts = _conversion_counts(conversion_state)

    statistics: dict[str, object] = {
        "label_count": _first_count(
            pairing.get("label_count"),
            gold_statistics.get("label_count"),
            parsing.get("total"),
        ),
        "report_count": _first_count(pairing.get("report_count"), report_statistics.get("total_files")),
        "exact_match_count": _first_count(
            pairing_status.get("paired_exact"), pairing_match.get("exact")
        ),
        "fuzzy_match_count": _first_count(
            pairing_status.get("paired_fuzzy"), pairing_match.get("fuzzy")
        ),
        "succeeded_count": _first_count(
            parsing.get("succeeded"), parsing_status.get("succeeded"), gold_statistics.get("record_count")
        ),
        "failed_count": failed_count,
        "quality_flag_count": quality_total,
        "unresolved_report_count": unresolved_count,
        "gold_self_score": _first_score(
            score.get("total_score"), score.get("micro_total_score"), score.get("macro_total_score")
        ),
    }

    categories: dict[str, int] = {}
    _add_category(categories, "unresolved_reports", unresolved_count)
    _add_category(categories, "label_parse_failure", failed_count)
    for status, category in (
        ("ambiguous", "ambiguous_pairing"),
        ("duplicate", "duplicate_pairing"),
        ("missing", "missing_pairing"),
    ):
        _add_category(categories, category, pairing_status.get(status))
    for code, count in _gold_failure_counts(gold_data).items():
        _add_category(categories, f"gold_failure:{code}", count)
    for code, count in quality_counts.items():
        _add_category(categories, f"quality_flag:{code}", count)
    if conversion == "incomplete":
        _add_category(categories, "conversion_incomplete", 1)
    if conversion_counts:
        _add_category(categories, "conversion_failed", conversion_counts["failed_count"])
        _add_category(
            categories,
            "conversion_unusable_target",
            conversion_counts["target_is_usable_false_count"]
            + conversion_counts["target_is_usable_missing_count"],
        )

    conversion_summary: dict[str, object] = {"status": conversion}
    conversion_summary.update(conversion_counts)
    return {
        "version": 1,
        "statistics": statistics,
        "error_categories": dict(sorted(categories.items())),
        "conversion": conversion_summary,
    }


def _display_number(value: object) -> str:
    parsed = _as_score(value)
    if parsed is None:
        return "—"
    return str(int(parsed)) if parsed.is_integer() else f"{parsed:g}"


def render_errorbook_markdown(summary: Mapping[str, Any]) -> str:
    """Render only fixed statistics, safe category names, and conversion status."""

    statistics = _object(summary.get("statistics"))
    categories = _count_map(summary.get("error_categories"))
    conversion_data = _object(summary.get("conversion"))
    conversion = _conversion_status(conversion_data.get("status"))
    stat_rows = (
        ("标签数量", "label_count"),
        ("报告数量", "report_count"),
        ("精确配对", "exact_match_count"),
        ("模糊配对", "fuzzy_match_count"),
        ("解析成功", "succeeded_count"),
        ("解析失败", "failed_count"),
        ("质量标记", "quality_flag_count"),
        ("unresolved 报告", "unresolved_report_count"),
        ("Gold 自评分", "gold_self_score"),
    )
    lines = [
        "# Gate 0 聚合错题本基线",
        "",
        "> 仅保留汇总统计和错误类别；不包含 Gold 原文、报告全文、样本路径或绝对路径。",
        "",
        "## 汇总统计",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
    ]
    for label, key in stat_rows:
        lines.append(f"| {label} | {_display_number(statistics.get(key))} |")
    lines.extend(["", "## 错误类别", "", "| 类别 | 数量 |", "| --- | ---: |"])
    if categories:
        lines.extend(f"| `{name}` | {count} |" for name, count in categories.items())
    else:
        lines.append("| 无 | 0 |")
    lines.extend(["", "## 转换状态", ""])
    if "record_count" in conversion_data:
        lines.extend(
            [
                f"- state records：{_display_number(conversion_data.get('record_count'))}",
                "- success/skipped/failed："
                f"{_display_number(conversion_data.get('success_count'))}/"
                f"{_display_number(conversion_data.get('skipped_count'))}/"
                f"{_display_number(conversion_data.get('failed_count'))}",
                "- target_is_usable（true/false/missing）："
                f"{_display_number(conversion_data.get('target_is_usable_true_count'))}/"
                f"{_display_number(conversion_data.get('target_is_usable_false_count'))}/"
                f"{_display_number(conversion_data.get('target_is_usable_missing_count'))}",
            ]
        )
    if conversion == "incomplete":
        lines.extend(["- 状态：未完成", "- 原因：LibreOffice 缺失导致转换未完成。"])
    elif conversion == "complete":
        lines.append("- 状态：已完成")
    else:
        lines.append("- 状态：未知")
    lines.append("")
    return "\n".join(lines)


def load_json(path: str | Path) -> dict[str, Any]:
    """Load one JSON object for aggregation; the loaded object is not rendered."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return data


def generate_errorbook(
    audit_path: str | Path,
    gold_path: str | Path,
    self_score_path: str | Path,
    output_path: str | Path,
    *,
    conversion_status: str | None = None,
    conversion_state_path: str | Path | None = None,
) -> dict[str, object]:
    """Generate a summary-only Markdown errorbook from three JSON files."""

    summary = aggregate_errorbook(
        load_json(audit_path),
        load_json(gold_path),
        load_json(self_score_path),
        conversion_status=conversion_status,
        conversion_state=(load_json(conversion_state_path) if conversion_state_path else None),
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_errorbook_markdown(summary), encoding="utf-8")
    return summary
