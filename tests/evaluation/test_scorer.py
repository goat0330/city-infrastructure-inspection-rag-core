from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from src.evaluation import score_dataset  # noqa: E402


def record() -> dict[str, object]:
    return {
        "sample_id": "synthetic-1",
        "summary": {
            "bridge_name": "示例桥",
            "report_date": "2026年8月",
            "overall_score": "87.060",
            "overall_grade": "B级",
        },
        "detailed_conclusion": ["桥面存在裂缝，应及时维修。"],
        "recommendations": [
            {
                "index": "1",
                "category": "尽快维修",
                "content": "对裂缝进行封闭处理",
                "location": "桥面",
            }
        ],
        "defects": [
            {
                "index": "1",
                "location": "桥面",
                "defect_type": "裂缝",
                "description": "桥面存在纵向裂缝",
                "is_new": "否",
                "previous_status": "无",
                "development": "无",
            },
            {
                "index": "2",
                "location": "护栏",
                "defect_type": "破损",
                "description": "护栏局部破损",
                "is_new": "是",
                "previous_status": "无",
                "development": "新增",
            },
        ],
        "causes": ["混凝土老化导致裂缝"],
        "treatments": ["封闭裂缝并加强巡查"],
        "safety_impact": ["裂缝影响行车安全"],
    }


class ScorerTests(unittest.TestCase):
    def test_gold_vs_gold_is_100(self) -> None:
        gold = record()
        result = score_dataset([gold], [copy.deepcopy(gold)])
        self.assertEqual(result["total_score"], 100.0)
        for section in result["sections"].values():
            self.assertEqual(section["f1"], 1.0)

    def test_empty_prediction_is_near_zero(self) -> None:
        result = score_dataset([record()], [{"sample_id": "synthetic-1"}])
        self.assertLessEqual(result["total_score"], 1.0)
        self.assertEqual(result["sections"]["defects"]["recall"], 0.0)

    def test_deleting_a_defect_row_lowers_score(self) -> None:
        gold = record()
        prediction = copy.deepcopy(gold)
        prediction["defects"].pop()
        result = score_dataset([gold], [prediction])
        self.assertLess(result["total_score"], 100.0)
        self.assertLess(result["sections"]["defects"]["recall"], 1.0)
        self.assertEqual(result["sections"]["defects"]["counts"]["false_negative"], 1)

    def test_tampering_with_score_lowers_score(self) -> None:
        gold = record()
        prediction = copy.deepcopy(gold)
        prediction["summary"]["overall_score"] = "80.00"
        result = score_dataset([gold], [prediction])
        self.assertLess(result["total_score"], 100.0)
        self.assertEqual(result["sections"]["summary"]["counts"]["false_negative"], 1)
        self.assertEqual(result["sections"]["summary"]["counts"]["false_positive"], 1)

    def test_rows_are_one_to_one_and_text_has_partial_fact_credit(self) -> None:
        gold = record()
        prediction = copy.deepcopy(gold)
        prediction["recommendations"].append(copy.deepcopy(prediction["recommendations"][0]))
        prediction["causes"] = ["混凝土老化"]
        result = score_dataset([gold], [prediction])
        self.assertEqual(result["sections"]["recommendations"]["counts"]["false_positive"], 1)
        causes = result["sections"]["causes"]
        self.assertGreater(causes["recall"], 0.0)
        self.assertLess(causes["recall"], 1.0)
        self.assertTrue(causes["missing"])

    def test_cli_accepts_json_and_jsonl_paths(self) -> None:
        gold = record()
        prediction = copy.deepcopy(gold)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp_dir:
            temp = Path(temp_dir)
            gold_path = temp / "gold.jsonl"
            prediction_path = temp / "predictions.json"
            output_path = temp / "score.json"
            gold_path.write_text(json.dumps(gold, ensure_ascii=False) + "\n", encoding="utf-8")
            prediction_path.write_text(json.dumps([prediction], ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts" / "score_predictions.py"),
                    "--gold",
                    str(gold_path),
                    "--predictions",
                    str(prediction_path),
                    "--output",
                    str(output_path),
                ],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(completed.stdout, "")
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["total_score"], 100.0)

    def test_dataset_reports_macro_and_micro_scores(self) -> None:
        good = record()
        bad = copy.deepcopy(good)
        bad["sample_id"] = "synthetic-2"
        empty = {"sample_id": "synthetic-2"}
        result = score_dataset([good, bad], [copy.deepcopy(good), empty])
        self.assertIn("micro_total_score", result)
        self.assertIn("macro_total_score", result)
        self.assertIn("macro_f1", result["sections"]["defects"])
        self.assertLess(result["macro_total_score"], 100.0)


if __name__ == "__main__":
    unittest.main()
