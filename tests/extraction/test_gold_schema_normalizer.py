from src.contracts import (
    BridgeSummary, DefectObservation, DocumentModel, ParagraphBlock, Recommendation, SourceAnchor
)
from src.extraction.summary import SummaryCandidate, SummaryExtraction
from src.extraction.gold_schema_normalizer import (
    canonicalize_defect_location,
    canonicalize_defect_type,
    canonicalize_recommendation_location,
    compose_gold_overall_conclusion,
    compose_gold_risk_points,
    gate_previous_summary,
    normalize_gold_schema_mode,
)


def test_defect_location_keeps_detail_in_description_and_refines_component():
    assert canonicalize_defect_location(
        "拱腰", "2#孔右拱腰距左洞口33m～40m处局部泛碱", "泛碱"
    ) == "右拱腰"
    assert canonicalize_defect_location(
        "左幅上部", "板底局部有车辆刮痕", "刮痕"
    ) == "左幅板底"
    assert canonicalize_defect_location(
        "桥面系", "防撞栏杆局部锈蚀", "锈蚀"
    ) == "桥面系防撞栏杆"
    assert canonicalize_defect_location(
        "桥面系", "距左侧桥台内10m处桥面局部露筋", "露筋"
    ) == "桥面系"


def test_defect_type_prefers_source_category_and_only_cleans_layout_noise():
    assert canonicalize_defect_type("破损、", "防撞栏杆局部破损") == "破损"
    assert canonicalize_defect_type("泛碱、泛碱", "右拱腰渗水泛碱") == "泛碱、泛碱"
    assert canonicalize_defect_type("锈蚀、破损", "防撞栏杆锈蚀、破损") == "锈蚀破损"


def test_recommendation_locations_are_maintenance_objects():
    assert canonicalize_recommendation_location(
        "盖梁", "对于盖梁露筋，挡块破损等病害，建议及时维修处理。"
    ) == "盖梁、挡块"
    assert canonicalize_recommendation_location(
        "桥面", "桥面铺装局部破损、露筋等病害，建议对桥面及时进行修复。"
    ) == "桥面铺装"
    assert canonicalize_recommendation_location(
        "该桥伸缩缝", "该桥伸缩缝保护带局部破损，建议及时修复。"
    ) == "伸缩缝"
    assert canonicalize_recommendation_location(
        "桥面", "由于桥面存在破损，伸缩缝破损，建议对桥面和伸缩缝及时进行修复。"
    ) == "桥面、伸缩缝"


def test_overall_conclusion_uses_component_level_phrases_not_coordinates():
    defects = (
        DefectObservation(location="右幅桥面", defect_type="破损", description="距2#伸缩缝46m处桥面铺装破损"),
        DefectObservation(location="右幅上部", defect_type="裂缝", description="第1跨3#梁腹板竖向裂缝，宽0.05mm"),
        DefectObservation(location="右幅桥台", defect_type="渗水", description="0#桥台盖梁局部渗水"),
    )
    value = compose_gold_overall_conclusion(BridgeSummary(), defects)
    assert value.startswith("本次定检结果表明，")
    assert "桥面系桥面铺装破损" in value
    assert "上部结构腹板裂缝" in value
    assert "下部结构盖梁渗水" in value
    assert "46m" not in value
    assert "3#梁" not in value


def test_risk_points_do_not_require_invented_consequence():
    defects = (
        DefectObservation(location="梁底", defect_type="纵向裂缝", description="梁底纵向裂缝"),
        DefectObservation(location="伸缩缝", defect_type="破损", description="伸缩缝保护带破损"),
    )
    value = compose_gold_risk_points("", defects)
    assert "梁底纵向裂缝" in value
    assert "伸缩缝保护带破损" in value
    assert "承载" not in value
    assert "耐久" not in value


def test_gold_schema_mode_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("GOLD_SCHEMA_MODE", raising=False)
    assert normalize_gold_schema_mode() == "legacy"
    monkeypatch.setenv("GOLD_SCHEMA_MODE", "v18")
    assert normalize_gold_schema_mode() == "v18"


def test_external_location_vocab_is_authoritative_and_unknown_location_drops_coordinates():
    assert canonicalize_defect_location(
        "桥面系", "防撞栏杆局部锈蚀", "锈蚀", canonical_locations={"桥面系": 99}
    ) == "桥面系"
    assert canonicalize_defect_location(
        "1#孔右拱腰距左洞口33m处", "1#孔右拱腰局部泛碱", "泛碱", canonical_locations={"右拱腰": 20}
    ) == "右拱腰"


def test_type_vocab_preserves_unknown_source_word_and_emits_warning():
    warnings: list[dict[str, object]] = []
    assert canonicalize_defect_type(
        "渗水、泛碱", "板底渗水泛碱", canonical_types={"渗水、泛碱": 8}, warnings=warnings
    ) == "渗水、泛碱"
    assert warnings == []
    assert canonicalize_defect_type(
        "局部粉化", "主梁局部粉化", canonical_types={"裂缝": 1}, warnings=warnings
    ) == "局部粉化"
    assert warnings[-1]["quality_flag"] == "gold_schema_unknown_defect_type"


def _history_fixture(anchor_text: str, candidate_kind: str = "previous_detection"):
    source = SourceAnchor("历史桥.docx", 0, anchor_text, paragraph_index=0)
    document = DocumentModel(
        source_file="历史桥.docx",
        blocks=(ParagraphBlock(0, anchor_text, source),),
    )
    candidate = SummaryCandidate(
        field="previous_overall_score",
        value="85.38",
        source_kind=candidate_kind,
        source=source,
        priority=100,
        label="上一次检测BCI",
    )
    extraction = SummaryExtraction(
        summary=BridgeSummary(previous_overall_score="85.38", previous_overall_grade="B级"),
        candidates={
            "previous_overall_score": (candidate,),
            "previous_overall_grade": (
                SummaryCandidate(
                    field="previous_overall_grade",
                    value="B级",
                    source_kind=candidate_kind,
                    source=source,
                    priority=100,
                    label="上一次检测总体等级",
                ),
            ),
        },
        sources={},
        quality_flags=(),
        field_states={},
    )
    return extraction, document


def test_previous_gate_requires_explicit_history_anchor():
    extraction, document = _history_fixture("2014年检测，BCI=85.38，总体等级B级")
    gated = gate_previous_summary(extraction, document)
    assert gated.summary.previous_overall_score == "无"
    assert gated.summary.previous_overall_grade == "无"
    assert any(flag.get("quality_flag") == "gold_schema_previous_without_anchor" for flag in gated.quality_flags)


def test_previous_gate_keeps_true_anchored_history_but_not_filename_only_candidate():
    extraction, document = _history_fixture("上次检测：BCI=85.38，总体等级B级")
    gated = gate_previous_summary(extraction, document)
    assert gated.summary.previous_overall_score == "85.38"
    assert gated.summary.previous_overall_grade == "B级"

    filename_only, document = _history_fixture("上次检测结果见对比章节", candidate_kind="filename_history")
    gated_filename = gate_previous_summary(filename_only, document)
    assert gated_filename.summary.previous_overall_score == "无"
    assert gated_filename.summary.previous_overall_grade == "无"


def test_gold_risk_points_ignore_predictive_assessment_generic_sentence():
    defects = (
        DefectObservation(location="梁底", defect_type="纵向裂缝", description="梁底纵向裂缝"),
        DefectObservation(location="伸缩缝", defect_type="破损", description="伸缩缝保护带破损"),
        DefectObservation(location="桥台", defect_type="渗水、泛碱", description="桥台渗水、泛碱"),
    )
    value = compose_gold_risk_points(
        "此类病害对结构安全暂时不会带来安全隐患，但长期可能影响耐久性。",
        defects,
    )
    assert "此类病害" not in value
    assert "梁底纵向裂缝" in value
    assert "伸缩缝保护带破损" in value
    assert "桥台渗水泛碱" in value


def test_gold94_frequency_vocab_can_be_loaded_from_json(tmp_path, monkeypatch):
    from src.extraction.gold_schema_normalizer import (
        GOLD_SCHEMA_LOCATION_VOCAB_ENV,
        GOLD_SCHEMA_TYPE_VOCAB_ENV,
        gold_schema_location_vocab,
        gold_schema_type_vocab,
    )

    location_path = tmp_path / "locations.json"
    location_path.write_text('{"右拱腰": 12, "主梁": 9}', encoding="utf-8")
    type_path = tmp_path / "types.json"
    type_path.write_text('{"defect_types": {"裂缝": 21, "渗水、泛碱": 8}}', encoding="utf-8")
    monkeypatch.setenv(GOLD_SCHEMA_LOCATION_VOCAB_ENV, str(location_path))
    monkeypatch.setenv(GOLD_SCHEMA_TYPE_VOCAB_ENV, str(type_path))

    locations, external_locations = gold_schema_location_vocab()
    types, external_types = gold_schema_type_vocab()
    assert external_locations is True
    assert locations == {"右拱腰", "主梁"}
    assert external_types is True
    assert types == {"裂缝", "渗水、泛碱"}


def test_oc_uses_conclusion_evidence_and_report_grade_when_later_conclusion_conflicts():
    from src.extraction.gold_schema_normalizer import extract_conclusion_evidence

    raw_lines = (
        "|检测结果 |一、外观检查|",
        "|         |桥面系：|",
        "|         |车行道桥面铺装局部破损、露筋，防撞护栏锈蚀。|",
        "|         |上部结构：|",
        "|         |板底均有涂层，状况良好，板底1条纵裂，且渗水泛碱。|",
        "|         |下部结构：|",
        "|         |桥台基础整体状况良好，左、右幅桥台均有渗水现象，左幅1#墩盖梁局部渗水。|",
        "桥梁BCI=87.18，整体技术状况等级评定为B级，为良好状态。",
        "结构现有承载能力满足满足设计荷载等级要求。",
        "试验桥跨结构强度满足设计荷载要求。",
        "检测结论：整体技术状况等级评定为A级，为完好状态。",
    )
    blocks = []
    for index, raw in enumerate(raw_lines):
        source = SourceAnchor("K39.docx", index, raw, paragraph_index=index)
        blocks.append(ParagraphBlock(index, raw, source))
    document = DocumentModel(source_file="K39.docx", blocks=tuple(blocks))
    evidence = extract_conclusion_evidence(document)
    value = compose_gold_overall_conclusion(
        BridgeSummary(bridge_name="K39+380上跨车行桥", overall_grade="B级"),
        (),
        facility_name="K39+380上跨车行桥",
        evidence_texts=evidence,
    )
    assert "桥梁桥面系存在铺装局部破损" in value
    assert "上部结构板底有涂层，但存在1条纵向裂缝且渗水泛碱" in value
    assert "下部结构桥台及盖梁存在渗水现象" in value
    assert "结构检算及荷载试验均满足设计荷载等级要求" in value
    assert "B级（良好状态）" in value
    assert "A级（完好状态）" not in value


def test_history_anchor_vocab_path_is_configurable_and_malformed_file_falls_back(tmp_path, monkeypatch):
    from src.extraction.gold_schema_normalizer import (
        GOLD_SCHEMA_HISTORY_ANCHOR_ENV,
        gold_schema_history_anchors,
    )

    path = tmp_path / "history.json"
    path.write_text('{"history_anchors": ["与上年度检测对比", "历年检测"]}', encoding="utf-8")
    monkeypatch.setenv(GOLD_SCHEMA_HISTORY_ANCHOR_ENV, str(path))
    assert gold_schema_history_anchors() == ("与上年度检测对比", "历年检测")

    extraction, document = _history_fixture("与上年度检测对比：BCI=85.38，总体等级B级")
    gated = gate_previous_summary(extraction, document)
    assert gated.summary.previous_overall_score == "85.38"

    path.write_text("{broken", encoding="utf-8")
    assert "上次检测" in gold_schema_history_anchors()
    no_anchor_extraction, no_anchor_document = _history_fixture("此前年度对照：BCI=85.38，总体等级B级")
    gated_fallback = gate_previous_summary(no_anchor_extraction, no_anchor_document)
    assert gated_fallback.summary.previous_overall_score == "无"


def test_gold94_vocab_files_gracefully_fallback_when_missing_empty_or_malformed(tmp_path, monkeypatch):
    from src.extraction.gold_schema_normalizer import (
        GOLD_SCHEMA_LOCATION_VOCAB_ENV,
        GOLD_SCHEMA_TYPE_VOCAB_ENV,
        gold_schema_location_vocab,
        gold_schema_type_vocab,
    )

    missing = tmp_path / "missing.json"
    monkeypatch.setenv(GOLD_SCHEMA_LOCATION_VOCAB_ENV, str(missing))
    locations, external = gold_schema_location_vocab()
    assert external is False and "右拱腰" in locations

    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(GOLD_SCHEMA_TYPE_VOCAB_ENV, str(empty))
    types, external = gold_schema_type_vocab()
    assert external is False and "裂缝" in types

    malformed = tmp_path / "malformed.json"
    malformed.write_text("[not-json", encoding="utf-8")
    monkeypatch.setenv(GOLD_SCHEMA_LOCATION_VOCAB_ENV, str(malformed))
    locations, external = gold_schema_location_vocab()
    assert external is False and "桥面系" in locations


def test_risk_point_priority_is_stable_under_input_order():
    defects = (
        DefectObservation(location="支座", defect_type="剪切变形", description="支座剪切变形"),
        DefectObservation(location="支座", defect_type="螺钉缺失", description="支座螺钉缺失"),
        DefectObservation(location="梁底", defect_type="纵向裂缝", description="梁底纵向裂缝"),
        DefectObservation(location="伸缩缝", defect_type="破损", description="伸缩缝保护带破损"),
        DefectObservation(location="桥台", defect_type="渗水、泛碱", description="桥台渗水泛碱"),
    )
    forward = compose_gold_risk_points("", defects)
    backward = compose_gold_risk_points("", tuple(reversed(defects)))
    assert forward == backward
    assert forward.startswith("支座剪切变形及螺钉缺失")
    assert "影响结构耐久性" not in forward


def test_unnamed_bridge_facility_card_preserves_bridge_system_schema():
    evidence = (
        "桥面系：铺装磨损破损、伸缩缝保护带破损高差明显等病害。",
        "上部结构：梁底有刮痕、局部破损、露筋、纵裂、渗水泛碱等病害。",
        "下部结构：桥台、盖梁存在渗水病害。",
        "主体结构当前承载能力满足汽-超20，挂-120级荷载等级要求。",
        "整体技术状况等级评定为B级，为良好状态。",
        "存在一定程度的病害，应及时采取必要的整治措施。",
    )
    value = compose_gold_overall_conclusion(
        BridgeSummary(bridge_name="上界路K45+735无名桥", overall_grade="B级"),
        (),
        facility_name="上界路K45+735无名桥",
        evidence_texts=evidence,
    )
    assert value.startswith("本次定检结果表明，桥面系")
    assert "上部结构" in value and "下部结构" in value
    assert "主体结构承载能力满足汽-超20，挂-120级荷载等级要求" in value


def test_gold94_frequency_and_list_vocab_shapes_are_loaded(tmp_path, monkeypatch):
    import json
    from src.extraction.gold_schema_normalizer import (
        GOLD_SCHEMA_LOCATION_VOCAB_ENV,
        GOLD_SCHEMA_TYPE_VOCAB_ENV,
        gold_schema_location_vocab,
        gold_schema_type_vocab,
    )

    location_path = tmp_path / "locations.json"
    type_path = tmp_path / "types.json"
    location_path.write_text(
        json.dumps({"location_frequency": {"右幅桥面": 8}}, ensure_ascii=False),
        encoding="utf-8",
    )
    type_path.write_text(
        json.dumps({"type_vocab": ["剥落", "划痕"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv(GOLD_SCHEMA_LOCATION_VOCAB_ENV, str(location_path))
    monkeypatch.setenv(GOLD_SCHEMA_TYPE_VOCAB_ENV, str(type_path))

    assert gold_schema_location_vocab() == (frozenset({"右幅桥面"}), True)
    assert gold_schema_type_vocab() == (frozenset({"剥落", "划痕"}), True)


def test_location_normalizer_keeps_side_and_removes_member_number():
    assert canonicalize_defect_location(
        "右幅1#伸缩缝", "右幅1#伸缩缝保护带破损", "破损"
    ) == "右幅伸缩缝"
    assert canonicalize_defect_location(
        "距3#墩4m处右幅4#跨3#与右幅4#跨4#板间",
        "距3#墩4m处右幅4#跨3#与右幅4#跨4#板间渗水",
        "渗水",
    ) == "右幅板间"
