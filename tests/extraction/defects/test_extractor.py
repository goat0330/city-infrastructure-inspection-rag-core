from __future__ import annotations

from pathlib import Path

from src.extraction.defects import extract_defects
from src.parsing import parse_docx, parse_document_xml
from src.routing import SectionCategory, route_sections
from tests.fixtures.word.ooxml_factory import (
    cell,
    document_xml,
    paragraph,
    row,
    table,
    write_docx,
)


def _header() -> str:
    return row(
        cell("序号"),
        cell("病害部位"),
        cell("病害类型"),
        cell("病害描述"),
        cell("是否新增"),
        cell("上一次定检状态"),
        cell("发展程度"),
    )


def _data(*values: str, vmerge: bool = False) -> str:
    merge_value = "restart" if vmerge else None
    return row(
        cell(values[0], vmerge=merge_value),
        cell(values[1], vmerge=merge_value),
        cell(values[2], vmerge=merge_value),
        cell(values[3]),
        cell(values[4]),
        cell(values[5]),
        cell(values[6]),
    )


def test_route_to_extract_preserves_rows_merges_locations_and_anchors(tmp_path: Path) -> None:
    defect_table = table(
        _header(),
        _data("1", "桥面", "裂缝", "裂缝\n宽度约2mm", "是", "无", "无", vmerge=True),
        row(
            cell("", vmerge="continue"),
            cell("", vmerge="continue"),
            cell("", vmerge="continue"),
            cell("第二条具体位置"),
            cell("否"),
            cell("轻微"),
            cell("稳定"),
        ),
        _data("", "栏杆", "破损", "位置A；位置B", "否", "轻微", "稳定"),
        _header(),
        _data("2", "梁体", "露筋", "第三条", "是", "无", "无"),
    )
    path = write_docx(
        tmp_path / "fixture.docx",
        paragraph("2.1 病害明细表"),
        defect_table,
        paragraph("三、建议明细"),
    )

    document = parse_docx(path, source_file="fixture.docx")
    routes = route_sections(document)
    assert [route.category for route in routes] == [
        SectionCategory.DEFECT_TABLE,
        SectionCategory.RECOMMENDATIONS,
    ]

    result = extract_defects(document, routes)

    assert [
        (record.index, record.location, record.defect_type, record.description)
        for record in result
    ] == [
        ("1", "桥面", "裂缝", "裂缝\n宽度约2mm"),
        ("1", "桥面", "裂缝", "第二条具体位置"),
        ("1", "栏杆", "破损", "位置A；位置B"),
        ("2", "梁体", "露筋", "第三条"),
    ]
    assert [record.is_new for record in result] == ["是", "否", "否", "是"]
    assert all(record.evidence for record in result)
    assert result[1].evidence[0].row_index == 2
    assert not result.quality_flags


def test_structural_header_fallback_is_flagged_when_route_is_missing() -> None:
    xml = document_xml(
        table(
            row(cell("序号"), cell("部位"), cell("类型"), cell("描述")),
            row(cell("1"), cell("桥面"), cell("裂缝"), cell("保留原文")),
        )
    )
    document = parse_document_xml(xml, source_file="fallback.docx")

    assert route_sections(document) == ()
    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].description == "保留原文"
    assert result[0].evidence[0].table_index == 0
    assert "fallback_defect_table_routing" in {flag["code"] for flag in result.quality_flags}


def test_missing_and_ambiguous_headers_emit_flags_without_dropping_rows() -> None:
    xml = document_xml(
        paragraph("病害明细表"),
        table(
            row(cell("序号"), cell("病害部位"), cell("病害部位"), cell("病害描述")),
            row(cell("1"), cell("桥面"), cell("桥面附属"), cell("描述保留")),
        ),
    )
    document = parse_document_xml(xml, source_file="flags.docx")

    result = extract_defects(document)
    codes = {flag["code"] for flag in result.quality_flags}

    assert len(result) == 1
    assert result[0].index == "1"
    assert result[0].location == "桥面"
    assert result[0].defect_type == ""
    assert result[0].description == "描述保留"
    assert {"missing_defect_columns", "ambiguous_defect_columns"} <= codes


def test_routed_table_without_headers_uses_positions_and_flags_uncertainty() -> None:
    xml = document_xml(
        paragraph("一、病害列表"),
        table(
            row(
                cell("1"),
                cell("桥面"),
                cell("裂缝"),
                cell("无表头仍保留"),
                cell("是"),
                cell("无"),
                cell("稳定"),
            )
        ),
    )
    document = parse_document_xml(xml, source_file="positional.docx")

    result = extract_defects(document)
    codes = {flag["code"] for flag in result.quality_flags}

    assert len(result) == 1
    assert result[0].index == "1"
    assert result[0].location == "桥面"
    assert result[0].defect_type == "裂缝"
    assert result[0].description == "无表头仍保留"
    assert {"missing_defect_header", "fallback_positional_columns"} <= codes


def test_structural_fallback_ignores_calculation_table_with_position_and_type() -> None:
    xml = document_xml(
        table(
            row(cell("单元"), cell("位置"), cell("类型"), cell("验算")),
            row(cell("1"), cell("I[1]"), cell("MY-MIN"), cell("OK")),
        )
    )
    document = parse_document_xml(xml, source_file="calculation.docx")

    result = extract_defects(document)

    assert result.records == ()
    assert "missing_defect_table" in {flag["code"] for flag in result.quality_flags}


def test_structural_fallback_ignores_generic_load_table_with_description() -> None:
    xml = document_xml(
        table(
            row(cell("编号"), cell("名称"), cell("类型"), cell("描述")),
            row(cell("1"), cell("自重"), cell("施工阶段荷载"), cell("")),
            row(cell("2"), cell("二期"), cell("施工阶段荷载"), cell("桥面铺装")),
        )
    )
    document = parse_document_xml(xml, source_file="load.docx")

    result = extract_defects(document)

    assert result.records == ()
    assert "missing_defect_table" in {flag["code"] for flag in result.quality_flags}


def test_structural_fallback_maps_common_kind_and_specific_location_headers() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("桥面系"), cell("裂缝"), cell("伸缩缝处纵向裂缝")),
        )
    )
    document = parse_document_xml(xml, source_file="variant.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].location == "桥面系"
    assert result[0].defect_type == "裂缝"
    assert result[0].description == "伸缩缝处纵向裂缝"


def test_defect_description_preserves_photo_reference_and_measurements() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(
                cell("桥面系"),
                cell("裂缝"),
                cell("伸缩缝处3处纵向裂缝，宽度约2mm，见图2.1.1、照片5.1.1-1"),
            ),
        )
    )
    document = parse_document_xml(xml, source_file="photo.docx")

    result = extract_defects(document)

    assert result[0].description == "伸缩缝处3处纵向裂缝，宽度约2mm，见图2.1.1、照片5.1.1-1"


def test_missing_status_fields_remain_internal_missing_for_v10() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("桥面系"), cell("裂缝"), cell("伸缩缝处纵向裂缝")),
        )
    )
    document = parse_document_xml(xml, source_file="defaults.docx")

    result = extract_defects(document)

    assert result[0].is_new == ""
    assert result[0].previous_status == ""
    assert result[0].development == ""
    assert "defaulted_defect_fields" in {flag["code"] for flag in result.quality_flags}


def test_location_gets_lane_prefix_when_description_leads_with_lane() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("车行道"), cell("破损"), cell("右幅0#桥台附近桥面铺装局部破损")),
        )
    )
    document = parse_document_xml(xml, source_file="lane.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].location == "右幅车行道"
    assert result[0].description == "右幅0#桥台附近桥面铺装局部破损"


def test_location_lane_prefix_skips_when_lane_already_present() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("右幅桥面"), cell("破损"), cell("右幅桥面局部破损")),
        )
    )
    document = parse_document_xml(xml, source_file="lane-already.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].location == "右幅桥面"


def test_bare_lane_location_is_expanded_with_section_heading() -> None:
    xml = document_xml(
        paragraph("5.1.2 上部结构"),
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("左幅"), cell("纵裂"), cell("第1跨1#板跨中1条纵裂")),
        ),
    )
    document = parse_document_xml(xml, source_file="section.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].location == "左幅上部结构"


def test_bare_lane_location_unchanged_without_section_heading() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("左幅"), cell("纵裂"), cell("第1跨1#板跨中1条纵裂")),
        )
    )
    document = parse_document_xml(xml, source_file="section-missing.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].location == "左幅"


def test_location_gets_side_prefix_when_description_leads_with_side() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("栏杆"), cell("开裂"), cell("右侧防撞护栏1处开裂")),
        )
    )
    document = parse_document_xml(xml, source_file="side.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].location == "右侧栏杆"


def test_side_prefix_skips_when_side_already_present() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("左侧护栏"), cell("破损"), cell("左侧防撞护栏局部破损")),
        )
    )
    document = parse_document_xml(xml, source_file="side-already.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].location == "左侧护栏"


def test_side_prefix_skips_when_lane_is_already_present_and_keeps_section() -> None:
    xml = document_xml(
        paragraph("5.1.2 上部结构"),
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("左幅"), cell("渗水"), cell("左侧桥台侧墙局部渗水")),
        ),
    )
    document = parse_document_xml(xml, source_file="lane-side-section.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].location == "左幅上部结构"
def test_text_fallback_is_scoped_and_splits_concrete_observations() -> None:
    xml = document_xml(
        paragraph("桥梁概况"),
        paragraph("桥面铺装局部破损。"),
        paragraph("检测结论"),
        paragraph("桥面局部存在裂缝，栏杆多处破损；左幅/右幅两处均存在渗水。"),
        paragraph("安全性评估"),
        paragraph("桥台局部出现沉降。"),
    )
    document = parse_document_xml(xml, source_file="text-fallback.docx")

    result = extract_defects(document)

    assert [
        (record.location, record.defect_type, record.description)
        for record in result
    ] == [
        ("桥面", "裂缝", "桥面局部存在裂缝"),
        ("栏杆", "破损", "栏杆多处破损"),
        ("左幅", "渗水", "左幅存在渗水"),
        ("右幅", "渗水", "右幅存在渗水"),
        ("桥台", "沉降", "桥台局部出现沉降"),
    ]
    assert "fallback_defect_text" in {flag["code"] for flag in result.quality_flags}
    assert all(record.evidence[0].paragraph_index is not None for record in result)


def test_text_fallback_requires_numbered_underfill_and_table_wins_deduplication() -> None:
    defect_table = table(
        row(cell("序号"), cell("部位"), cell("类型"), cell("描述")),
        row(cell("1"), cell("桥面"), cell("裂缝"), cell("桥面局部存在裂缝")),
    )
    xml = document_xml(
        paragraph("病害明细表"),
        defect_table,
        paragraph("检测结论"),
        paragraph("（1）桥面局部存在裂缝。"),
        paragraph("（2）栏杆多处破损。"),
        paragraph("（3）桥台局部出现渗水。"),
    )
    document = parse_document_xml(xml, source_file="text-underfill.docx")

    result = extract_defects(document)

    assert [
        (record.location, record.defect_type, record.description)
        for record in result
    ] == [
        ("桥面", "裂缝", "桥面局部存在裂缝"),
        ("栏杆", "破损", "栏杆多处破损"),
        ("桥台", "渗水", "桥台局部出现渗水"),
    ]
    fallback = next(flag for flag in result.quality_flags if flag["code"] == "fallback_defect_text")
    assert fallback["details"]["reason"] == "text_candidates_exceed_table_rows"
    assert fallback["details"]["added_row_count"] == 2


def test_empty_result_reads_only_bounded_legacy_appearance_section() -> None:
    section = (
        "4.2 外观病害检查表4.2 序号位置病害种类病害情况"
        "1桥面/桥面无泄水孔"
        "2栏杆锈蚀、松动钢丝网局部锈蚀、松动"
        "4.3 桥梁线形测量"
    )
    xml = document_xml(
        paragraph("前置段落桥面局部破损。"),
        table(*(row(cell("封面信息")) for _ in range(9)), row(cell(section))),
        paragraph("后置段落梁体裂缝。"),
    )
    document = parse_document_xml(xml, source_file="legacy-text.docx")

    result = extract_defects(document)

    assert [
        (record.location, record.defect_type, record.description)
        for record in result
    ] == [
        ("桥面", "设施缺失", "桥面无泄水孔"),
        ("栏杆", "锈蚀、松动", "钢丝网局部锈蚀、松动"),
    ]
    assert "fallback_defect_text" in {flag["code"] for flag in result.quality_flags}
def test_caption_rows_merge_into_their_defect_evidence_instead_of_new_records(
    tmp_path: Path,
) -> None:
    defect_table = table(
        row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况")),
        row(
            cell("1"),
            cell("空心板"),
            cell("裂缝"),
            cell("第1跨6#板距0#台2.5m处，有1条横向贯通裂缝，L=1.5m、W=0.08mm，照5.3.1-1"),
        ),
        row(
            cell("2"),
            cell(""),
            cell(""),
            cell("第1跨7#板距0#台2.5m处，有1条横向贯通裂缝，L=1.5m、W=0.1mm，照5.3.1-2"),
        ),
        row(
            cell("照5.3.1-1  第1跨6#板距0#台2.5m处，有1条横向贯通裂缝，L=1.5m、W=0.08mm", grid_span=4),
            cell("照5.3.1-2  第1跨7#板距0#台2.5m处，有1条横向贯通裂缝，L=1.5m、W=0.1mm"),
        ),
    )
    xml = document_xml(
        paragraph("5.3.1 上部结构病害表"),
        defect_table,
    )
    document = parse_document_xml(xml, source_file="caption-merge.docx")

    result = extract_defects(document)

    assert [(record.index, record.location, record.defect_type) for record in result] == [
        ("1", "空心板", "裂缝"),
        ("2", "空心板", "裂缝"),
    ]
    assert result[0].description == "第1跨6#板距0#台2.5m处，有1条横向贯通裂缝，L=1.5m、W=0.08mm，照5.3.1-1"
    assert sorted(anchor.row_index for anchor in result[0].evidence) == [1, 1, 1, 1, 3]
    assert sorted(anchor.row_index for anchor in result[1].evidence) == [1, 1, 2, 2, 2, 2, 3]
    merged = [flag for flag in result.quality_flags if flag["code"] == "photo_caption_row_merged"]
    assert len(merged) == 1
    assert merged[0]["details"]["defect_indices"] == ["1", "2"]


def test_caption_row_without_matching_defect_is_flagged_not_fabricated() -> None:
    xml = document_xml(
        paragraph("病害明细表"),
        table(
            row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况")),
            row(cell("1"), cell("桥面"), cell("裂缝"), cell("桥面局部裂缝，照5.1-1")),
            row(cell("照9.9.9  未见对应病害行的照片", grid_span=4)),
        ),
    )
    document = parse_document_xml(xml, source_file="caption-unmapped.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].description == "桥面局部裂缝，照5.1-1"
    assert "unmapped_caption_row" in {flag["code"] for flag in result.quality_flags}


def test_unit_placeholder_and_continuation_rows_are_excluded_with_flags() -> None:
    xml = document_xml(
        paragraph("病害明细表"),
        table(
            row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况")),
            row(cell("1"), cell("桥面"), cell("裂缝"), cell("桥面局部裂缝，宽约0.2mm")),
            row(cell("单位：mm", grid_span=4)),
            row(cell("本表无病害", grid_span=4)),
            row(cell("续表", grid_span=1)),
        ),
    )
    document = parse_document_xml(xml, source_file="layout-rows.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].description == "桥面局部裂缝，宽约0.2mm"
    excluded = [flag for flag in result.quality_flags if flag["code"] == "excluded_non_defect_row"]
    assert len(excluded) == 3
    reasons = {flag["details"]["reason"] for flag in excluded}
    assert reasons == {"unit_or_placeholder", "unit_or_placeholder", "repeated_header"}


def test_is_valid_defect_row_classifies_non_defect_rows() -> None:
    from src.extraction.defects.extractor import is_valid_defect_row

    assert is_valid_defect_row(
        {"index": "1", "location": "桥面", "defect_type": "裂缝", "description": "桥面裂缝，照5.1-1"}
    ) is None
    assert is_valid_defect_row(
        {"index": "", "location": "桥面", "defect_type": "裂缝", "description": "3条纵向裂缝，宽度约2mm，见图2.1.1、照片5.1.1-1"}
    ) is None
    assert is_valid_defect_row(
        {"index": "照5.3.1-1  第1跨6#板距0#台2.5m处，有1条横向贯通裂缝", "location": "", "defect_type": "", "description": ""}
    ) == "photo_caption"
    assert is_valid_defect_row(
        {"index": "", "location": "", "defect_type": "", "description": "单位：mm"}
    ) == "unit_or_placeholder"
    assert is_valid_defect_row(
        {"index": "", "location": "", "defect_type": "", "description": "本表无病害"}
    ) == "unit_or_placeholder"
    assert is_valid_defect_row(
        {"index": "", "location": "", "defect_type": "", "description": ""}
    ) == "placeholder"


def test_valid_description_preserves_figure_refs_counts_dimensions_and_crack_widths() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(
                cell("桥面铺装"),
                cell("裂缝"),
                cell("距1#伸缩缝21m，距左侧2.5m，3处龟裂，s=0.5m×0.3m，宽约2mm，L=1.5m，见图2.1.1、照片5.1.1-1"),
            ),
        )
    )
    document = parse_document_xml(xml, source_file="preserve.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert (
        result[0].description
        == "距1#伸缩缝21m，距左侧2.5m，3处龟裂，s=0.5m×0.3m，宽约2mm，L=1.5m，见图2.1.1、照片5.1.1-1"
    )


def test_caption_like_description_merges_when_it_documents_an_existing_defect() -> None:
    xml = document_xml(
        paragraph("病害明细表"),
        table(
            row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况")),
            row(
                cell("1"),
                cell("桥面铺装"),
                cell("龟裂"),
                cell("距1#伸缩缝21m，距左侧2.5m，龟裂，s=0.5m×0.3m，照5.1-1"),
            ),
            row(cell(""), cell(""), cell(""), cell("照5.1-1  距1#伸缩缝21m，距左侧2.5m，龟裂，s=0.5m×0.3m")),
        ),
    )
    document = parse_document_xml(xml, source_file="caption-desc.docx")

    result = extract_defects(document)

    assert len(result) == 1
    assert result[0].index == "1"
    assert result[0].description == "距1#伸缩缝21m，距左侧2.5m，龟裂，s=0.5m×0.3m，照5.1-1"
    assert sorted(anchor.row_index for anchor in result[0].evidence) == [1, 1, 1, 1, 2, 2, 2, 2]
    assert "photo_caption_row_merged" in {flag["code"] for flag in result.quality_flags}


def test_duplicate_table_rows_are_deduped_keeping_first_description_and_all_anchors() -> None:
    xml = document_xml(
        paragraph("病害明细表"),
        table(
            row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况")),
            row(cell("1"), cell("桥面"), cell("裂缝"), cell("桥面局部裂缝，见图2.1.1")),
            row(cell("1"), cell("桥面"), cell("裂缝"), cell("桥面局部裂缝，见图2.1.1")),
            row(cell("2"), cell("栏杆"), cell("破损"), cell("右侧栏杆局部破损")),
        ),
    )
    document = parse_document_xml(xml, source_file="dedup.docx")

    result = extract_defects(document)

    assert [(record.index, record.location, record.defect_type) for record in result] == [
        ("1", "桥面", "裂缝"),
        ("2", "右侧栏杆", "破损"),
    ]
    assert result[0].description == "桥面局部裂缝，见图2.1.1"
    assert len(result[0].evidence) == len({anchor for anchor in result[0].evidence})
    dedup = [flag for flag in result.quality_flags if flag["code"] == "deduplicated_defect_rows"]
    assert len(dedup) == 1
    assert dedup[0]["details"]["removed_row_count"] == 1


def test_no_hard_defect_count_cap() -> None:
    rows = [row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况"))]
    for index in range(1, 41):
        rows.append(
            row(
                cell(str(index)),
                cell("桥面"),
                cell("裂缝"),
                cell(f"第{index}处桥面局部裂缝，L={index}m"),
            )
        )
    xml = document_xml(paragraph("病害明细表"), table(*rows))
    document = parse_document_xml(xml, source_file="nocap.docx")

    result = extract_defects(document)

    assert len(result) == 40
    assert result[-1].description == "第40处桥面局部裂缝，L=40m"


def test_history_table_matches_location_column_without_repeating_location_in_disease_cells() -> None:
    xml = document_xml(
        paragraph("5.1 病害明细表"),
        table(
            row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况")),
            row(cell("1"), cell("主梁"), cell("裂缝"), cell("第1跨主梁局部裂缝")),
        ),
        paragraph("7.1 外观检测结果对比分析"),
        table(
            row(cell("位置"), cell("上一次检测结果"), cell("本次检测结果"), cell("发展状况")),
            row(cell("主梁"), cell("无"), cell("裂缝"), cell("新增")),
        ),
    )
    document = parse_document_xml(xml, source_file="history-location.docx")

    result = extract_defects(document)

    target = next(record for record in result if record.defect_type == "裂缝")
    assert target.is_new == "是"
    assert target.previous_status == "无"
    assert target.development == "新增"
    assert any(anchor.table_index == 1 for anchor in target.evidence)
    assert "history_comparison_enriched" in {flag["code"] for flag in result.quality_flags}


def test_history_table_inherits_merged_location_and_enriches_multiple_diseases() -> None:
    xml = document_xml(
        paragraph("5.1 病害明细表"),
        table(
            row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况")),
            row(cell("1"), cell("主梁"), cell("裂缝"), cell("主梁裂缝")),
            row(cell("2"), cell("主梁"), cell("露筋"), cell("主梁露筋")),
        ),
        paragraph("7.1 外观检测结果对比分析"),
        table(
            row(cell("位置"), cell("上一次检测结果"), cell("本次检测结果"), cell("发展状况")),
            row(cell("主梁", vmerge="restart"), cell("裂缝"), cell("裂缝"), cell("无变化")),
            row(cell("", vmerge="continue"), cell("无"), cell("露筋"), cell("新增")),
        ),
    )
    document = parse_document_xml(xml, source_file="history-merged.docx")

    result = extract_defects(document)
    by_type = {record.defect_type: record for record in result}

    assert by_type["裂缝"].previous_status == "裂缝"
    assert by_type["裂缝"].development == "无变化"
    assert by_type["裂缝"].is_new == "否"
    assert by_type["露筋"].is_new == "是"
    assert by_type["露筋"].previous_status == "无"
    assert by_type["露筋"].development == "新增"


def test_headerless_history_table_is_used_only_with_explicit_history_context() -> None:
    xml = document_xml(
        paragraph("5.1 病害明细表"),
        table(
            row(cell("序号"), cell("位置"), cell("病害种类"), cell("病害情况")),
            row(cell("1"), cell("桥面"), cell("破损"), cell("桥面局部破损")),
        ),
        paragraph("7 历次检测结果对比分析"),
        paragraph("7.1 外观检测结果对比"),
        table(
            row(cell("1"), cell("桥面"), cell("无"), cell("破损"), cell("新增")),
            row(cell("2"), cell("栏杆"), cell("锈蚀"), cell("锈蚀"), cell("无变化")),
        ),
    )
    document = parse_document_xml(xml, source_file="history-headerless.docx")

    result = extract_defects(document)
    target = next(record for record in result if record.defect_type == "破损")

    assert target.is_new == "是"
    assert target.development == "新增"
