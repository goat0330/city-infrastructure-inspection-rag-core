from __future__ import annotations

from pathlib import Path

from src.extraction.summary import CONFLICTING_CANDIDATES, MISSING_VALUE, extract_summary
from src.parsing import parse_docx
from tests.fixtures.word.ooxml_factory import cell, paragraph, row, table, write_docx


def _parse(tmp_path: Path, *blocks: str):
    return parse_docx(write_docx(tmp_path / "summary.docx", *blocks), source_file="summary.docx")


def _summary_table(*rows: tuple[str, str]) -> str:
    body = [row(cell("字段"), cell("内容"))]
    body.extend(row(cell(key), cell(value)) for key, value in rows)
    return table(*body)


def _score_table(*rows: tuple[str, str, str]) -> str:
    body = [row(cell("项目"), cell("评分"), cell("等级"))]
    body.extend(row(cell(name), cell(score), cell(grade)) for name, score, grade in rows)
    return table(*body)


def test_overall_assessment_table_wins_and_keeps_all_score_candidates(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        paragraph("示例桥检测报告"),
        paragraph("总体技术状况评定表"),
        _score_table(
            ("总体", "95.0", "A级"),
            ("上部结构", "88.0", "B级"),
            ("下部结构", "91.0", "A级"),
            ("桥面系", "92.0", "A级"),
        ),
        _summary_table(
            ("桥梁名称", "示例桥"),
            ("桥梁编号", ""),
            ("报告日期", "2013年12月"),
            ("总体评分", "80.0"),
            ("总体等级", "C级"),
        ),
        paragraph("评分章节"),
        _score_table(
            ("总体", "70.0", "D级"),
            ("上部结构", "60.0", "C级"),
            ("下部结构", "61.0", "C级"),
            ("桥面系", "62.0", "C级"),
        ),
        paragraph("检测结论"),
        paragraph("总体评分 70 分，总体技术状况等级为D级。"),
        table(
            row(cell("序号"), cell("建议类别"), cell("建议内容"), cell("部位")),
            row(cell("1"), cell("尽快维修"), cell("修复桥面"), cell("桥面")),
            row(cell("2"), cell("预防性养护"), cell("定期检查"), cell("桥梁")),
        ),
    )

    result = extract_summary(document)

    assert result.summary.bridge_name == "示例桥"
    assert result.summary.bridge_id == ""
    assert result.summary.overall_score == "95.0"
    assert result.summary.overall_grade == "A级"
    assert result.summary.superstructure_score == "88.0"
    assert result.summary.substructure_score == "91.0"
    assert result.summary.deck_score == "92.0"
    assert result.recommendation_count == 2
    assert any(
        candidate.source_kind == "overall_assessment_table"
        and candidate.value == "95.0"
        for candidate in result.candidates["overall_score"]
    )
    assert any(
        candidate.source_kind == "summary_page" and candidate.value == "80.0"
        for candidate in result.candidates["overall_score"]
    )
    assert any(flag["code"] == CONFLICTING_CANDIDATES for flag in result.quality_flags)


def test_missing_values_are_flagged_without_inventing_scores_or_grades(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        _summary_table(("桥梁名称", "缺值桥"), ("总体评分", ""), ("总体等级", "B级")),
    )

    result = extract_summary(document)

    assert result.summary.overall_score == ""
    assert result.summary.overall_grade == "B级"
    missing_fields = {
        flag["details"]["field"]
        for flag in result.quality_flags
        if flag["code"] == MISSING_VALUE
    }
    assert "overall_score" in missing_fields
    assert "superstructure_score" in missing_fields
    assert result.summary.superstructure_grade == ""


def test_report_date_candidates_preserve_cover_sign_and_detection_sources(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        paragraph("封面日期 2013年12月"),
        paragraph("签发日期：2013年12月20日"),
        paragraph("检测日期：2013年11月30日"),
        paragraph("桥梁名称：日期桥"),
    )

    result = extract_summary(document)

    assert result.summary.report_date == "2013年12月"
    assert {candidate.date_kind for candidate in result.report_date_candidates} >= {
        "cover",
        "sign",
        "detection",
    }
    assert {candidate.source_kind for candidate in result.report_date_candidates} >= {
        "cover",
        "sign",
        "detection",
    }
    assert any(flag["code"] == CONFLICTING_CANDIDATES for flag in result.quality_flags)


def test_explicit_empty_official_bridge_id_is_preserved_as_a_candidate(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        _summary_table(("桥梁名称", "无编号桥"), ("桥梁编号", ""), ("报告日期", "2013年12月")),
    )

    result = extract_summary(document)

    assert result.summary.bridge_id == ""
    assert len(result.bridge_id_candidates) == 1
    assert result.bridge_id_candidates[0].value == ""
    assert not any(
        flag["code"] == MISSING_VALUE and flag["details"]["field"] == "bridge_id"
        for flag in result.quality_flags
    )
