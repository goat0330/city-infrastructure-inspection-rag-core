from __future__ import annotations

import unittest

from src.evaluation import AlignmentError, align_prediction_records


class PredictionAlignmentTests(unittest.TestCase):
    def test_manifest_source_paths_select_and_order_raw_predictions(self) -> None:
        manifest = [
            {"sample_id": "2012年-B", "source_docx": "runs/p0-converted/2012年/b.docx"},
            {"sample_id": "2012年-A", "source_docx": "runs/p0-converted/2012年/a.docx"},
        ]
        raw = [
            {"sample_id": "raw-a", "source_file": "2012年/a.docx", "summary": {}},
            {"sample_id": "raw-extra", "source_file": "2012年/extra.docx", "summary": {}},
            {"sample_id": "raw-b", "source_file": "2012年/b.docx", "summary": {}},
        ]

        aligned, stats = align_prediction_records(manifest, raw)

        self.assertEqual([item["sample_id"] for item in aligned], ["2012年-B", "2012年-A"])
        self.assertEqual([item["source_file"] for item in aligned], ["2012年/b.docx", "2012年/a.docx"])
        self.assertEqual(stats["excluded_prediction_count"], 1)
        self.assertEqual(stats["sample_id_rewritten_count"], 2)

    def test_missing_or_ambiguous_source_fails_closed(self) -> None:
        manifest = [{"sample_id": "a", "source_docx": "runs/x/a.docx"}]
        with self.assertRaises(AlignmentError):
            align_prediction_records(manifest, [{"source_file": "x/b.docx"}])
        with self.assertRaises(AlignmentError):
            align_prediction_records(
                manifest,
                [
                    {"source_file": "x/a.docx"},
                    {"source_file": "runs/x/a.docx"},
                ],
            )

    def test_legacy_id_only_manifest_preserves_existing_contract(self) -> None:
        manifest = [{"sample_id": "a"}]
        raw = [{"sample_id": "a", "summary": {}}]
        aligned, stats = align_prediction_records(manifest, raw)
        self.assertEqual(aligned, raw)
        self.assertEqual(stats["mode"], "sample-id")
