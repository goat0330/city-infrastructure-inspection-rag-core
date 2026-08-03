"""A deterministic, privacy-safe CSV leaderboard for B2 experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


LEADERBOARD_FIELDS = (
    "commit",
    "config",
    "summary_score",
    "defect_precision",
    "defect_recall",
    "defect_f1",
    "recommendation_f1",
    "weighted_total",
    "runtime",
    "failed_documents",
    "notes",
)
LEADERBOARD_COLUMNS = LEADERBOARD_FIELDS

LEADERBOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(LEADERBOARD_FIELDS),
    "properties": {
        "commit": {"type": "string"},
        "config": {"type": "string"},
        "summary_score": {"type": "number"},
        "defect_precision": {"type": "number", "minimum": 0, "maximum": 1},
        "defect_recall": {"type": "number", "minimum": 0, "maximum": 1},
        "defect_f1": {"type": "number", "minimum": 0, "maximum": 1},
        "recommendation_f1": {"type": "number", "minimum": 0, "maximum": 1},
        "weighted_total": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
        "runtime": {"type": "number", "minimum": 0},
        "failed_documents": {"type": "integer", "minimum": 0},
        "notes": {"type": "string"},
    },
}

_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\s,;]+")
_FLOAT_FIELDS = (
    "summary_score",
    "defect_precision",
    "defect_recall",
    "defect_f1",
    "recommendation_f1",
    "weighted_total",
    "runtime",
)


def _safe_note(value: Any) -> str:
    text = " ".join(str(value).split())
    return _PATH_RE.sub("[path-redacted]", text) if _PATH_RE.search(text) else text


def _number(value: Any, field: str, *, nullable: bool = False) -> float | None:
    if nullable and (value is None or (isinstance(value, str) and not value.strip())):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    if parsed < 0:
        raise ValueError(f"{field} must be non-negative")
    return round(parsed, 6)


@dataclass(frozen=True)
class LeaderboardEntry:
    commit: str
    config: str
    summary_score: float
    defect_precision: float
    defect_recall: float
    defect_f1: float
    recommendation_f1: float
    weighted_total: float | None
    runtime: float
    failed_documents: int
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return normalize_leaderboard_entry(asdict(self))


def normalize_leaderboard_entry(entry: Mapping[str, Any] | LeaderboardEntry) -> dict[str, Any]:
    """Validate and normalize one row to the fixed leaderboard schema."""

    source = asdict(entry) if isinstance(entry, LeaderboardEntry) else dict(entry)
    missing = [field for field in LEADERBOARD_FIELDS if field not in source]
    if missing:
        raise ValueError(f"missing leaderboard fields: {', '.join(missing)}")
    extra = sorted(set(source) - set(LEADERBOARD_FIELDS))
    if extra:
        raise ValueError(f"unknown leaderboard fields: {', '.join(extra)}")
    result: dict[str, Any] = {
        "commit": _safe_note(source["commit"]),
        "config": _safe_note(source["config"]),
        "notes": _safe_note(source["notes"]),
    }
    if not result["commit"] or not result["config"]:
        raise ValueError("commit and config must be non-empty")
    for field in _FLOAT_FIELDS:
        result[field] = _number(source[field], field, nullable=field == "weighted_total")
    for field in ("defect_precision", "defect_recall", "defect_f1", "recommendation_f1"):
        if result[field] is not None and result[field] > 1:
            raise ValueError(f"{field} must be between 0 and 1")
    if result["summary_score"] is not None and result["summary_score"] > 100:
        raise ValueError("summary_score must be between 0 and 100")
    if result["weighted_total"] is not None and result["weighted_total"] > 100:
        raise ValueError("weighted_total must be between 0 and 100")
    failed = source["failed_documents"]
    if isinstance(failed, bool):
        raise ValueError("failed_documents must be an integer")
    try:
        failed_int = int(failed)
    except (TypeError, ValueError) as error:
        raise ValueError("failed_documents must be an integer") from error
    if isinstance(failed, str):
        if not failed.strip().isdigit() or failed_int < 0:
            raise ValueError("failed_documents must be a non-negative integer")
    elif isinstance(failed, float) and not failed.is_integer():
        raise ValueError("failed_documents must be a non-negative integer")
    elif failed_int < 0:
        raise ValueError("failed_documents must be a non-negative integer")
    result["failed_documents"] = failed_int
    return {field: result[field] for field in LEADERBOARD_FIELDS}


def sort_leaderboard(entries: Iterable[Mapping[str, Any] | LeaderboardEntry]) -> list[dict[str, Any]]:
    """Normalize and deterministically rank rows by quality, then identity."""

    rows = [normalize_leaderboard_entry(entry) for entry in entries]
    return sorted(
        rows,
        key=lambda row: (
            -(row["weighted_total"] if row["weighted_total"] is not None else float("-inf")),
            -row["defect_f1"],
            -row["recommendation_f1"],
            -row["summary_score"],
            row["commit"],
            row["config"],
        ),
    )


def load_leaderboard(path: str | Path) -> list[dict[str, Any]]:
    """Read and validate a leaderboard CSV."""

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != LEADERBOARD_FIELDS:
            raise ValueError("leaderboard CSV header does not match the fixed schema")
        return [normalize_leaderboard_entry(row) for row in reader]


def write_leaderboard(path: str | Path, entries: Iterable[Mapping[str, Any] | LeaderboardEntry]) -> list[dict[str, Any]]:
    """Write sorted rows with a stable header and numeric representation."""

    rows = sort_leaderboard(entries)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEADERBOARD_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def append_leaderboard_entry(path: str | Path, entry: Mapping[str, Any] | LeaderboardEntry) -> list[dict[str, Any]]:
    """Add one row and rewrite the CSV in deterministic ranking order."""

    destination = Path(path)
    existing = load_leaderboard(destination) if destination.exists() else []
    return write_leaderboard(destination, [*existing, entry])


read_leaderboard = load_leaderboard
save_leaderboard = write_leaderboard


def entry_from_diagnostics(
    diagnostics: Mapping[str, Any],
    *,
    commit: str,
    config: str,
    runtime: float = 0.0,
    notes: str = "",
) -> dict[str, Any]:
    """Create a leaderboard row from :func:`diagnose_records` output."""

    micro = diagnostics.get("micro") if isinstance(diagnostics.get("micro"), Mapping) else {}
    if not micro and isinstance(diagnostics.get("sections"), Mapping):
        micro = diagnostics["sections"]
    summary = micro.get("summary") if isinstance(micro.get("summary"), Mapping) else {}
    defects = micro.get("defects") if isinstance(micro.get("defects"), Mapping) else {}
    recommendations = micro.get("recommendations") if isinstance(micro.get("recommendations"), Mapping) else {}
    row = {
        "commit": commit,
        "config": config,
        "summary_score": diagnostics.get("summary_score", summary.get("score", 0.0)),
        "defect_precision": diagnostics.get("defect_precision", defects.get("precision", 0.0)),
        "defect_recall": diagnostics.get("defect_recall", defects.get("recall", 0.0)),
        "defect_f1": diagnostics.get("defect_f1", defects.get("f1", 0.0)),
        "recommendation_f1": diagnostics.get("recommendation_f1", recommendations.get("f1", 0.0)),
        "weighted_total": diagnostics.get(
            "weighted_total",
            diagnostics.get("micro_total_score", micro.get("weighted_total")),
        ),
        "runtime": runtime,
        "failed_documents": diagnostics.get("failed_documents", 0),
        "notes": notes,
    }
    return normalize_leaderboard_entry(row)


# A concise alias reads naturally at call sites that build experiment rows.
build_leaderboard_entry = entry_from_diagnostics
