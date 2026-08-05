from __future__ import annotations

from src.contracts import (
    DocumentModel,
    ParagraphBlock,
    SourceAnchor,
    TableBlock,
    TableCell,
    TableRow,
)
from src.routing import SectionCategory, route_sections
from src.routing.section_router import (
    is_recommendation_container_heading,
    is_recommendation_leaf_heading,
)


def _paragraph(
    index: int,
    text: str,
    *,
    heading_level: int | None = None,
    style_id: str | None = None,
) -> ParagraphBlock:
    anchor = SourceAnchor("fixture.docx", index, text, paragraph_index=index)
    return ParagraphBlock(
        index,
        text,
        anchor,
        heading_level=heading_level,
        style_id=style_id,
    )


def _defect_table(index: int) -> TableBlock:
    text = "序号\t病害部位\t病害类型\t病害描述"
    anchor = SourceAnchor("fixture.docx", index, text, table_index=0)
    cell_anchor = SourceAnchor(
        "fixture.docx", index, "病害部位", table_index=0, row_index=0, column_index=1
    )
    row = TableRow(
        0,
        (
            TableCell(0, 0, "序号", source=anchor),
            TableCell(0, 1, "病害部位", source=cell_anchor),
            TableCell(0, 2, "病害类型", source=anchor),
            TableCell(0, 3, "病害描述", source=anchor),
        ),
    )
    return TableBlock(index, text, anchor, table_index=0, rows=(row,))


def _model(*blocks: ParagraphBlock | TableBlock) -> DocumentModel:
    return DocumentModel("fixture.docx", tuple(blocks))


def test_routes_styled_sections_and_preserves_boundaries_and_anchors() -> None:
    model = _model(
        _paragraph(0, "一、总体评分", style_id="Heading1"),
        _paragraph(1, "评分内容"),
        _paragraph(2, "2.1 病害明细表", heading_level=1, style_id="Heading1"),
        _defect_table(3),
        _paragraph(4, "三、建议明细", heading_level=1, style_id="Heading1"),
        _paragraph(5, "维修建议内容"),
        _paragraph(6, "四、详细结论", heading_level=1, style_id="Heading1"),
        _paragraph(7, "检测结论正文"),
        _paragraph(8, "5 安全性评估", heading_level=1, style_id="Heading1"),
        _paragraph(9, "安全性评估正文"),
        _paragraph(10, "第六章 处理建议", heading_level=1, style_id="Heading1"),
        _paragraph(11, "处置建议正文"),
    )

    routes = route_sections(model)

    assert [route.category for route in routes] == [
        SectionCategory.SCORING,
        SectionCategory.DEFECT_TABLE,
        SectionCategory.RECOMMENDATIONS,
        SectionCategory.INSPECTION_CONCLUSION,
        SectionCategory.SAFETY_ASSESSMENT,
        SectionCategory.TREATMENT_RECOMMENDATIONS,
    ]
    assert [route.source.block_index for route in routes] == [0, 2, 4, 6, 8, 10]
    assert [block.block_index for block in routes[0].blocks] == [0, 1]
    assert routes[1].blocks[1].source.table_index == 0
    assert all(route.source == route.heading.source for route in routes)
    assert all(isinstance(route.source, SourceAnchor) for route in routes)


def test_keyword_fallback_accepts_numbering_variants_without_bridge_name() -> None:
    model = _model(
        _paragraph(0, "3.2 评分"),
        _paragraph(1, "（七）病害列表"),
        _paragraph(2, "第九章 建议"),
        _paragraph(3, "十、检测结论"),
        _paragraph(4, "11) 安全评估"),
        _paragraph(5, "12.1 处置建议"),
    )

    routes = route_sections(model)

    assert [route.category for route in routes] == [
        SectionCategory.SCORING,
        SectionCategory.DEFECT_TABLE,
        SectionCategory.RECOMMENDATIONS,
        SectionCategory.INSPECTION_CONCLUSION,
        SectionCategory.SAFETY_ASSESSMENT,
        SectionCategory.TREATMENT_RECOMMENDATIONS,
    ]
    assert [route.heading.raw_text for route in routes] == [
        block.raw_text for block in model.blocks
    ]
    assert all(route.source.source_file == "fixture.docx" for route in routes)


def test_table_title_routes_as_defect_table_and_repeated_runs_are_stable() -> None:
    model = _model(_defect_table(0), _paragraph(1, "正文"))

    first = route_sections(model)
    second = route_sections(model)

    assert first == second
    assert len(first) == 1
    assert first[0].category is SectionCategory.DEFECT_TABLE
    assert first[0].source.table_index == 0
    assert first[0].blocks == (model.blocks[0],)


def test_no_matching_heading_returns_empty_tuple() -> None:
    model = _model(
        _paragraph(0, "桥梁基本信息", heading_level=1, style_id="Heading1"),
        _paragraph(1, "检测人员与日期"),
    )

    assert route_sections(model) == ()


def test_composite_recommendation_heading_is_container_not_leaf() -> None:
    model = _model(
        _paragraph(0, "5 结论与建议", heading_level=1),
        _paragraph(1, "5.1 检测结论", heading_level=2),
        _paragraph(2, "结论正文"),
        _paragraph(3, "5.4 处理建议", heading_level=2),
        _paragraph(4, "建议正文"),
    )

    routes = route_sections(model)

    parent = next(route for route in routes if route.heading.block_index == 0)
    leaf = next(route for route in routes if route.heading.block_index == 3)
    assert parent.is_container
    assert not leaf.is_container
    assert is_recommendation_container_heading("5 结论与建议")
    assert not is_recommendation_leaf_heading("5 结论与建议")
    assert is_recommendation_leaf_heading("5.4 处理建议")
