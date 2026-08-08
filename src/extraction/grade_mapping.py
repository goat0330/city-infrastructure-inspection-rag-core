"""Grade-mode helpers for platform A/B experiments.

The report remains the authoritative source in the default ``report`` mode.
``generic`` is an explicit experiment that maps an already-extracted score to
A/B/C/D/E without ever deriving or changing the score itself.

V13 adds two guardrails for the generic arm:

* an explicit report grade is preserved when its paired score is missing;
* Chinese ``一类/二类/...`` grades are a different report system and are never
  overwritten by the A/B/C/D/E mapping.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import os
import re
from typing import Mapping

GRADE_MODE_ENV = "GRADE_MODE"
GRADE_MODE = os.getenv(GRADE_MODE_ENV, "report").strip().lower() or "report"
VALID_GRADE_MODES = frozenset({"report", "generic"})

GRADE_SCORE_PAIRS: tuple[tuple[str, str], ...] = (
    ("overall_score", "overall_grade"),
    ("superstructure_score", "superstructure_grade"),
    ("substructure_score", "substructure_grade"),
    ("deck_score", "deck_grade"),
)

_MISSING = {"", "无", "暂无", "不适用", "未提取到", "none", "null", "nan"}
CLASS_GRADE_PATTERN = re.compile(r"[一二三四五六七八九]\s*类")
LETTER_GRADE_PATTERN = re.compile(r"[A-Ea-e]\s*级?")


def normalize_grade_mode(mode: str | None = None) -> str:
    """Return ``report`` or ``generic``; reject accidental third modes."""

    resolved = (
        str(mode).strip().lower()
        if mode is not None
        else os.getenv(GRADE_MODE_ENV, "report").strip().lower()
    ) or "report"
    if resolved not in VALID_GRADE_MODES:
        allowed = ", ".join(sorted(VALID_GRADE_MODES))
        raise ValueError(f"invalid {GRADE_MODE_ENV}={resolved!r}; expected one of: {allowed}")
    return resolved


def _text(value: object) -> str:
    return str(value or "").strip()


def is_missing_grade_value(value: object) -> bool:
    """Return True for the project's explicit missing/display sentinels."""

    return _text(value).casefold() in _MISSING


def is_class_grade(value: object) -> bool:
    """Return True only for the report's Chinese ``X类`` grade system."""

    return CLASS_GRADE_PATTERN.fullmatch(_text(value)) is not None


def _numeric_score(score: object) -> Decimal | None:
    text = _text(score)
    if text.casefold() in _MISSING:
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def grade_from_score(score: float | int | Decimal) -> str | None:
    """Map a numeric score using the platform A/B hypothesis.

    A >= 90, B >= 80, C >= 70, D >= 60, E < 60.
    The function never infers a score from a grade.
    """

    try:
        value = Decimal(str(score))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not value.is_finite():
        return None
    if value >= Decimal("90"):
        return "A级"
    if value >= Decimal("80"):
        return "B级"
    if value >= Decimal("70"):
        return "C级"
    if value >= Decimal("60"):
        return "D级"
    return "E级"


def grade_from_score_text(score: object) -> str | None:
    """Return a generic grade only for an explicit numeric score value."""

    value = _numeric_score(score)
    return grade_from_score(value) if value is not None else None


def should_apply_generic(score: object, report_grade: object) -> bool:
    """Return whether the A/B/C/D/E mapping may replace/fill this grade.

    Generic mapping is allowed only when the paired score is an explicit
    numeric report fact.  A Chinese ``X类`` report grade is a separate grading
    system and is always preserved.  Missing report grades may be filled when
    a score exists; missing scores always preserve the report grade.
    """

    if _numeric_score(score) is None:
        return False
    if is_class_grade(report_grade):
        return False
    return is_missing_grade_value(report_grade) or LETTER_GRADE_PATTERN.fullmatch(_text(report_grade)) is not None


def generic_change_kind(score: object, report_grade: object) -> str:
    """Classify the generic-arm decision for audit provenance."""

    if _numeric_score(score) is None or is_class_grade(report_grade):
        return "preserved"
    if is_missing_grade_value(report_grade):
        return "filled"
    return "mapped"


def apply_grade_mode(values: Mapping[str, object], *, mode: str | None = None) -> dict[str, object]:
    """Return a copy with only eligible grade fields changed in generic mode.

    V13 deliberately preserves explicit report grades when a score is missing
    and preserves every Chinese ``X类`` grade even if a numeric score exists.
    No grade is ever used to derive or modify a score.
    """

    resolved = normalize_grade_mode(mode)
    result = dict(values)
    if resolved == "report":
        return result

    for score_field, grade_field in GRADE_SCORE_PAIRS:
        score = result.get(score_field)
        report_grade = result.get(grade_field)
        if not should_apply_generic(score, report_grade):
            continue
        mapped = grade_from_score_text(score)
        if mapped is not None:
            result[grade_field] = mapped
    return result
