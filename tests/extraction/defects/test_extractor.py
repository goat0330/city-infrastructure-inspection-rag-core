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
