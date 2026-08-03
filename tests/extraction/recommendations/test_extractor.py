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


def test_appends_unumbered_cross_paragraph_continuation() -> None:
    model = _model(
        _paragraph(0, "处理建议", heading_level=1),
        _paragraph(1, "桥面：维修裂缝"),
        _paragraph(2, "具体处理为先清理后封闭。"),
    )

    result = extract_recommendations(model)

    assert len(result.records) == 1
    assert result.records[0].content == "维修裂缝 具体处理为先清理后封闭。"


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


def test_target_route_ignores_unrelated_wide_table() -> None:
    model = _model(
        _paragraph(0, "处置建议", heading_level=1),
        _table(
            1,
            [
                ["单元", "位置", "类型", "验算"],
                ["1", "I[1]", "MY-MIN", "OK"],
            ],
        ),
    )

    result = extract_recommendations(model)

    assert result.records == ()


def test_fallback_ignores_test_procedure_conclusion_and_metadata() -> None:
    model = _model(
        _paragraph(
            0,
            "按照试验方案要求试验车辆装载过磅，记录试验车的原始数据。清理桥面，标记加载位置。",
        ),
        _paragraph(
            1,
            "（2）防撞护栏、桥墩局部破损、露筋、锈蚀的主要原因：由于保护层厚度不足，维修不及时。",
        ),
        _paragraph(2, "桥梁名称：白房子中桥 所在路名：渝遂高青段 等级：Ⅰ等养护"),
    )

    result = extract_recommendations(model)

    assert result.records == ()


def test_target_route_accepts_repair_and_skips_introductory_sentence() -> None:
    model = _model(
        _paragraph(0, "15 建议", heading_level=1),
        _paragraph(1, "结合检测结果，对桥梁养护建议如下："),
        _paragraph(2, "（1）对空心板底板裂缝采取压力灌浆方法进行修补。"),
    )

    result = extract_recommendations(model)

    assert [(item.index, item.content) for item in result.records] == [
        ("（1", "对空心板底板裂缝采取压力灌浆方法进行修补。"),
    ]


def test_extracts_parenthesized_ideographic_numbered_recommendations() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(1, "⑴建议对桥面及时进行修复。"),
        _paragraph(2, "⑵对于栏杆破损，建议采用混凝土修补处理。"),
    )

    result = extract_recommendations(model)

    assert len(result.records) == 2
    assert [item.content for item in result.records] == [
        "建议对桥面及时进行修复。",
        "对于栏杆破损，建议采用混凝土修补处理。",
    ]


def test_infers_explicit_location_from_recommendation_sentence() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(1, "⑴由于桥面存在破损，建议对桥面及时进行修复。"),
        _paragraph(2, "⑵对于栏杆存在破损，建议及时采用混凝土修补处理。"),
        _paragraph(3, "⑶建议严格按规范做好桥梁的日常检查和维护工作。"),
    )

    result = extract_recommendations(model)

    assert [item.location for item in result.records] == ["桥面", "栏杆", "桥梁"]


def test_category_inference_is_opt_in_and_gold_derived() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(1, "⑴建议对桥面及时进行修复。"),
        _paragraph(2, "⑵建议严格按规范做好桥梁的日常检查和维护工作。"),
    )

    unresolved = extract_recommendations(model)
    inferred = extract_recommendations(model, infer_categories=True)

    assert [item.category for item in unresolved.records] == ["", ""]
    assert [item.category for item in inferred.records] == ["尽快维修", "预防性养护"]
    assert all(
        flag["code"] == "recommendation_category_inferred"
        for flag in inferred.quality_flags
    )
