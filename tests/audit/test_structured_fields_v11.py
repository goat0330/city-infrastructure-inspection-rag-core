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


def test_grade_mode_diff_reports_only_changed_grade_fields() -> None:
    report = SimpleNamespace(
        summary=SimpleNamespace(
            overall_score="89.46",
            overall_grade="B级",
            superstructure_score="86.10",
            superstructure_grade="D级",
            substructure_score="92.00",
            substructure_grade="B级",
            deck_score="85.75",
            deck_grade="D级",
        )
    )
    generic = SimpleNamespace(
        summary=SimpleNamespace(
            overall_score="89.46",
            overall_grade="B级",
            superstructure_score="86.10",
            superstructure_grade="B级",
            substructure_score="92.00",
            substructure_grade="A级",
            deck_score="85.75",
            deck_grade="B级",
        )
    )

    decisions = module._grade_mode_differences(
        "太平水库大桥", "太平水库大桥.docx", report, generic
    )

    changed = [item for item in decisions if item["changed"]]
    assert {item["field"] for item in changed} == {
        "superstructure_grade", "substructure_grade", "deck_grade"
    }
    assert all(item["score"] for item in decisions)
    payload = module._grade_mode_diff_payload(decisions, input_count=1)
    assert payload["decision_count"] == 4
    assert payload["changed_count"] == 3
    assert payload["changed_sample_count"] == 1
    assert payload["platform_score_verified"] is False


def test_generic_grade_audit_uses_score_anchor_not_report_grade_anchor() -> None:
    selected_score = _candidate(
        "superstructure_score", "86.10", "bci", "上部结构BCIs=86.10，评定为D级"
    )
    report_grade = _candidate(
        "superstructure_grade", "D级", "bci", "上部结构BCIs=86.10，评定为D级"
    )
    summary = SimpleNamespace(
        summary=SimpleNamespace(superstructure_score="86.10", superstructure_grade="B级"),
        candidates={
            "superstructure_score": (selected_score,),
            "superstructure_grade": (report_grade,),
        },
        facility_context=SimpleNamespace(facility_type_raw="桥梁", facility_type="bridge"),
    )
    submission = SimpleNamespace(scalars={"superstructure_grade": "B级"})
    rendered = {"superstructure_grade": "B级"}
    fake_path = Path("/tmp/root/sample.docx")
    fake_root = Path("/tmp/root")
    document = SimpleNamespace(blocks=())

    row = module._record_for_field(
        fake_path,
        fake_root,
        document,
        (),
        summary,
        submission,
        rendered,
        "superstructure_grade",
        grade_mode="generic",
    )

    assert row["state"] == "extracted"
    assert row["source_kind"] == "generic_grade_mapping"
    assert row["derived_from_score"] == "superstructure_score"
    assert row["renderer_match"] is True


def test_grade_mode_change_kind_mapped_filled_preserved() -> None:
    report = SimpleNamespace(
        summary=SimpleNamespace(
            overall_score="86.10", overall_grade="D级",
            superstructure_score="86.10", superstructure_grade="无",
            substructure_score="95.39", substructure_grade="二类",
            deck_score="无", deck_grade="B级",
        )
    )
    generic = SimpleNamespace(
        summary=SimpleNamespace(
            overall_score="86.10", overall_grade="B级",
            superstructure_score="86.10", superstructure_grade="B级",
            substructure_score="95.39", substructure_grade="二类",
            deck_score="无", deck_grade="B级",
        )
    )
    decisions = module._grade_mode_differences("sample", "sample.docx", report, generic)
    kinds = {item["field"]: item["change_kind"] for item in decisions}
    assert kinds["overall_grade"] == "mapped"
    assert kinds["superstructure_grade"] == "filled"
    assert kinds["substructure_grade"] == "preserved"
    assert kinds["deck_grade"] == "preserved"


def test_generic_preserved_class_grade_keeps_report_anchor() -> None:
    report_grade = _candidate(
        "overall_grade", "二类", "section_score_table", "综合评定分数Dr=70.4\t二类"
    )
    score = _candidate(
        "overall_score", "70.4", "section_score_table", "综合评定分数Dr=70.4"
    )
    summary = SimpleNamespace(
        summary=SimpleNamespace(overall_score="70.4", overall_grade="二类"),
        candidates={"overall_score": (score,), "overall_grade": (report_grade,)},
        facility_context=SimpleNamespace(facility_type_raw="大桥", facility_type="bridge"),
    )
    submission = SimpleNamespace(scalars={"overall_grade": "二类"})
    rendered = {"overall_grade": "二类"}
    row = module._record_for_field(
        Path("/tmp/root/sample.docx"), Path("/tmp/root"), SimpleNamespace(blocks=()), (),
        summary, submission, rendered, "overall_grade", grade_mode="generic",
    )
    assert row["change_kind"] == "preserved"
    assert row["source_kind"] == "section_score_table"
    assert row["derived_from_score"] == ""


def test_paired_score_grade_audit_records_counterpart_and_rejected_candidate() -> None:
    source = SourceAnchor(source_file="sample.docx", block_index=8, raw_text="BSIs=81.36（B级）")
    paired_score = SimpleNamespace(
        field="superstructure_score", value="81.36", source_kind="paired_score_grade",
        label="BSIs评分等级配对", date_kind=None, priority=820, source=source,
    )
    old_score = _candidate("superstructure_score", "97.80", "bci", "上部结构BCIs=97.80")
    paired_grade = SimpleNamespace(
        field="superstructure_grade", value="B级", source_kind="paired_score_grade",
        label="BSIs评分等级配对", date_kind=None, priority=820, source=source,
    )
    summary = SimpleNamespace(
        summary=SimpleNamespace(superstructure_score="81.36", superstructure_grade="B级"),
        candidates={
            "superstructure_score": (paired_score, old_score),
            "superstructure_grade": (paired_grade,),
        },
        facility_context=SimpleNamespace(facility_type_raw="桥梁", facility_type="bridge"),
    )
    submission = SimpleNamespace(scalars={"superstructure_score": "81.36"})
    rendered = {"superstructure_score": "81.36"}
    row = module._record_for_field(
        Path("/tmp/root/sample.docx"), Path("/tmp/root"), SimpleNamespace(blocks=()), (),
        summary, submission, rendered, "superstructure_score", grade_mode="report",
    )

    assert row["source_kind"] == "paired_score_grade"
    assert row["paired_grade"] == "B级"
    assert row["bsi_kind"].lower() == "bsis"
    assert row["selection_reason"] == "paired_final_assessment_preferred"
    assert any(item["value"] == "97.80" for item in row["rejected_candidates"])


def test_bsi_only_component_fact_is_audit_evidence_not_selected() -> None:
    source = SourceAnchor(source_file="sample.docx", block_index=8, raw_text="BSIs=78.83（C级）")
    bsi_score = SimpleNamespace(
        field="superstructure_score", value="78.83", source_kind="paired_score_grade",
        label="BSIs评分等级配对", date_kind=None, priority=820, source=source,
    )
    summary = SimpleNamespace(
        summary=SimpleNamespace(superstructure_score="无", superstructure_grade="无"),
        candidates={"superstructure_score": (bsi_score,), "superstructure_grade": ()},
        facility_context=SimpleNamespace(facility_type_raw="桥梁", facility_type="bridge"),
    )
    submission = SimpleNamespace(scalars={"superstructure_score": "无"})
    rendered = {"superstructure_score": "无"}
    row = module._record_for_field(
        Path("/tmp/root/sample.docx"), Path("/tmp/root"), SimpleNamespace(blocks=()), (),
        summary, submission, rendered, "superstructure_score", grade_mode="report",
    )
    assert row["source_kind"] == ""
    assert row["bsi_only_not_mapped"] is True
    assert row["bsi_selection_reason"] == "bsi_only_not_mapped"
    assert row["bsi_evidence"][0]["value"] == "78.83"


def test_bci_selected_component_keeps_bsi_as_rejected_audit_evidence() -> None:
    bci_source = SourceAnchor(source_file="sample.docx", block_index=7, raw_text="BCIs=86.91")
    bsi_source = SourceAnchor(source_file="sample.docx", block_index=8, raw_text="BSIs=78.83（C级）")
    bci_score = SimpleNamespace(
        field="superstructure_score", value="86.91", source_kind="bci",
        label="BCI指数", date_kind=None, priority=500, source=bci_source,
    )
    bsi_score = SimpleNamespace(
        field="superstructure_score", value="78.83", source_kind="paired_score_grade",
        label="BSIs评分等级配对", date_kind=None, priority=820, source=bsi_source,
    )
    summary = SimpleNamespace(
        summary=SimpleNamespace(superstructure_score="86.91", superstructure_grade="无"),
        candidates={"superstructure_score": (bsi_score, bci_score), "superstructure_grade": ()},
        facility_context=SimpleNamespace(facility_type_raw="桥梁", facility_type="bridge"),
    )
    submission = SimpleNamespace(scalars={"superstructure_score": "86.91"})
    rendered = {"superstructure_score": "86.91"}
    row = module._record_for_field(
        Path("/tmp/root/sample.docx"), Path("/tmp/root"), SimpleNamespace(blocks=()), (),
        summary, submission, rendered, "superstructure_score", grade_mode="report",
    )
    assert row["source_kind"] == "bci"
    assert row["bsi_only_not_mapped"] is False
    assert row["bsi_selection_reason"] == "bci_primary_bsi_not_mapped"
    assert row["bsi_evidence"][0]["value"] == "78.83"
