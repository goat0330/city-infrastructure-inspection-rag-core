"""Summary-only errorbook rendering for the B2 benchmark.

The B2 errorbook is derived exclusively from :func:`diagnose_records` output
and a caller-supplied commit/config label.  It never serializes Gold field
values, report text, absolute paths, or ``raw_text``; only fixed aggregate
statistics, per-section micro metrics, and deterministic quality-flag codes
are emitted.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping


_SECTION_ORDER = ("summary", "defects", "recommendations")
_SECTION_LABELS = {
    "summary": "summary",
    "defects": "defects",
    "recommendations": "recommendations",
}

_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\s,;]+")
_COMMIT_RE = re.compile(r"[0-9A-Za-z_.-]{1,64}")


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> str:
    parsed = _as_float(value)
    if parsed is None:
        return "—"
    text = f"{parsed:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _safe_note(value: Any) -> str:
    text = " ".join(str(value).split())
    return _PATH_RE.sub("[path-redacted]", text) if _PATH_RE.search(text) else text


def _safe_commit(value: Any) -> str:
    text = " ".join(str(value).split())
    if not _COMMIT_RE.fullmatch(text):
        return "unknown"
    return text


def b2_errorbook_summary(
    diagnostics: Mapping[str, Any],
    *,
    commit: str,
    config: str,
) -> dict[str, Any]:
    """Project diagnostics onto fixed, privacy-safe errorbook statistics."""

    micro = diagnostics.get("micro") if isinstance(diagnostics.get("micro"), Mapping) else {}
    sections: dict[str, dict[str, float | None]] = {}
    for section in _SECTION_ORDER:
        view = micro.get(section) if isinstance(micro, Mapping) else {}
        if not isinstance(view, Mapping):
            view = {}
        sections[section] = {
            key: (_as_float(view.get(key)) if key in view else None)
            for key in ("precision", "recall", "f1", "score")
        }
    flags = diagnostics.get("quality_flags", [])
    if not isinstance(flags, list):
        flags = []
    safe_flags: list[dict[str, Any]] = []
    for flag in flags:
        if not isinstance(flag, Mapping):
            continue
        code = _safe_note(flag.get("code"))
        count = _as_float(flag.get("count", 0))
        if code and count is not None:
            safe_flags.append({"code": code, "count": count})
    return {
        "commit": _safe_commit(commit),
        "config": _safe_note(config),
        "prediction_records": int(diagnostics.get("record_count", 0) or 0),
        "failed_documents": int(diagnostics.get("failed_documents", 0) or 0),
        "weighted_total": _as_float(diagnostics.get("weighted_total")),
        "sections": sections,
        "quality_flags": safe_flags,
    }


def render_b2_errorbook_markdown(summary: Mapping[str, Any]) -> str:
    """Render only fixed statistics and deterministic quality-flag codes."""

    sections = summary.get("sections") if isinstance(summary.get("sections"), Mapping) else {}
    flags = summary.get("quality_flags") if isinstance(summary.get("quality_flags"), list) else []
    lines = [
        "# B2 夜间 benchmark 错题本",
        "",
        "> 仅保留汇总指标、分段指标和固定质量标记；不包含 Gold 原文、报告全文、样本路径或绝对路径。",
        "",
        f"- commit: `{_safe_commit(summary.get('commit'))}`",
        f"- config: `{_safe_note(summary.get('config'))}`",
        f"- prediction records: {_number(summary.get('prediction_records'))}",
        f"- failed documents: {_number(summary.get('failed_documents'))}",
        f"- internal micro total: {_number(summary.get('weighted_total'))}",
        "",
        "## B2 分段",
        "",
        "| section | precision | recall | F1 | score |",
        "|---|---:|---:|---:|---:|",
    ]
    for section in _SECTION_ORDER:
        view = sections.get(section) if isinstance(sections, Mapping) else {}
        if not isinstance(view, Mapping):
            view = {}
        lines.append(
            "| {label} | {precision} | {recall} | {f1} | {score} |".format(
                label=_SECTION_LABELS.get(section, section),
                precision=_number(view.get("precision")),
                recall=_number(view.get("recall")),
                f1=_number(view.get("f1")),
                score=_number(view.get("score")),
            )
        )
    lines.extend(["", "## 质量标记", "", "| code | count |", "|---|---:|"])
    if flags:
        for flag in flags:
            lines.append(f"| `{_safe_note(flag.get('code'))}` | {_number(flag.get('count'))} |")
    else:
        lines.append("| 无 | 0 |")
    lines.append("")
    return "\n".join(lines)


def write_b2_errorbook(
    diagnostics: Mapping[str, Any],
    output_path: str | Path,
    *,
    commit: str,
    config: str,
) -> dict[str, Any]:
    """Write a summary-only Markdown errorbook from diagnostics output."""

    summary = b2_errorbook_summary(diagnostics, commit=commit, config=config)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_b2_errorbook_markdown(summary), encoding="utf-8")
    return summary


def load_json(path: str | Path) -> dict[str, Any]:
    """Load one JSON object; used by callers that read diagnostics back in."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return data
