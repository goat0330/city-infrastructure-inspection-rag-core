from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from src.contracts import SourceAnchor


SCRIPT = Path(__file__).parents[2] / "scripts" / "audit_structured_fields_v11.py"
spec = importlib.util.spec_from_file_location("audit_structured_fields_v11", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _candidate(field: str, value: str, source_kind: str, raw_text: str, label: str = ""):
    return SimpleNamespace(
        field=field,
        value=value,
        source_kind=source_kind,
        label=label,
        date_kind=None,
        priority=600 if source_kind == "facility_name" else 500,
        source=SourceAnchor(source_file="sample.docx", block_index=1, raw_text=raw_text),
    )


def test_bridge_name_many_candidates_is_resolved_by_authoritative_anchor() -> None:
    selected = _candidate("bridge_name", "太平水库大桥", "facility_name", "太平水库大桥", "工程名称")
    candidates = (
        selected,
        _candidate("bridge_name", "太平水库大桥 跨越：/", "facility_name", "桥梁名称：太平水库大桥 跨越：/", "桥梁名称"),
        _candidate("bridge_name", "公路桥", "body_name", "公路桥涵设计通用规范", "设施名"),
    )

    state, conflict, resolved, distinct = module._audit_state(
        "bridge_name", "太平水库大桥", candidates, selected, renderer_match=True
    )

    assert state == "extracted"
    assert conflict is False
    assert resolved is True
    assert distinct >= 2


def test_numeric_spelling_does_not_create_score_conflict() -> None:
    selected = _candidate("superstructure_score", "86.10", "bci", "上部结构BCIs=86.10")
    candidates = (
        selected,
        _candidate("superstructure_score", "86.1", "section_score_table", "86.1"),
    )

    state, conflict, resolved, distinct = module._audit_state(
        "superstructure_score", "86.10", candidates, selected, renderer_match=True
    )

    assert state == "extracted"
    assert conflict is False
    assert distinct == 1


def test_unresolved_same_field_conflict_stays_ambiguous() -> None:
    selected = _candidate("trend", "新增裂缝", "paragraph", "新增裂缝")
    candidates = (
        selected,
        _candidate("trend", "病害无明显发展", "paragraph", "病害无明显发展"),
    )

    state, conflict, resolved, distinct = module._audit_state(
        "trend", "新增裂缝", candidates, selected, renderer_match=True
    )

    assert state == "ambiguous"
    assert conflict is True
    assert resolved is False
    assert distinct == 2


def test_prediction_baseline_diff_reports_field_level_changes(tmp_path: Path) -> None:
    baseline = tmp_path / "prediction.jsonl"
    baseline.write_text(
        json.dumps(
            {
                "sample_id": "一处-测试桥报告",
                "source_file": "一处-测试桥报告.docx",
                "summary": {"overall_score": "88.00", "overall_grade": "B级"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    rows = [
        {
            "sample_id": "一处-测试桥报告",
            "filename": "一处-测试桥报告.docx",
            "field": "overall_score",
            "value": "89.00",
            "state": "extracted",
            "source_kind": "overall_assessment_table",
            "anchor": {"raw_text": "89.00"},
        },
        {
            "sample_id": "一处-测试桥报告",
            "filename": "一处-测试桥报告.docx",
            "field": "overall_grade",
            "value": "B级",
            "state": "extracted",
            "source_kind": "bci",
            "anchor": {"raw_text": "B级"},
        },
    ]

    payload = module._compare_prediction_baseline(
        baseline, rows, baseline_label="v8", current_label="v11"
    )

    assert payload["matched_sample_count"] == 1
    assert payload["changed_count"] == 1
    assert payload["changed_by_field"] == {"overall_score": 1}
