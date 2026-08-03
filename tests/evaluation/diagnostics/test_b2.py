from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from src.evaluation.diagnostics import diagnose_files, diagnose_records, write_diagnostics


def record(sample_id: str = "doc-1") -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "summary": {
            "bridge_name": "秘密桥",
            "bridge_id": "B-1",
            "overall_score": "87.060",
        },
        "defects": [
            {
                "index": "1",
                "location": "桥面",
                "defect_type": "裂缝",
                "description": "纵向裂缝",
                "is_new": "是",
                "previous_status": "无",
                "development": "新增",
                "evidence": [
                    {
                        "source_file": r"D:\private\report.docx",
                        "block_index": 4,
                        "table_index": 1,
                        "row_index": 2,
                        "column_index": 3,
                        "raw_text": "不得进入诊断报告",
                    }
                ],
            }
        ],
        "recommendations": [
            {"index": "1", "category": "尽快维修", "content": "封闭裂缝", "location": "桥面"}
        ],
    }


class B2DiagnosticsTests(unittest.TestCase):
    def test_field_categories_and_structural_anchor_redaction(self) -> None:
        gold = record()
        prediction = copy.deepcopy(gold)
        prediction["summary"].pop("bridge_id")
        prediction["defects"][0]["location"], prediction["defects"][0]["defect_type"] = (
            prediction["defects"][0]["defect_type"],
            prediction["defects"][0]["location"],
        )
        prediction["defects"][0]["description"] = "改写描述"
        prediction["recommendations"].append(
            {"index": "2", "category": "预防养护", "content": "巡查", "location": "护栏"}
        )

        result = diagnose_records(
            [gold],
            [prediction],
            metadata={"doc-1": {"facility": "设施甲", "template_cluster": "p1|t1"}},
        )
        sections = result["records"][0]["sections"]
        self.assertEqual(sections["summary"]["category_counts"]["missing"], 1)
        self.assertGreaterEqual(sections["defects"]["category_counts"]["wrong_column"], 1)
        self.assertEqual(sections["defects"]["category_counts"]["description_difference"], 1)
        self.assertEqual(sections["recommendations"]["category_counts"]["extra"], 1)
        self.assertEqual(sections["defects"]["evidence"]["raw_text_redacted_count"], 2)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("秘密桥", serialized)
        self.assertNotIn("不得进入诊断报告", serialized)
        self.assertNotIn(r"D:\private\report.docx", serialized)
        self.assertEqual(result["facility_buckets"]["设施甲"]["document_count"], 1)
        self.assertEqual(result["template_buckets"]["p1|t1"]["document_count"], 1)

    def test_micro_macro_and_defect_count_buckets_are_deterministic(self) -> None:
        gold = [record("a"), record("b")]
        prediction = [copy.deepcopy(gold[0]), {"sample_id": "b", "summary": {}, "defects": [], "recommendations": []}]
        first = diagnose_records(gold, prediction)
        second = diagnose_records(gold, prediction)
        self.assertEqual(first, second)
        self.assertIn("micro", first)
        self.assertIn("macro", first)
        self.assertEqual(first["defect_count_buckets"]["1-10"]["document_count"], 2)
        self.assertLess(first["micro"]["defects"]["recall"], 1.0)
        self.assertLess(first["macro"]["defects"]["f1"], 1.0)
        self.assertEqual(first["bucket_metadata"], {"facility_available": True, "template_available": False})

    def test_json_and_jsonl_files_write_summary_only_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gold_path = root / "gold.jsonl"
            prediction_path = root / "prediction.json"
            output_path = root / "nested" / "diagnostics.json"
            gold_path.write_text(json.dumps(record(), ensure_ascii=False) + "\n", encoding="utf-8")
            prediction_path.write_text(json.dumps([record()], ensure_ascii=False), encoding="utf-8")
            loaded = diagnose_files(gold_path, prediction_path)
            written = write_diagnostics(gold_path, prediction_path, output_path)
            self.assertEqual(loaded, written)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8")), written)
            self.assertNotIn(str(root), output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
