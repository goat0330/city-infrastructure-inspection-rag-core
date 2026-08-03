from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.errorbook import aggregate_errorbook, generate_errorbook, render_errorbook_markdown


def synthetic_payloads() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    audit = {
        "pairing": {
            "label_count": 94,
            "report_count": 161,
            "status_counts": {
                "paired_exact": 90,
                "paired_fuzzy": 4,
                "missing": 0,
                "ambiguous": 0,
                "duplicate": 0,
            },
            "unresolved_report_count": 67,
            "unresolved_report_relative_paths": [r"D:\private\report.doc"],
        },
        "label_parsing": {
            "total": 94,
            "succeeded": 86,
            "failed": 8,
            "quality_flag_count": 8,
            "quality_flag_counts": {
                "first_inspection_with_trend": 2,
                "recommendation_count_mismatch": 4,
                "trend_without_previous_score": 2,
            },
        },
    }
    gold = {
        "statistics": {
            "label_count": 94,
            "record_count": 86,
            "failed_count": 8,
            "quality_flag_count": 8,
        },
        "failed": [
            {
                "error_code": "missing_defects_table",
                "label_relative_path": r"C:\private\source.docx",
                "error": "真实报告正文不应出现在聚合结果",
            }
            for _ in range(7)
        ]
        + [
            {
                "error_code": "legacy_doc_unsupported",
                "label_relative_path": r"C:\private\legacy.doc",
                "error": "原始文件全文不应出现在聚合结果",
            }
        ],
        "records": [
            {
                "summary": {"bridge_name": "真实桥梁名称"},
                "detailed_conclusion": ["真实文档正文"],
                "quality_flags": [],
            }
        ],
    }
    self_score = {"total_score": 100.0, "micro_total_score": 100.0, "macro_total_score": 100.0}
    return audit, gold, self_score


class ErrorbookAggregatorTests(unittest.TestCase):
    def test_synthetic_payloads_match_gate0_statistics_and_categories(self) -> None:
        audit, gold, self_score = synthetic_payloads()

        summary = aggregate_errorbook(
            audit,
            gold,
            self_score,
            conversion_status="incomplete",
        )

        self.assertEqual(
            summary["statistics"],
            {
                "label_count": 94,
                "report_count": 161,
                "exact_match_count": 90,
                "fuzzy_match_count": 4,
                "succeeded_count": 86,
                "failed_count": 8,
                "quality_flag_count": 8,
                "unresolved_report_count": 67,
                "gold_self_score": 100.0,
            },
        )
        self.assertEqual(
            summary["error_categories"],
            {
                "conversion_incomplete": 1,
                "gold_failure:legacy_doc_unsupported": 1,
                "gold_failure:missing_defects_table": 7,
                "label_parse_failure": 8,
                "quality_flag:first_inspection_with_trend": 2,
                "quality_flag:recommendation_count_mismatch": 4,
                "quality_flag:trend_without_previous_score": 2,
                "unresolved_reports": 67,
            },
        )

    def test_missing_fields_are_safe_and_do_not_create_fake_details(self) -> None:
        summary = aggregate_errorbook({}, {}, {})

        self.assertEqual(summary["statistics"]["label_count"], 0)
        self.assertEqual(summary["statistics"]["failed_count"], 0)
        self.assertIsNone(summary["statistics"]["gold_self_score"])
        self.assertEqual(summary["error_categories"], {})
        rendered = render_errorbook_markdown(summary)
        self.assertIn("| Gold 自评分 | — |", rendered)
        self.assertIn("- 状态：未知", rendered)
        self.assertNotIn("None", rendered)

    def test_repeated_rendering_is_deterministic_and_summary_only(self) -> None:
        audit, gold, self_score = synthetic_payloads()
        first = aggregate_errorbook(audit, gold, self_score, conversion_status="incomplete")
        second = aggregate_errorbook(audit, gold, self_score, conversion_status="incomplete")

        self.assertEqual(first, second)
        self.assertEqual(render_errorbook_markdown(first), render_errorbook_markdown(second))
        serialized = json.dumps(first, ensure_ascii=False)
        rendered = render_errorbook_markdown(first)
        for forbidden in (r"D:\private\report.doc", r"C:\private\source.docx", "真实文档正文"):
            self.assertNotIn(forbidden, serialized)
            self.assertNotIn(forbidden, rendered)

    def test_file_generator_uses_only_synthetic_json_objects(self) -> None:
        audit, gold, self_score = synthetic_payloads()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {
                "audit": root / "audit.json",
                "gold": root / "gold.json",
                "score": root / "score.json",
            }
            for key, payload in (("audit", audit), ("gold", gold), ("score", self_score)):
                paths[key].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            output = root / "nested" / "baseline.md"

            summary = generate_errorbook(
                paths["audit"],
                paths["gold"],
                paths["score"],
                output,
                conversion_status="incomplete",
            )

            self.assertEqual(summary["statistics"]["gold_self_score"], 100.0)
            self.assertEqual(output.read_text(encoding="utf-8"), render_errorbook_markdown(summary))
            self.assertNotIn(str(root), output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
