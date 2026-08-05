from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.evaluation.scorer import score_dataset
from src.extraction import extract_report


EXPECTED = {
    "2012年-杨公桥EC匝道人行通道": {
        "name": "杨公桥EC匝道人行通道",
        "date": "2013年2月",
        "defects": 2,
        "recommendations": 3,
        "summary": "0条立即处置、2条尽快维修、1条预防性养护",
    },
    "2012年-丁家院大桥": {
        "name": "丁家院大桥",
        "date": "2013年2月",
        "defects": 42,
        "recommendations": 8,
        "summary": "0条立即处置、6条尽快维修、2条预防性养护",
    },
    "2012年-上界路K38+576人行天桥": {
        "name": "上界路K38+576人行天桥",
        "date": "2012年6月12日",
        "defects": 5,
        "recommendations": 5,
        "summary": "0条立即处置、3条尽快维修、2条预防性养护",
    },
    "2013年-12-035杨公桥立交EC匝道桥": {
        "name": "杨公桥立交EC匝道桥",
        "date": "2013年12月",
        "defects": 237,
        "recommendations": 6,
        "summary": "0条立即处置、4条尽快维修、2条预防性养护",
    },
}


def _sample_id_from_name(name: str) -> str:
    if "K38+576" in name:
        return "2012年-上界路K38+576人行天桥"
    if "丁家院" in name:
        return "2012年-丁家院大桥"
    if "杨公桥立交EC" in name:
        return "2013年-12-035杨公桥立交EC匝道桥"
    if "杨公桥EC" in name and "人行通道" in name:
        return "2012年-杨公桥EC匝道人行通道"
    raise AssertionError(f"unrecognised review sample: {name}")


def test_four_worst_case_reports() -> None:
    root_value = os.environ.get("ROUND2_REVIEW_ROOT")
    if not root_value:
        pytest.skip("ROUND2_REVIEW_ROOT is not configured")
    root = Path(root_value)
    input_dir = root / "02_evidence" / "converted-input-docx"
    gold_path = root / "02_evidence" / "selected-gold" / "selected-gold.jsonl"

    predictions: list[dict[str, object]] = []
    for path in input_dir.glob("*.docx"):
        probe = extract_report(path)
        sample_id = _sample_id_from_name(probe.prediction.summary.bridge_name)
        result = extract_report(path, source_file=sample_id + ".docx")
        expected = EXPECTED[sample_id]
        assert result.prediction.summary.bridge_name == expected["name"]
        assert result.prediction.summary.report_date == expected["date"]
        assert len(result.prediction.defects) == expected["defects"]
        assert len(result.prediction.recommendations) == expected["recommendations"]
        assert result.prediction.summary.recommendations_summary == expected["summary"]
        assert all(item.index for item in result.prediction.defects)
        assert all(item.category for item in result.prediction.recommendations)
        predictions.append(result.prediction.to_dict())

    assert len(predictions) == 4
    gold = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line]
    score = score_dataset(gold, predictions)
    assert score["micro_total_score"] >= 75.0
    assert score["sections"]["defects"]["score"] >= 29.0
