from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_gold_and_prediction_schemas_are_distinct() -> None:
    gold = json.loads((ROOT / "schema/gold_record.schema.json").read_text(encoding="utf-8"))
    prediction = json.loads((ROOT / "schema/prediction_record.schema.json").read_text(encoding="utf-8"))

    assert "split" in gold["required"]
    assert "provenance" in gold["required"]
    assert "split" not in prediction["properties"]
    assert "provenance" not in prediction["properties"]
    assert "schema_version" in prediction["required"]
    assert set(gold["properties"]["summary"]["properties"]) == set(
        prediction["properties"]["summary"]["properties"]
    )
