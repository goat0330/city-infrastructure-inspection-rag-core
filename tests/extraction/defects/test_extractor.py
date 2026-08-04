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


def test_defect_description_drops_trailing_photo_reference() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("桥面系"), cell("裂缝"), cell("伸缩缝处纵向裂缝，见照片5.1.1-1")),
        )
    )
    document = parse_document_xml(xml, source_file="photo.docx")

    result = extract_defects(document)

    assert result[0].description == "伸缩缝处纵向裂缝"


def test_missing_status_fields_use_auditable_gold_template_defaults() -> None:
    xml = document_xml(
        table(
            row(cell("位置"), cell("病害种类"), cell("具体位置")),
            row(cell("桥面系"), cell("裂缝"), cell("伸缩缝处纵向裂缝")),
        )
    )
    document = parse_document_xml(xml, source_file="defaults.docx")

    result = extract_defects(document)

    assert result[0].is_new == "否"
    assert result[0].previous_status == "无"
    assert result[0].development == "无"
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
