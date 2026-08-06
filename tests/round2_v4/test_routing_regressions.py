from __future__ import annotations

from src.contracts import (
    DocumentModel,
    ParagraphBlock,
    SourceAnchor,
    TableBlock,
    TableCell,
    TableRow,
)
from src.extraction.defects import extract_defects
from src.routing import SectionCategory, SectionRoute


def _paragraph(index: int, text: str) -> ParagraphBlock:
    return ParagraphBlock(
        index,
        text,
        SourceAnchor("fixture.docx", index, text, paragraph_index=index),
        heading_level=1,
        style_id="Heading1",
    )


def _table(block_index: int, table_index: int, rows: list[list[str]]) -> TableBlock:
    table_rows: list[TableRow] = []
    raw_rows: list[str] = []
    for row_index, values in enumerate(rows):
        cells = tuple(
            TableCell(
                row_index,
                column_index,
                value,
                source=SourceAnchor(
                    "fixture.docx",
                    block_index,
                    value,
                    table_index=table_index,
                    row_index=row_index,
                    column_index=column_index,
                ),
            )
            for column_index, value in enumerate(values)
        )
        table_rows.append(TableRow(row_index, cells))
        raw_rows.append("\t".join(values))
    raw_text = "\n".join(raw_rows)
    return TableBlock(
        block_index,
        raw_text,
        SourceAnchor(
            "fixture.docx",
            block_index,
            raw_text,
            table_index=table_index,
        ),
        table_index=table_index,
        rows=tuple(table_rows),
    )


def test_nonstandard_route_falls_back_to_real_structural_defect_table() -> None:
    heading = _paragraph(0, "病害检查")
    routed_noise = _table(
        1,
        0,
        [
            ["项目", "检测结果"],
            ["桥面线形", "线形正常"],
        ],
    )
    defect_table = _table(
        2,
        1,
        [
            ["序号", "病害部位", "病害类型", "病害描述"],
            ["1", "梁底", "裂缝", "梁底存在竖向裂缝"],
        ],
    )
    document = DocumentModel(
        "fixture.docx",
        (heading, routed_noise, defect_table),
    )
    route = SectionRoute(
        category=SectionCategory.DEFECT_TABLE,
        heading=heading,
        blocks=(heading, routed_noise),
        source=heading.source,
    )

    result = extract_defects(document, (route,))

    assert len(result.records) == 1
    assert result.records[0].location == "梁底"
    assert result.records[0].defect_type == "裂缝"
    assert result.records[0].description == "梁底存在竖向裂缝"
    assert "fallback_defect_table_routing" in {
        flag["code"] for flag in result.quality_flags
    }
