from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.prepare_semantic_calibration import build_calibration


ROOT = Path(__file__).resolve().parents[2]


def _require_calibration_fixtures() -> None:
    required = ROOT / "runs" / "b2-night" / "eval-manifest.json"
    if not required.is_file():
        pytest.skip("semantic calibration run artifacts are not bundled in the source review package")


def test_calibration_has_eight_ordered_slots_and_explicit_duplicate() -> None:
    _require_calibration_fixtures()
    result = build_calibration()
    assert result["slot_count"] == 8
    assert result["unique_sample_count"] == 7
    assert result["duplicate_sample_ids"] == ["2012年-杨公桥A叉口人行通道"]
    assert [item["slot"] for item in result["slots"]] == list(range(1, 9))
    assert result["slots"][1]["sample_id"] == "2012年-杨公桥EC匝道人行通道"
    assert result["slots"][7]["sample_id"] == "2012年-茶亭大桥"


def test_calibration_references_existing_official_inputs() -> None:
    _require_calibration_fixtures()
    result = build_calibration()
    assert result["all_sources_exist"] is True
    assert result["all_baselines_exist"] is True
    assert all(item["candidate_count"] > 0 for item in result["slots"])
    assert result["slots"][7]["index"]["status"] == "available"
    index_dir = ROOT / result["slots"][7]["index"]["path"]
    assert (index_dir / "metadata.jsonl").is_file()
    assert (index_dir / "vectors.npy").is_file()


def test_calibration_is_json_serialisable() -> None:
    _require_calibration_fixtures()
    result = build_calibration()
    encoded = json.dumps(result, ensure_ascii=False)
    assert "semantic-calibration-8-v1" in encoded
