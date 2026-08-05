from src.contracts import DefectObservation
from src.extraction.defects.extractor import _clean_description, _fill_missing_indices
from src.extraction.recommendations.extractor import _infer_category
from src.extraction.summary.extractor import _normalise_bridge_name


def test_long_cover_title_is_reduced_to_facility_name() -> None:
    value = (
        "重庆至湛江公路丁家院大桥检测评估 "
        "检测项目: A外观检查;B专项检测;C桥面线形;D结构验算;E荷载试验 "
        "检测类别: 委托检测"
    )
    assert _normalise_bridge_name(value) == "重庆至湛江公路丁家院大桥"


def test_bridge_photo_reference_is_removed_but_underpass_reference_can_be_kept() -> None:
    value = "右幅第一跨梁底混凝土剥落，见照片5.1.1-1"
    assert _clean_description(value) == "右幅第一跨梁底混凝土剥落"
    assert _clean_description(value, preserve_figure_refs=True) == value


def test_missing_defect_indexes_are_filled_stably() -> None:
    records = (
        DefectObservation(index="", location="梁底", defect_type="裂缝", description="裂缝"),
        DefectObservation(index="7", location="桥台", defect_type="破损", description="破损"),
    )
    filled = _fill_missing_indices(records)
    assert [item.index for item in filled] == ["1", "7"]


def test_concrete_repair_overrides_monitoring_phrase() -> None:
    assert _infer_category("及时封闭裂缝，并定期观测裂缝发展") == "尽快维修"
    assert _infer_category("对桥面布置排水设施，保证桥面干燥") == "尽快维修"
    assert _infer_category("加强桥梁定期检查和有关养护维修工作") == "预防性养护"
