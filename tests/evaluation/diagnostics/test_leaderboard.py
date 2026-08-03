from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.errorbook.b2 import (
    LEADERBOARD_FIELDS,
    append_leaderboard_entry,
    entry_from_diagnostics,
    load_leaderboard,
    normalize_leaderboard_entry,
    write_leaderboard,
)


def diagnostics() -> dict[str, object]:
    return {
        "summary_score": 18.5,
        "defect_precision": 0.8,
        "defect_recall": 0.75,
        "defect_f1": 0.774194,
        "recommendation_f1": 0.6,
        "weighted_total": 82.0,
        "failed_documents": 2,
    }


class LeaderboardTests(unittest.TestCase):
    def test_entry_schema_and_diagnostics_projection(self) -> None:
        entry = entry_from_diagnostics(diagnostics(), commit="abc123", config="screen-a", runtime=12.5)
        self.assertEqual(tuple(entry), LEADERBOARD_FIELDS)
        self.assertEqual(entry["defect_f1"], 0.774194)
        self.assertEqual(entry["failed_documents"], 2)
        with self.assertRaises(ValueError):
            normalize_leaderboard_entry({"commit": "abc"})

    def test_csv_is_sorted_and_round_trips(self) -> None:
        lower = entry_from_diagnostics({**diagnostics(), "weighted_total": 70}, commit="z", config="late")
        higher = entry_from_diagnostics(diagnostics(), commit="a", config="early", notes=r"D:\private\text")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "leaderboard.csv"
            rows = write_leaderboard(path, [lower, higher])
            self.assertEqual([row["commit"] for row in rows], ["a", "z"])
            self.assertEqual(load_leaderboard(path), rows)
            self.assertNotIn(r"D:\private\text", path.read_text(encoding="utf-8"))
            appended = append_leaderboard_entry(path, {**lower, "commit": "b"})
            self.assertEqual([row["commit"] for row in appended], ["a", "b", "z"])


if __name__ == "__main__":
    unittest.main()
