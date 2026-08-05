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
    summarize_recommendations,
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
        ("1", "立即处置", "修复裂缝", "桥面、伸缩缝"),
        ("2", "预防性养护", "定期清理", "排水系统"),
    ]
    assert result.records[0].evidence[0].row_index == 1
    assert result.records[0].evidence[0].column_index == 0
    assert result.quality_flags == ()
    assert result.raw_categories == (
        {"index": "1", "raw_category": "立即维修", "category": "立即处置"},
    )


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
    assert [item.content for item in result.records] == ["修复裂缝；", "定期清理"]


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

    assert [item.category for item in result.records] == ["立即处置"]
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

    assert [item.location for item in result.records] == ["桥面", "栏杆", "该设施"]


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


def test_numbered_item_content_keeps_trailing_semicolon() -> None:
    model = _model(
        _paragraph(0, "一、建议明细", heading_level=1),
        _paragraph(1, "（1）尽快维修：桥面：修复裂缝；"),
    )

    result = extract_recommendations(model)

    assert len(result.records) == 1
    assert result.records[0].content == "修复裂缝；"


def test_infers_subject_state_location_without_preposition() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(1, "⑴桥台存在多处渗水、泛碱的病害，建议按照规范及时进行处理。"),
    )

    result = extract_recommendations(model)

    assert result.records[0].location == "桥台"
    assert result.records[0].content.startswith("桥台存在多处渗水")


def test_infers_compound_location_from_repaired_targets() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(1, "⑴建议对桥面和伸缩缝及时进行修复，以免影响桥梁的耐久性。"),
    )

    result = extract_recommendations(model)

    assert result.records[0].location == "桥面、伸缩缝"


def test_strips_defect_suffix_and_dangling_word_from_inferred_location() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(1, "⑴对于腹板斜裂的病害，建议采用压力灌浆法灌注环氧树脂胶进行处理。"),
        _paragraph(2, "⑵对于桥台开裂的病害，建议及时修补处理。"),
        _paragraph(3, "⑶对于主梁均存在纵裂的情况，建议及时封闭处理。"),
    )

    result = extract_recommendations(model)

    assert [item.location for item in result.records] == ["腹板", "桥台", "主梁"]


def test_normalises_location_joiners_and_prefix() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(1, "⑴对于桥台及盖梁存在渗水的病害，建议按照规范及时进行处理。"),
        _paragraph(2, "⑵对于桥面伸缩缝变形的情况，建议及时进行修复处理。"),
    )

    result = extract_recommendations(model)

    assert [item.location for item in result.records] == ["桥台、盖梁", "伸缩缝"]


def test_infers_generic_preventive_maintenance_location() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(
            1,
            "⑴建议严格按规范做好桥梁的日常检查和维护工作。⑵严禁行人在桥上同步跑动。",
        ),
    )

    result = extract_recommendations(model)

    assert [item.location for item in result.records] == ["该设施", "桥上"]


def test_category_repair_action_overrides_monitoring_marker() -> None:
    model = _model(
        _paragraph(0, "12 处理建议", heading_level=1),
        _paragraph(1, "⑴针对箱梁底部的局部纵向裂缝，应进行表面封闭处理，并定期观测裂缝发展变化状况。"),
    )

    result = extract_recommendations(model, infer_categories=True)

    assert result.records[0].category == "尽快维修"
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


def test_a_intersection_composite_parent_does_not_pollute_ec_leaf() -> None:
    result = extract_recommendations(
        _model(
            _paragraph(0, "5 结论与建议", heading_level=1),
            _paragraph(1, "5.1 检测结论", heading_level=2),
            _paragraph(2, "（1）检测结果显示人行通道存在裂缝，建议后续关注。"),
            _paragraph(3, "5.2 安全影响", heading_level=2),
            _paragraph(4, "（2）安全影响较小，暂不影响通行。"),
            _paragraph(5, "5.3 检测结果", heading_level=2),
            _paragraph(6, "（3）检测结果见图2.1.1。"),
            _paragraph(7, "5.4 处理建议", heading_level=2),
            _paragraph(8, "（4）尽快维修：人行通道：修补裂缝。"),
        ),
        infer_categories=True,
        facility_noun="人行通道",
    )

    assert [(item.category, item.location) for item in result.records] == [
        ("尽快维修", "人行通道"),
    ]


def test_facility_noun_replaces_bridge_default_for_unlabelled_leaf_item() -> None:
    model = _model(
        _paragraph(0, "主要建议", heading_level=1),
        _paragraph(1, "（1）建议定期检查。"),
    )

    assert extract_recommendations(model, infer_categories=True).records[0].location == "该设施"
    assert (
        extract_recommendations(
            model,
            infer_categories=True,
            facility_noun="人行通道",
        ).records[0].location
        == "人行通道"
    )


def test_recommendation_summary_has_all_gold_categories_and_reports_conflict() -> None:
    details = [
        {"category": "尽快维修"},
        {"category": "尽快维修"},
        {"category": "预防性养护"},
    ]

    summary = summarize_recommendations(
        details,
        source_summary="0条立即处置、2条尽快维修、1条预防性养护",
    )
    assert summary["counts"] == {
        "立即处置": 0,
        "尽快维修": 2,
        "预防性养护": 1,
    }
    assert summary["summary"] == "0条立即处置、2条尽快维修、1条预防性养护"
    assert summary["conflict"] is False

    conflict = summarize_recommendations(
        details,
        source_summary="1条立即处置、2条尽快维修、1条预防性养护",
    )
    assert conflict["conflict"] is True
    assert any(
        item["code"] == "recommendation_summary_conflict"
        for item in conflict["diagnostics"]
    )


def test_composite_container_heading_is_parent_not_direct_source() -> None:
    result = extract_recommendations(
        _model(
            _paragraph(0, "5 结论与建议", heading_level=1),
            _paragraph(1, "5.1 检测结论", heading_level=2),
            _paragraph(2, "（1）建议立即修复桥面破损。"),
            _paragraph(3, "5.4 处理建议", heading_level=2),
            _paragraph(4, "（2）尽快维修：伸缩缝：更换止水带。"),
        ),
        infer_categories=True,
    )

    assert [
        (item.index, item.category, item.location, item.content)
        for item in result.records
    ] == [
        ("（2", "尽快维修", "伸缩缝", "更换止水带。"),
    ]


def test_infers_new_repair_actions_as_quick_repair() -> None:
    actions = (
        "对砖砌体勾缝。",
        "对侧墙抹灰。",
        "桥面重新铺装。",
        "对破损面凿除重做。",
        "对裂缝灌封胶。",
        "对裂缝打磨后修补。",
        "对墙面冲洗后修补。",
        "恢复缺失的面层。",
        "重新安装脱落的栏杆。",
    )
    model = _model(
        _paragraph(0, "处理建议", heading_level=1),
        *[
            _paragraph(index + 1, f"（{index + 1}）{text}")
            for index, text in enumerate(actions)
        ],
    )

    result = extract_recommendations(model, infer_categories=True)

    assert len(result.records) == len(actions)
    assert [item.category for item in result.records] == [
        "尽快维修"
    ] * len(actions)


def test_infers_preventive_actions_as_preventive_maintenance() -> None:
    actions = (
        "加强观察病害发展变化。",
        "加强监测裂缝发展。",
        "定期复查病害情况。",
        "做好日常检查。",
        "建立技术档案。",
        "做好常规保养。",
        "定期清理排水系统。",
        "加强维护管理。",
    )
    model = _model(
        _paragraph(0, "处理建议", heading_level=1),
        *[
            _paragraph(index + 1, f"（{index + 1}）{text}")
            for index, text in enumerate(actions)
        ],
    )

    result = extract_recommendations(model, infer_categories=True)

    assert len(result.records) == len(actions)
    assert [item.category for item in result.records] == [
        "预防性养护"
    ] * len(actions)


def test_immediate_disposal_requires_explicit_urgent_evidence() -> None:
    model = _model(
        _paragraph(0, "处理建议", heading_level=1),
        _paragraph(1, "（1）支座垫石变形严重，建议尽快处理。"),
        _paragraph(2, "（2）建议恢复缺失的防撞栏杆。"),
        _paragraph(3, "（3）梁体裂缝危及结构安全，立即处置。"),
        _paragraph(4, "（4）建议立即修复桥面破损。"),
    )

    result = extract_recommendations(model, infer_categories=True)

    assert [item.category for item in result.records] == [
        "尽快维修",
        "尽快维修",
        "立即处置",
        "立即处置",
    ]


def test_generic_sentence_uses_facility_noun_not_bridge_default() -> None:
    model = _model(
        _paragraph(0, "主要建议", heading_level=1),
        _paragraph(1, "（1）建议严格按规范做好桥梁的日常检查和维护工作。"),
        _paragraph(2, "（2）桥面：定期清理。"),
    )

    default_result = extract_recommendations(model, infer_categories=True)
    underpass_result = extract_recommendations(
        model,
        infer_categories=True,
        facility_noun="人行通道",
    )

    assert [item.location for item in default_result.records] == ["该设施", "桥面"]
    assert [item.location for item in underpass_result.records] == [
        "人行通道",
        "桥面",
    ]


def test_ambiguous_category_stays_unresolved_without_invented_label() -> None:
    model = _model(
        _paragraph(0, "处理建议", heading_level=1),
        _paragraph(1, "（1）尽快维修、预防性养护：桥面：修复裂缝。"),
        _paragraph(2, "（2）立即处置或尽快维修：栏杆：更换栏杆。"),
    )

    result = extract_recommendations(model, infer_categories=True)

    assert [item.category for item in result.records] == ["", ""]
    assert result.quality_flag_codes == (
        "recommendation_category_unresolved",
        "recommendation_category_unresolved",
    )
    assert all(item["quality_flag"] == "recommendation_category_unresolved" for item in result.quality_flags)


def test_zero_filled_summary_reconciles_source_and_exposes_conflict() -> None:
    zero = summarize_recommendations(
        [],
        source_summary="3条尽快维修、1条预防性养护",
    )

    assert zero["counts"] == {"立即处置": 0, "尽快维修": 0, "预防性养护": 0}
    assert zero["summary"] == "0条立即处置、0条尽快维修、0条预防性养护"
    assert zero["formatted"] == zero["summary"]
    assert zero["source_counts"] == {
        "立即处置": 0,
        "尽快维修": 3,
        "预防性养护": 1,
    }
    assert zero["conflict"] is True
    assert any(
        item["code"] == "recommendation_summary_conflict"
        for item in zero["diagnostics"]
    )

    without_source = summarize_recommendations([])
    assert without_source["counts"] == {
        "立即处置": 0,
        "尽快维修": 0,
        "预防性养护": 0,
    }
    assert without_source["conflict"] is False

