from __future__ import annotations

from src.contracts import (
    DocumentModel,
    ParagraphBlock,
    SourceAnchor,
    TableBlock,
    TableCell,
    TableRow,
)
from src.extraction.recommendations import extract_recommendations


def _paragraph(index: int, text: str, *, heading_level: int | None = None) -> ParagraphBlock:
    return ParagraphBlock(
        index,
        text,
        SourceAnchor("fixture.docx", index, text, paragraph_index=index),
        heading_level=heading_level,
        style_id="Heading1" if heading_level else None,
    )


def _table(index: int, rows: list[list[str]]) -> TableBlock:
    table_index = 0
    table_rows = []
    raw_rows = []
    for row_index, values in enumerate(rows):
        cells = []
        for column_index, value in enumerate(values):
            cells.append(
                TableCell(
                    row_index,
                    column_index,
                    value,
                    source=SourceAnchor(
                        "fixture.docx",
                        index,
                        value,
                        table_index=table_index,
                        row_index=row_index,
                        column_index=column_index,
                    ),
                )
            )
        table_rows.append(TableRow(row_index, tuple(cells)))
        raw_rows.append("\t".join(values))
    raw_text = "\n".join(raw_rows)
    return TableBlock(
        index,
        raw_text,
        SourceAnchor("fixture.docx", index, raw_text, table_index=table_index),
        table_index=table_index,
        rows=tuple(table_rows),
    )


def _model(*blocks: ParagraphBlock | TableBlock) -> DocumentModel:
    return DocumentModel("fixture.docx", tuple(blocks))


def test_extracts_table_rows_and_preserves_cell_anchors() -> None:
    model = _model(
        _table(
            0,
            [
                ["序号", "建议类别", "建议内容", "病害部位"],
                ["1", "立即维修", "修复裂缝", "桥面、伸缩缝"],
                ["2", "预防性养护", "定期清理", "排水系统"],
            ],
        )
    )

    result = extract_recommendations(model)

    assert [
        (item.index, item.category, item.content, item.location)
        for item in result.records
    ] == [
        ("1", "立即维修", "修复裂缝", "桥面、伸缩缝"),
        ("2", "预防性养护", "定期清理", "排水系统"),
    ]
    assert result.records[0].evidence[0].row_index == 1
    assert result.records[0].evidence[0].column_index == 0
    assert result.quality_flags == ()


def test_splits_numbered_paragraph_list_and_keeps_multiple_locations() -> None:
    model = _model(
        _paragraph(0, "一、建议明细", heading_level=1),
        _paragraph(
            1,
            "1、尽快维修：桥面、伸缩缝：修复裂缝；2、预防性养护：排水沟：定期清理",
        ),
    )

    result = extract_recommendations(model)

    assert [(item.index, item.category, item.location) for item in result.records] == [
        ("1", "尽快维修", "桥面、伸缩缝"),
        ("2", "预防性养护", "排水沟"),
    ]
    assert [item.content for item in result.records] == ["修复裂缝", "定期清理"]


def test_appends_cross_paragraph_continuation_with_both_anchors() -> None:
    model = _model(
        _paragraph(0, "处理建议", heading_level=1),
        _paragraph(1, "1、尽快维修：桥面：修复裂缝"),
        _paragraph(2, "具体处理为先清理后封闭。"),
    )

    result = extract_recommendations(model)

    assert len(result.records) == 1
    assert result.records[0].content == "修复裂缝 具体处理为先清理后封闭。"
    assert [anchor.paragraph_index for anchor in result.records[0].evidence] == [1, 2]
    assert result.records[0].category == "尽快维修"


def test_consumes_treatment_route_without_forcing_category() -> None:
    model = _model(
        _paragraph(0, "处置建议", heading_level=1),
        _paragraph(1, "1、桥面：修复裂缝"),
    )

    result = extract_recommendations(model)

    assert len(result.records) == 1
    assert result.records[0].category == ""
    assert result.quality_flag_codes == ("recommendation_category_unresolved",)
    assert result.quality_flags[0]["quality_flag"] == "recommendation_category_unresolved"


def test_resolves_category_from_content_and_flags_unresolved_fallback() -> None:
    model = _model(
        _paragraph(0, "建议明细", heading_level=1),
        _paragraph(1, "1、立即维修：桥面：修复裂缝"),
        _paragraph(2, "2、检查桥面排水并记录结果"),
    )

    result = extract_recommendations(model)

    assert [item.category for item in result.records] == ["立即维修", ""]
    assert [item.index for item in result.records] == ["1", "2"]
    assert result.quality_flag_codes == ("recommendation_category_unresolved",)


def test_uses_paragraph_fallback_when_no_route_heading_is_available() -> None:
    result = extract_recommendations(_model(_paragraph(0, "桥面、伸缩缝：维修裂缝")))

    assert len(result.records) == 1
    assert result.records[0].location == "桥面、伸缩缝"
    assert result.records[0].content == "维修裂缝"
    assert result.records[0].evidence[0].paragraph_index == 0
