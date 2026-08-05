import json
from pathlib import Path
import importlib.util

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "select_round2_narrative_samples.py"
spec = importlib.util.spec_from_file_location("select_samples", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_frozen_sample_sets_are_disjoint_and_balanced():
    assert len(module.CORE_SAMPLE_IDS) == 8
    assert len(module.MEDIUM_SAMPLE_IDS) == 8
    assert set(module.CORE_SAMPLE_IDS).isdisjoint(module.MEDIUM_SAMPLE_IDS)
    assert any("人行通道" in item for item in module.CORE_SAMPLE_IDS)
    assert all("12-0" not in item for item in module.MEDIUM_SAMPLE_IDS)
