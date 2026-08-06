from src.contracts.prediction import BridgeSummary, InspectionPrediction
from src.rendering.submission_document import build_submission_document


def test_submission_title_and_missing_values_use_final_policy():
    prediction = InspectionPrediction(summary=BridgeSummary(bridge_name="测试大桥"))
    document = build_submission_document(prediction)
    assert document.scalars["report_title"] == "测试大桥·信息提取报告"
    assert document.scalars["report_date"] == "无"
    assert "未提取到" not in str(document.to_dict())
