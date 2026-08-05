from __future__ import annotations

import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from src.evaluation.diagnostics import diagnose_records, subset_summaries
from src.errorbook.b2 import (
    LEADERBOARD_FIELDS,
    b2_errorbook_summary,
    render_b2_errorbook_markdown,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "run_b2_benchmark.py"


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location("run_b2_benchmark", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(sample_id: str, bridge_name: str) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "summary": {
            "bridge_name": bridge_name,
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


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )


def build_manifest() -> dict[str, object]:
    return {
        "version": 1,
        "gold_path": "runs/p0-gold/gold.json",
        "records": [
            {
                "sample_id": "doc-a",
                "split": "fit",
                "stress_tags": ["large_recommendation_list", "route_overlap"],
            },
            {
                "sample_id": "doc-b",
                "split": "holdout",
                "stress_tags": ["route_overlap"],
            },
        ],
    }


class SubsetSummariesTests(unittest.TestCase):
    def test_subset_summaries_are_deterministic_and_privacy_safe(self) -> None:
        gold = [record("doc-a", "秘密桥A"), record("doc-b", "秘密桥B")]
        prediction = [copy.deepcopy(gold[0]), {"sample_id": "doc-b", "summary": {}, "defects": [], "recommendations": []}]
        metadata = {
            "doc-a": {"split": "fit", "stress_tags": ["large_recommendation_list"]},
            "doc-b": {"split": "holdout", "stress_tags": []},
        }
        diagnostics = diagnose_records(gold, prediction, metadata=metadata)
        groups = {
            "fit": ["doc-a"],
            "holdout": ["doc-b"],
            "stress": ["doc-a"],
        }
        first = subset_summaries(diagnostics["records"], groups)
        second = subset_summaries(diagnostics["records"], groups)
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"fit", "holdout", "stress"})
        self.assertEqual(first["fit"]["document_count"], 1)
        self.assertEqual(first["holdout"]["document_count"], 1)
        serialized = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("秘密桥", serialized)
        self.assertNotIn("不得进入诊断报告", serialized)
        self.assertNotIn(r"D:\private", serialized)
        self.assertNotIn("raw_text", serialized)

    def test_subset_summaries_drop_unknown_ids(self) -> None:
        diagnostics = diagnose_records([record("doc-a", "桥A")], [record("doc-a", "桥A")])
        result = subset_summaries(diagnostics["records"], {"only": ["doc-a", "missing-id"]})
        self.assertEqual(result["only"]["document_count"], 1)


class ErrorbookRendererTests(unittest.TestCase):
    def test_renderer_is_summary_only(self) -> None:
        diagnostics = diagnose_records([record("doc-a", "秘密桥A")], [record("doc-a", "秘密桥A")])
        summary = b2_errorbook_summary(diagnostics, commit="abc1234", config="test-run")
        self.assertEqual(summary["commit"], "abc1234")
        self.assertEqual(summary["prediction_records"], 1)
        markdown = render_b2_errorbook_markdown(summary)
        self.assertIn("abc1234", markdown)
        self.assertIn("prediction records: 1", markdown)
        self.assertNotIn("秘密桥", markdown)
        self.assertNotIn(r"D:\private", markdown)
        self.assertNotIn("不得进入诊断报告", markdown)

    def test_commit_sanitized_when_unexpected(self) -> None:
        summary = b2_errorbook_summary(
            {"micro": {}, "quality_flags": [], "record_count": 0, "failed_documents": 0, "weighted_total": None},
            commit=r"D:\evil\tag",
            config="c",
        )
        self.assertEqual(summary["commit"], "unknown")


class BenchmarkScriptTests(unittest.TestCase):
    def test_end_to_end_run_creates_all_artifacts(self) -> None:
        module = load_benchmark_module()
        gold = [record("doc-a", "秘密桥A"), record("doc-b", "秘密桥B")]
        prediction = [copy.deepcopy(gold[0]), copy.deepcopy(gold[1])]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gold_path = root / "gold.jsonl"
            prediction_path = root / "predictions.jsonl"
            manifest_path = root / "manifest.json"
            output_dir = root / "out"
            write_jsonl(gold_path, gold)
            write_jsonl(prediction_path, prediction)
            manifest_path.write_text(json.dumps(build_manifest(), ensure_ascii=False), encoding="utf-8")

            exit_code = module.main(
                [
                    "--gold",
                    str(gold_path),
                    "--predictions",
                    str(prediction_path),
                    "--manifest",
                    str(manifest_path),
                    "--output-dir",
                    str(output_dir),
                    "--commit",
                    "abc1234",
                    "--config",
                    "smoke",
                    "--notes",
                    "unit smoke run",
                ]
            )
            self.assertEqual(exit_code, 0)

            score = json.loads((output_dir / "score.json").read_text(encoding="utf-8"))
            self.assertEqual(score["record_count"], 2)

            diagnostics = json.loads((output_dir / "diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(diagnostics["record_count"], 2)
            self.assertEqual(diagnostics["failed_documents"], 0)
            text = (output_dir / "diagnostics.json").read_text(encoding="utf-8")
            self.assertNotIn(r"D:\private", text)
            self.assertNotIn("不得进入诊断报告", text)

            summaries = json.loads((output_dir / "summaries.json").read_text(encoding="utf-8"))
            self.assertEqual(summaries["split"]["fit"]["document_count"], 1)
            self.assertEqual(summaries["split"]["holdout"]["document_count"], 1)
            self.assertEqual(summaries["stress"]["route_overlap"]["document_count"], 2)
            self.assertEqual(summaries["stress"]["large_recommendation_list"]["document_count"], 1)

            errorbook = (output_dir / "errorbook.md").read_text(encoding="utf-8")
            self.assertIn("abc1234", errorbook)
            self.assertIn("prediction records: 2", errorbook)
            self.assertNotIn(r"D:\private", errorbook)

            with (output_dir / "leaderboard.csv").open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(tuple(rows[0]), LEADERBOARD_FIELDS)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][0], "abc1234")

    def test_missing_prediction_is_recorded_as_failure(self) -> None:
        module = load_benchmark_module()
        gold = [record("doc-a", "秘密桥A"), record("doc-b", "秘密桥B")]
        prediction = [copy.deepcopy(gold[0])]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gold_path = root / "gold.jsonl"
            prediction_path = root / "predictions.jsonl"
            manifest_path = root / "manifest.json"
            output_dir = root / "out"
            write_jsonl(gold_path, gold)
            write_jsonl(prediction_path, prediction)
            manifest_path.write_text(json.dumps(build_manifest(), ensure_ascii=False), encoding="utf-8")

            exit_code = module.main(
                [
                    "--gold",
                    str(gold_path),
                    "--predictions",
                    str(prediction_path),
                    "--manifest",
                    str(manifest_path),
                    "--output-dir",
                    str(output_dir),
                    "--commit",
                    "abc1234",
                    "--config",
                    "smoke-fail",
                ]
            )
            self.assertEqual(exit_code, 1)
            diagnostics = json.loads((output_dir / "diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(diagnostics["missing_sample_ids"], ["doc-b"])
            self.assertEqual(diagnostics["failed_documents"], 1)
            errorbook = (output_dir / "errorbook.md").read_text(encoding="utf-8")
            self.assertIn("failed documents: 1", errorbook)


if __name__ == "__main__":
    unittest.main()
