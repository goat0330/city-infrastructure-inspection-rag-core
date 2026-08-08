from __future__ import annotations

import pytest

from src.extraction.grade_mapping import (
    apply_grade_mode,
    generic_change_kind,
    grade_from_score,
    normalize_grade_mode,
    should_apply_generic,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    (
        (90, "A级"),
        (89.99, "B级"),
        (80, "B级"),
        (79.99, "C级"),
        (70, "C级"),
        (60, "D级"),
        (59.99, "E级"),
    ),
)
def test_grade_from_score_boundaries(score: float, expected: str) -> None:
    assert grade_from_score(score) == expected


def test_generic_mode_overrides_report_grade() -> None:
    result = apply_grade_mode(
        {"superstructure_score": "86.10", "superstructure_grade": "D级"},
        mode="generic",
    )
    assert result["superstructure_grade"] == "B级"


def test_report_mode_keeps_extracted_grade() -> None:
    result = apply_grade_mode(
        {"superstructure_score": "86.10", "superstructure_grade": "D级"},
        mode="report",
    )
    assert result["superstructure_grade"] == "D级"


def test_generic_score_missing_keeps_report_grade() -> None:
    result = apply_grade_mode(
        {"superstructure_score": "无", "superstructure_grade": "B级"},
        mode="generic",
    )
    assert result["superstructure_grade"] == "B级"
    assert should_apply_generic("无", "B级") is False
    assert generic_change_kind("无", "B级") == "preserved"


def test_generic_class_system_keeps_report_grade() -> None:
    result = apply_grade_mode(
        {"overall_score": "95.39", "overall_grade": "二类"},
        mode="generic",
    )
    assert result["overall_grade"] == "二类"
    assert should_apply_generic("95.39", "二类") is False
    assert generic_change_kind("95.39", "二类") == "preserved"


def test_generic_no_report_grade_fills() -> None:
    result = apply_grade_mode(
        {"superstructure_score": "86.10", "superstructure_grade": "无"},
        mode="generic",
    )
    assert result["superstructure_grade"] == "B级"
    assert generic_change_kind("86.10", "无") == "filled"


def test_four_groups_all_affected() -> None:
    result = apply_grade_mode(
        {
            "overall_score": "91.0",
            "overall_grade": "B级",
            "superstructure_score": "86.10",
            "superstructure_grade": "D级",
            "substructure_score": "75",
            "substructure_grade": "B级",
            "deck_score": "59.9",
            "deck_grade": "A级",
        },
        mode="generic",
    )
    assert result["overall_grade"] == "A级"
    assert result["superstructure_grade"] == "B级"
    assert result["substructure_grade"] == "C级"
    assert result["deck_grade"] == "E级"


def test_environment_mode_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADE_MODE", "generic")
    assert normalize_grade_mode() == "generic"
    monkeypatch.setenv("GRADE_MODE", "report")
    assert normalize_grade_mode() == "report"


def test_invalid_mode_fails_fast() -> None:
    with pytest.raises(ValueError):
        normalize_grade_mode("other")
