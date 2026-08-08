from __future__ import annotations

from pathlib import Path

import pytest

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
    }
    assert {candidate.source_kind for candidate in result.report_date_candidates} >= {
        "cover",
        "sign",
    }
    assert result.facility_context.inspection_date == "2013年11月30日"
    assert {candidate.date_kind for candidate in result.inspection_date_candidates} == {"detection"}
    assert not any(candidate.date_kind in {"detection", "detection_end"} for candidate in result.report_date_candidates)
    assert any(flag["code"] == CONFLICTING_CANDIDATES for flag in result.quality_flags)


def test_report_number_is_not_treated_as_a_date(tmp_path: Path) -> None:
    document = _parse(tmp_path, paragraph("报告编号：bql2012-00121"))

    result = extract_summary(document)

    assert result.summary.report_date == ""


def test_report_date_uses_gold_forms_and_range_end(tmp_path: Path) -> None:
    cases = (
        ("报告日期：2013年10月", "2013年10月", ""),
        ("报告日期：2013年10月18日", "2013年10月18日", ""),
        ("示例桥检测报告", "2013年10月", ""),
        ("报告日期：2013.10", "2013年10月", ""),
        ("检验日期：2012/3/24～6/12", "", "2012年6月12日"),
    )
    for text, report_expected, inspection_expected in cases:
        blocks = (paragraph(text),)
        if text == "示例桥检测报告":
            blocks = (paragraph(text), paragraph("二〇一三年十月"))
        result = extract_summary(_parse(tmp_path, *blocks))
        assert result.summary.report_date == report_expected
        assert result.facility_context.inspection_date == inspection_expected


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
    assert result.facility_context.inspection_date == "2012年6月12日"


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


def test_dafosi_class_assessment_pairs_selected_grade_with_explicit_dr_score(tmp_path: Path) -> None:
    def assessment_table(score: str) -> str:
        return table(
            row(cell("里程桩号"), cell("K3+720"), cell("桥梁名称"), cell("大佛寺长江大桥")),
            row(cell("项目"), cell("权重"), cell("缺损程度标度"), cell("最终评定标度")),
            row(cell("综合评定分数Dr"), cell("等级"), cell(f"二类（{score}）")),
        )

    result = extract_summary(
        _parse(
            tmp_path,
            paragraph("2019年度桥梁等结构设施定期检测"),
            paragraph("大佛寺长江大桥"),
            _summary_table(
                ("项目名称", "2019年度桥梁等结构设施定期检测"),
                ("检测时间", "2019年9月至10月"),
                ("检测结束日期", "2019年11月20日"),
            ),
            paragraph("表7.1 大佛寺长江大桥技术状况评定表（主桥）"),
            assessment_table("74.0"),
            paragraph("表7.2 大佛寺长江大桥技术状况评定表（引桥）"),
            assessment_table("72.6"),
        )
    )

    assert result.summary.bridge_name == "大佛寺长江大桥"
    assert result.summary.report_date == ""
    assert result.facility_context.inspection_date == "2019年11月20日"
    assert result.summary.overall_score == "74.0"
    assert {candidate.value for candidate in result.candidates["overall_score"]} == {
        "74.0",
        "72.6",
    }
    assert {candidate.label for candidate in result.candidates["overall_score"]} >= {
        "主桥综合评定分数Dr",
        "引桥综合评定分数Dr",
    }


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


def test_pedestrian_underpass_context_preserves_body_name_and_date_pools(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph("杨公桥EC匝道人行地通道"),
            paragraph("检测评估报告"),
            paragraph("二〇一三年二月"),
            paragraph("地通道名称：杨公桥EC匝道人行通道"),
            _summary_table(
                ("人行通道名称", "杨公桥EC匝道人行通道"),
                ("检查时间", "2012.6.20"),
            ),
            paragraph("主要结论：外观检测发现侧墙局部破损，建议及时修复。"),
            paragraph("综合评估：承载能力满足一类技术标准，处于良好状态。"),
        )
    )

    assert result.summary.bridge_name == "杨公桥EC匝道人行通道"
    assert result.summary.report_date == "2013年2月"
    assert result.facility_context.facility_name == "杨公桥EC匝道人行通道"
    assert result.facility_context.facility_type_raw == "人行通道"
    assert result.facility_context.inspection_date == "2012年6月20日"
    assert result.summary.report_date != result.facility_context.inspection_date
    assert result.summary.overall_grade == "一类"
    assert result.summary.overall_conclusion.startswith("外观检测发现")
    assert result.field_states["report_date"] == "present"
    assert result.field_states["inspection_date"] == "present"
    assert set(result.field_states.values()) <= {
        "present",
        "explicit_none",
        "not_applicable",
        "not_extracted",
    }


def test_conclusion_fragments_are_excluded_and_risk_fallback_is_limited(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph("检测结论：条石抗压强度检测结果为18MPa。"),
            paragraph("主要结论：侧墙破损，建议及时修复。"),
            paragraph("安全影响：侧墙破损会影响耐久性，但不应把整段安全影响当作主要风险。"),
            paragraph("处置建议：对侧墙破损进行修复。"),
        )
    )

    assert result.summary.overall_conclusion == "侧墙破损"
    assert all(
        "抗压强度" not in candidate.value
        for candidate in result.candidates["overall_conclusion"]
    )
    assert result.summary.risk_points == "侧墙破损会影响耐久性,但不应把整段安全影响当作主要风险"
    assert len(result.candidates["risk_points"]) <= 3


def test_history_window_stops_at_any_chapter_two_heading_and_keeps_equal_scores(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph("1.2 上一次检测状况"),
            paragraph("上次检测桥梁BCI=85.46，总体等级为B级。"),
            paragraph("2 桥梁概况"),
            paragraph("本桥BCI=85.46，整体技术状况等级评定为B级。"),
        )
    )

    assert result.summary.previous_overall_score == "85.46"
    assert result.summary.previous_overall_grade == "B级"
    assert result.summary.overall_score == "85.46"
    assert result.summary.overall_grade == "B级"
    assert any(c.source_kind == "previous_detection" for c in result.candidates["previous_overall_score"])
    assert all(c.source_kind != "previous_detection" for c in result.candidates["overall_score"])


def test_score_matrix_uses_explicit_component_label_not_first_serial_cell(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            table(
                row(cell("序号"), cell("部位名称"), cell("评分"), cell("等级")),
                row(cell("1"), cell("上部结构"), cell("78.50"), cell("C级")),
                row(cell("2"), cell("下部结构"), cell("91.20"), cell("A级")),
                row(cell("3"), cell("桥面系"), cell("88.40"), cell("B级")),
                row(cell("4"), cell("总体"), cell("84.60"), cell("B级")),
            ),
        )
    )

    assert result.summary.superstructure_score == "78.50"
    assert result.summary.superstructure_grade == "C级"
    assert result.summary.substructure_score == "91.20"
    assert result.summary.substructure_grade == "A级"
    assert result.summary.deck_score == "88.40"
    assert result.summary.deck_grade == "B级"
    assert result.summary.overall_score == "84.60"
    assert result.summary.overall_grade == "B级"


def test_facility_specific_name_labels_are_first_class_body_sources(tmp_path: Path) -> None:
    cases = (
        ("桥式通道名称", "官方院子桥式通道"),
        ("人行天桥名称", "小四沟人行天桥"),
        ("车行地通道名称", "K20+100车行地通道"),
        ("车行通道名称", "K20+200车行通道"),
    )
    expected_types = {
        "官方院子桥式通道": "桥式通道",
        "小四沟人行天桥": "人行天桥",
        "K20+100车行地通道": "车行地通道",
        "K20+200车行通道": "车行通道",
    }
    for label, expected in cases:
        result = extract_summary(_parse(tmp_path, _summary_table((label, expected))))
        assert result.summary.bridge_name == expected
        assert result.facility_context.facility_name == expected
        assert result.facility_context.facility_type_raw == expected_types[expected]


def test_component_grade_accepts_structural_assessment_phrasing(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph("根据桥面系检查，经计算桥面系BCIm=85.78，且BSIm=min(100-MDPi)=76.94，评定等级为C级；"),
            paragraph("根据上部结构检查，经计算上部结构BCIs=86.92，且BSIs=min(BCIs)=73.83，评定等级为C级；"),
            paragraph("根据下部结构检查，经计算结构BSIX=95.8；BSIx=min（BCIxi）=91.6，下部结构结构状况评定为A级；"),
        )
    )

    assert result.summary.deck_grade == "C级"
    assert result.summary.superstructure_grade == "C级"
    assert result.summary.substructure_grade == "A级"


def test_final_assessment_table_wins_component_score_conflict_only(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph("根据下部结构外观检查，经计算下部结构BCIx=96.29，BSIx=min（BCIxj）=88.89，评定为B级；"),
            table(
                row(cell("部位名称"), cell("技术状况指数"), cell("权重"), cell("BCI"), cell("桥梁整体技术状况等级")),
                row(cell("桥面系"), cell("91.95"), cell("0.15"), cell("89.31"), cell("B")),
                row(cell("上部结构"), cell("81.32"), cell("0.40"), cell(""), cell("")),
                row(cell("下部结构"), cell("95.53"), cell("0.45"), cell(""), cell("")),
            ),
            paragraph("本桥BCI=89.31，整体技术状况等级评定为B级。"),
        )
    )

    assert result.summary.substructure_score == "95.53"
    assert result.summary.overall_score == "89.31"
    assert result.summary.overall_grade == "B级"
    assert any(
        candidate.source_kind == "overall_assessment_table" and candidate.value == "95.53"
        for candidate in result.candidates["substructure_score"]
    )


def test_explicit_bci_score_phrase_is_current_score_fact(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph("综合评估：本设施技术状况等级BCI评分为95.39分，处于完好状态。"),
        )
    )

    assert result.summary.overall_score == "95.39"


def test_extract_summary_generic_mode_changes_only_grades(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        _score_table(
            ("总体", "89.46", "B级"),
            ("上部结构", "86.10", "D级"),
            ("下部结构", "92.00", "B级"),
            ("桥面系", "85.75", "D级"),
        ),
    )

    report = extract_summary(document, grade_mode="report").summary
    generic = extract_summary(document, grade_mode="generic").summary

    assert report.overall_score == generic.overall_score == "89.46"
    assert report.superstructure_score == generic.superstructure_score == "86.10"
    assert report.substructure_score == generic.substructure_score == "92.00"
    assert report.deck_score == generic.deck_score == "85.75"
    assert report.overall_grade == "B级"
    assert report.superstructure_grade == "D级"
    assert report.substructure_grade == "B级"
    assert report.deck_grade == "D级"
    assert generic.overall_grade == "B级"
    assert generic.superstructure_grade == "B级"
    assert generic.substructure_grade == "A级"
    assert generic.deck_grade == "B级"


def test_extract_summary_generic_mode_preserves_report_grade_when_score_missing(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        _summary_table(("桥梁名称", "无评分桥"), ("总体评分", ""), ("总体等级", "B级")),
    )

    result = extract_summary(document, grade_mode="generic").summary

    assert result.overall_score == "无"
    assert result.overall_grade == "B级"


def test_generic_mode_missing_score_preserves_grade_state_for_renderer(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        _summary_table(("桥梁名称", "无评分桥"), ("总体评分", ""), ("总体等级", "B级")),
    )
    result = extract_summary(document, grade_mode="generic")

    assert result.summary.overall_score == "无"
    assert result.summary.overall_grade == "B级"
    assert result.field_states["overall_grade"] == "present"


def test_score_extraction_common_variants(tmp_path: Path) -> None:
    cases = (
        ("综合评分为95.39分", "95.39"),
        ("技术状况评分 82.40 分", "82.40"),
        ("本桥评分为88.20分", "88.20"),
        ("本桥BCI=76.60，技术状况等级为二类。", "76.60"),
    )
    for text, expected in cases:
        result = extract_summary(_parse(tmp_path, paragraph(text)))
        assert result.summary.overall_score == expected


def test_class_grade_extraction_from_explicit_phrase(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(tmp_path, paragraph("经评定，本桥技术状况等级为二类，处于较好状态。"))
    )
    assert result.summary.overall_grade == "二类"


def test_class_assessment_inline_dr_score_is_explicit_fact(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            table(
                row(cell("项目"), cell("等级")),
                row(cell("综合评定分数Dr=70.4"), cell("二类")),
            ),
        )
    )
    assert result.summary.overall_score == "70.4"
    assert result.summary.overall_grade == "二类"


def test_no_fabrication_when_only_class_grade_exists(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(tmp_path, paragraph("本桥技术状况等级为二类，未提供综合评分。"))
    )
    assert result.summary.overall_score == "无"
    assert result.summary.overall_grade == "二类"


def test_overall_score_variant_does_not_steal_component_score(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(tmp_path, paragraph("上部结构技术状况评分为86.10分，评定为B级。"))
    )
    assert result.summary.superstructure_score == "86.10"
    assert result.summary.overall_score == "无"


@pytest.mark.parametrize("label, expected", [
    ("综合评定分数Dr=70.4", "70.4"),
    ("综合评定分数Dr=76.6", "76.6"),
    ("综合评定分数 Dr=67.80", "67.80"),
    ("综合评定分数Dr=82.4", "82.4"),
])
def test_long_span_class_system_dr_variants(label: str, expected: str, tmp_path: Path) -> None:
    result = extract_summary(
        _parse(tmp_path, table(row(cell(label), cell("二类"))))
    )
    assert result.summary.overall_score == expected
    assert result.summary.overall_grade == "二类"
    generic = extract_summary(
        _parse(tmp_path, table(row(cell(label), cell("二类")))), grade_mode="generic"
    )
    assert generic.summary.overall_score == expected
    assert generic.summary.overall_grade == "二类"


def test_bsi_score_grade_triplet_is_audit_evidence_not_prediction_fields(tmp_path: Path) -> None:
    text = "全桥结构状况指数BSIm、BSIs、BSIx分别为60.00（D级）、81.36（B级）、91.90（A级）。"
    result = extract_summary(_parse(tmp_path, paragraph(text)))

    assert result.summary.deck_score == "无"
    assert result.summary.deck_grade == "无"
    assert result.summary.superstructure_score == "无"
    assert result.summary.superstructure_grade == "无"
    assert result.summary.substructure_score == "无"
    assert result.summary.substructure_grade == "无"
    assert any(c.source_kind == "paired_score_grade" and c.value == "81.36" for c in result.candidates["superstructure_score"])
    assert any(c.source_kind == "paired_score_grade" and c.value == "B级" for c in result.candidates["superstructure_grade"])


def test_bci_component_scores_win_when_bsi_pairs_coexist(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph(
                "BCIm=72.35，BSIm=60.00（D级）；"
                "BCIs=86.91，BSIs=78.83（C级）；"
                "BCIx=97.01，BSIx=91.02（A级）。"
            ),
        )
    )

    assert result.summary.deck_score == "72.35"
    assert result.summary.superstructure_score == "86.91"
    assert result.summary.substructure_score == "97.01"
    # The BSI grades are structural-condition evidence, not component BCI grades.
    assert result.summary.deck_grade == "无"
    assert result.summary.superstructure_grade == "无"
    assert result.summary.substructure_grade == "无"


def test_final_assessment_table_beats_earlier_bci_and_bsi(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph("上部结构BCIs=97.80，BSIs=81.36（B级）。"),
            table(
                row(cell("部位名称"), cell("技术状况指数"), cell("权重"), cell("BCI")),
                row(cell("上部结构"), cell("81.36"), cell("0.40"), cell("89.31")),
            ),
        )
    )

    assert result.summary.superstructure_score == "81.36"
    assert result.summary.superstructure_grade == "无"
    values = {(c.value, c.source_kind) for c in result.candidates["superstructure_score"]}
    assert ("97.80", "bci") in values
    assert ("81.36", "paired_score_grade") in values
    assert ("81.36", "overall_assessment_table") in values


def test_bsi_pair_accepts_ascii_parentheses_but_is_not_mapped(tmp_path: Path) -> None:
    result = extract_summary(_parse(tmp_path, paragraph("BSIs=81.36(B级)")))
    assert result.summary.superstructure_score == "无"
    assert result.summary.superstructure_grade == "无"
    assert any(c.source_kind == "paired_score_grade" for c in result.candidates["superstructure_score"])


def test_generic_mode_maps_bci_score_not_bsi_score(tmp_path: Path) -> None:
    document = _parse(
        tmp_path,
        paragraph("上部结构BCIs=97.80，BSIs=81.36（B级）。"),
    )
    report = extract_summary(document, grade_mode="report").summary
    generic = extract_summary(document, grade_mode="generic").summary

    assert report.superstructure_score == generic.superstructure_score == "97.80"
    assert report.superstructure_grade == "无"
    assert generic.superstructure_grade == "A级"


def test_bsi_grade_only_does_not_fabricate_score(tmp_path: Path) -> None:
    result = extract_summary(_parse(tmp_path, paragraph("上部结构技术状况等级为B级。")))
    assert result.summary.superstructure_score == "无"
    assert result.summary.superstructure_grade == "B级"


def test_bsi_triplet_inside_table_cell_remains_audit_only(tmp_path: Path) -> None:
    text = "BSIm、BSIs、BSIx分别为60.00（D级）、81.36（B级）、91.90（A级）"
    result = extract_summary(_parse(tmp_path, table(row(cell("技术状况评定"), cell(text)))))

    assert result.summary.deck_score == "无"
    assert result.summary.deck_grade == "无"
    assert result.summary.superstructure_score == "无"
    assert result.summary.superstructure_grade == "无"
    assert result.summary.substructure_score == "无"
    assert result.summary.substructure_grade == "无"
    assert any(c.source_kind == "paired_score_grade" for c in result.candidates["deck_score"])


def test_class_system_component_dr_row_requires_explicit_component_and_dr(tmp_path: Path) -> None:
    result = extract_summary(
        _parse(
            tmp_path,
            table(
                row(cell("上部结构综合评定分数Dr=74.6"), cell("二类")),
                row(cell("综合评定分数Dr=70.4"), cell("二类")),
            ),
        )
    )
    assert result.summary.superstructure_score == "74.6"
    assert result.summary.superstructure_grade == "二类"
    assert result.summary.overall_score == "70.4"
    assert result.summary.overall_grade == "二类"


def test_dashancun_bci_primary_restore_regression(tmp_path: Path) -> None:
    """V15 regression: BCI component scores must beat paired BSI indices."""
    result = extract_summary(
        _parse(
            tmp_path,
            paragraph(
                "大山村互通Ⅰ号桥：BCIm=72.35，BSIm=60.00（D级）；"
                "BCIs=86.91，BSIs=78.83（C级）；"
                "BCIx=97.01，BSIx=91.02（A级）。"
            ),
        ),
        grade_mode="report",
    )
    assert result.summary.deck_score == "72.35"
    assert result.summary.superstructure_score == "86.91"
    assert result.summary.substructure_score == "97.01"
    assert any(c.source_kind == "paired_score_grade" and c.value == "60.00" for c in result.candidates["deck_score"])
    assert any(c.source_kind == "paired_score_grade" and c.value == "78.83" for c in result.candidates["superstructure_score"])
    assert any(c.source_kind == "paired_score_grade" and c.value == "91.02" for c in result.candidates["substructure_score"])
