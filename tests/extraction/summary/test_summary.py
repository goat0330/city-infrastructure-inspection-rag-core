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

    assert result.summary.overall_score == "无"
    assert result.summary.overall_grade == "B级"
    missing_fields = {
        flag["details"]["field"]
        for flag in result.quality_flags
        if flag["code"] == MISSING_VALUE
    }
    assert "overall_score" in missing_fields
    assert "superstructure_score" in missing_fields
    assert result.summary.superstructure_grade == "无"


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


def test_report_number_is_not_treated_as_a_date(tmp_path: Path) -> None:
    document = _parse(tmp_path, paragraph("报告编号：bql2012-00121"))

    result = extract_summary(document)

    assert result.summary.report_date == ""


def test_report_date_uses_gold_forms_and_range_end(tmp_path: Path) -> None:
    cases = (
        ("报告日期：2013年10月", "2013年10月"),
        ("报告日期：2013年10月18日", "2013年10月18日"),
        ("示例桥检测报告", "2013年10月"),
        ("报告日期：2013.10", "2013年10月"),
        ("检验日期：2012/3/24～6/12", "2012年6月12日"),
    )
    for text, expected in cases:
        blocks = (paragraph(text),)
        if text == "示例桥检测报告":
            blocks = (paragraph(text), paragraph("二〇一三年十月"))
        result = extract_summary(_parse(tmp_path, *blocks))
        assert result.summary.report_date == expected


def test_report_date_table_label_with_sampling_annotation_uses_adjacent_value(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            _summary_table(
                ("桥梁名称", "日期桥"),
                ("检验日期（Sampling date）", "2012/3/24～6/12"),
            ),
        )
    )

    assert result.summary.report_date == "2012年6月12日"


def test_report_date_normalization_uses_first_explicit_date(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            _summary_table(
                (
                    "报告发出日期",
                    "2012年12月24日，中交第一公路工程局有限公司二〇一三年二月",
                )
            ),
        )
    )

    assert result.summary.report_date == "2012年12月24日"


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


def test_extracts_bci_score_matrix_fields(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        table(
            row(
                cell("部位名称"),
                cell("技术状况指数"),
                cell("技术状况"),
                cell("权重"),
                cell("BCI"),
                cell("桥梁整体技术状况等级"),
            ),
            row(cell("桥面系"), cell("86.00"), cell("B"), cell("0.15"), cell("82.00"), cell("B（良好状态）")),
            row(cell("上部结构"), cell("71.49"), cell("C"), cell("0.40"), cell(""), cell("")),
            row(cell("下部结构"), cell("90.00"), cell("A"), cell("0.45"), cell(""), cell("")),
        ),
    )

    result = extract_summary(document)

    assert result.summary.overall_score == "82.00"
    assert result.summary.overall_grade == "B级"
    assert result.summary.deck_score == "86.00"
    assert result.summary.deck_grade == "B级"
    assert result.summary.superstructure_score == "71.49"
    assert result.summary.superstructure_grade == "C级"
    assert result.summary.substructure_score == "90.00"
    assert result.summary.substructure_grade == "A级"


def test_extracts_bci_phrase_scores_and_grades(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        paragraph("桥面系BCIm=89.00，评定为B级，处于良好状态。"),
        paragraph("上部结构BCIs=79.00，评定为C级。"),
        paragraph("下部结构BCIx=100.00，评定为A级。"),
        paragraph("桥梁BCI=89.95，整体技术状况等级评定为B级，为良好状态。"),
    )

    result = extract_summary(document)

    assert result.summary.deck_score == "89.00"
    assert result.summary.deck_grade == "B级"
    assert result.summary.superstructure_score == "79.00"
    assert result.summary.superstructure_grade == "C级"
    assert result.summary.substructure_score == "100.00"
    assert result.summary.substructure_grade == "A级"
    assert result.summary.overall_score == "89.95"
    assert result.summary.overall_grade == "B级"


def test_bcik_phrase_maps_to_superstructure(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(tmp_path, paragraph("上部结构BCIk=79.00，评定为C级。"))
    )

    assert result.summary.superstructure_score == "79.00"
    assert result.summary.superstructure_grade == "C级"
    assert result.summary.overall_score == "无"


def test_underpass_conclusion_grade_wins_table_conflict(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        table(row(cell("11.人行通道技术状况总评"), cell("二类，较好的状态"))),
        paragraph("根据主要结论，承载能力满足一类技术标准，处于良好状态。"),
    )

    result = extract_summary(document)

    assert result.summary.overall_grade == "一类"


def test_bridge_name_repairs_only_observed_suffixes_and_wrong_paragraph_source(tmp_path: Path) -> None:
    cases = (
        (paragraph("桥梁名称：成渝2#人行天桥"), "成渝2号人行天桥"),
        (paragraph("桥梁名称：老杨公桥异形梁桥"), "老杨公异形桥"),
        (paragraph("桥梁名称：杨公桥互通式立交EC匝道桥"), "杨公桥立交EC匝道桥"),
        (
            table(row(cell("项目名称"), cell("重庆市内环路段杨公桥立交BD2匝道桥外观检查"))),
            "杨公桥立交BD2匝道桥",
        ),
    )
    for block, expected in cases:
        result = extract_summary(_parse(tmp_path, block))
        assert result.summary.bridge_name == expected


def test_bci_phrase_wins_over_misprinted_matrix_grade(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        table(
            row(
                cell("部位名称"),
                cell("技术状况指数"),
                cell("技术状况"),
                cell("权重"),
                cell("BCI"),
                cell("桥梁整体技术状况等级"),
            ),
            row(cell("桥面系"), cell("83.57"), cell("B"), cell("0.15"), cell("85.46"), cell("C（良好状态）")),
        ),
        paragraph("桥梁BCI=85.46，整体技术状况等级评定为B级，为良好状态。"),
    )

    result = extract_summary(document)

    assert result.summary.overall_score == "85.46"
    assert result.summary.overall_grade == "B级"


def test_component_grade_phrase_is_not_taken_as_overall_grade(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        paragraph("BCIx=96.25，故下部结构整体技术状况等级评定为A级。"),
        paragraph("本桥BCI=87.06，综合评定桥梁的整体技术状况等级为B级。"),
    )

    result = extract_summary(document)

    assert result.summary.substructure_score == "96.25"
    assert result.summary.substructure_grade == "A级"
    assert result.summary.overall_score == "87.06"
    assert result.summary.overall_grade == "B级"


def test_previous_scores_default_to_none_when_not_documented(tmp_path: Path) -> None:
    document = _parse(tmp_path, _summary_table(("桥梁名称", "示例桥")))

    result = extract_summary(document)

    assert result.summary.previous_overall_score == "无"
    assert result.summary.previous_overall_grade == "无"


def test_chinese_numeral_cover_date_is_converted(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        paragraph("示例桥检测报告"),
        paragraph("二○一三年二月"),
        paragraph("桥梁名称：示例桥"),
    )

    result = extract_summary(document)

    assert result.summary.report_date == "2013年2月"


def test_chinese_numeral_cover_date_with_tens_month(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        paragraph("示例桥检测报告"),
        paragraph("二○一二年十二月"),
        paragraph("桥梁名称：示例桥"),
    )

    result = extract_summary(document)

    assert result.summary.report_date == "2012年12月"


def test_underpass_grade_from_general_review(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        table(
            row(cell("11.人行通道技术状况总评"), cell("一类，良好的状态")),
        ),
    )

    result = extract_summary(document)

    assert result.summary.overall_grade == "一类"
