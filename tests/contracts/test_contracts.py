import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.contracts import (  # noqa: E402
    BridgeSummary,
    DefectObservation,
    DocumentModel,
    InspectionPrediction,
    ParagraphBlock,
    SourceAnchor,
    StageStatus,
)


class ContractTests(unittest.TestCase):
    def test_source_anchor_has_word_location_without_page_claim(self) -> None:
        anchor = SourceAnchor("sample.docx", 2, "病害文本", table_index=1, row_index=4, column_index=2)
        self.assertEqual(anchor.to_dict()["row_index"], 4)
        self.assertNotIn("page_number", anchor.to_dict())

    def test_document_and_prediction_are_serializable(self) -> None:
        anchor = SourceAnchor("sample.docx", 0, "桥梁名称", paragraph_index=0)
        document = DocumentModel("sample.docx", (ParagraphBlock(0, "桥梁名称", anchor),))
        prediction = InspectionPrediction(
            summary=BridgeSummary(bridge_name="示例桥"),
            defects=(DefectObservation(location="桥面", defect_type="裂缝", evidence=(anchor,)),),
        )
        self.assertEqual(document.to_dict()["source_file"], "sample.docx")
        self.assertEqual(prediction.to_dict()["summary"]["bridge_name"], "示例桥")

    def test_stage_status_is_stable(self) -> None:
        self.assertEqual(StageStatus.SUCCEEDED.value, "succeeded")
