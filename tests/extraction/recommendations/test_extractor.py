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
from src.extraction.recommendations.extractor import (
    _looks_like_recommendation_paragraph,
    _location_fields,
)


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

    assert [item.category for item in result.records] == ["立即维修"]
    assert [item.index for item in result.records] == ["1"]
    assert result.quality_flag_codes == ()


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


def test_explicit_measure_section_excludes_historical_numbered_prose() -> None:
    result = extract_recommendations(
        _model(
            _paragraph(0, "1 历史检查" , heading_level=1),
            _paragraph(1, "（1）历史检查建议对桥梁进行维修。"),
            _paragraph(2, "3 应采取的措施", heading_level=2),
            _paragraph(3, "3.1应立即维护的设施"),
            _paragraph(4, "（1）立即修复主桥北岸构件脱落的伸缩缝。"),
            _paragraph(5, "（2）拆除遗留在桥上的临时设施，避免安全事故。"),
            _paragraph(6, "3.2在日常养护中采取才措施"),
            _paragraph(7, "（1）加强日常巡查，按规范要求对桥梁进行养护。"),
            _paragraph(8, ""),
            _paragraph(9, ""),
            _paragraph(10, ""),
            _paragraph(11, "桥梁资料卡"),
        )
    )

    assert [item.content for item in result.records] == [
        "立即修复主桥北岸构件脱落的伸缩缝。",
        "拆除遗留在桥上的临时设施，避免安全事故。",
        "加强日常巡查，按规范要求对桥梁进行养护。",
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


def test_fallback_requires_action_and_location_and_rejects_narrative() -> None:
    rejected = (
        "检查桥面并记录结果。",
        "桥面：建议检查并记录结果。",
        "检测方法：清理桥面并记录加载位置。",
        "评定依据：桥梁技术状况等级。",
        "技术要求：应定期检查桥梁。",
        "原因分析：维修不及时。",
        "提出如下建议：",
        "梁底钢板加固，外观状况良好，暂未见明显病害。具体情况见表5.1.2。",
        "主桥上部结构现状为拱腰渗水、泛碱等，如不及时进行处理会影响耐久性。",
        "板底多条纵向裂缝，加固钢板锈蚀，个别挡块斜裂。",
        "桥面目前不能够满足功能要求，不维修处理就不能正常运营。",
    )

    assert all(not _looks_like_recommendation_paragraph(text) for text in rejected)
    assert _looks_like_recommendation_paragraph("桥面：修复裂缝。")


def test_keeps_multiple_actions_for_one_numbered_repair_item() -> None:
    result = extract_recommendations(
        _model(
            _paragraph(0, "处理建议", heading_level=1),
            _paragraph(1, "1、尽快维修：桥面：清理裂缝；修补裂缝。"),
        )
    )

    assert len(result.records) == 1
    assert result.records[0].content == "清理裂缝；修补裂缝。"


def test_splits_semicolon_parallel_location_items() -> None:
    result = extract_recommendations(
        _model(_paragraph(0, "桥面：清理裂缝；栏杆：修复破损。"))
    )

    assert [(item.location, item.content) for item in result.records] == [
        ("桥面", "清理裂缝"),
        ("栏杆", "修复破损。"),
    ]


def test_splits_newline_continuous_circled_numbering() -> None:
    result = extract_recommendations(
        _model(
            _paragraph(0, "处理建议", heading_level=1),
            _paragraph(1, "①建议对桥面进行修复\n②建议对栏杆进行维修"),
        )
    )

    assert [item.index for item in result.records] == ["①", "②"]
    assert [item.location for item in result.records] == ["桥面", "栏杆"]


def test_location_relations_stop_at_location_content_boundary() -> None:
    cases = (
        ("对于破损的侧墙进行修复", "侧墙"),
        ("针对箱梁底板及腹板出现的裂缝采取压力灌浆修补", "箱梁底板、腹板"),
        ("对右洞口处顶板车辆刮痕进行修复", "顶板"),
        ("在顶板处进行修复", "顶板"),
        ("桥面部位：清理并修补", "桥面"),
    )

    assert [_location_fields(text)[0] for text, _ in cases] == [
        expected for _, expected in cases
    ]


def test_high_confidence_inference_does_not_default_to_repair() -> None:
    result = extract_recommendations(
        _model(
            _paragraph(0, "处理建议", heading_level=1),
            _paragraph(1, "1、对于桥面存在病害，建议关注其变化。"),
        ),
        infer_categories=True,
    )

    assert len(result.records) == 1
    assert result.records[0].category == ""
    assert result.quality_flag_codes == ("recommendation_category_unresolved",)


def test_audits_recommendations_and_treatment_recommendations_routes() -> None:
    result = extract_recommendations(
        _model(
            _paragraph(0, "建议", heading_level=1),
            _paragraph(1, "1、尽快维修：桥面：修复裂缝"),
            _paragraph(2, "处置建议", heading_level=1),
            _paragraph(3, "1、预防性养护：桥梁：定期检查"),
        ),
        infer_categories=True,
    )

    assert [(item.category, item.location) for item in result.records] == [
        ("尽快维修", "桥面"),
        ("预防性养护", "桥梁"),
    ]
