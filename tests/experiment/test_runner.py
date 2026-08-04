from __future__ import annotations

import json
from pathlib import Path

from scripts import run_narrative_enhancement as runner
from tests.fixtures.word.ooxml_factory import paragraph, write_docx


def test_offline_runner_writes_a_b_c_d_artifacts(tmp_path: Path) -> None:
    source = write_docx(tmp_path / "实验桥.docx", paragraph("检测结论：桥面存在裂缝。"))
    output = tmp_path / "run"
    summary = runner.run_experiment(source, output, sample_id="sample-1", offline=True)
    assert summary["status"] == "offline"
    assert summary["offline"] is True
    for name in ("baseline_prediction.json", "enhanced_prediction.json", "retrieval_trace.json", "ab_results.json", "experiment_summary.json"):
        assert (output / name).is_file()
    ab = json.loads((output / "ab_results.json").read_text(encoding="utf-8"))
    assert set(ab["groups"]) == {"A", "B", "C", "D"}
    assert "evidence_id_validity" in ab["groups"]["D"]


def test_missing_real_configuration_does_not_write_enhanced(tmp_path: Path, monkeypatch) -> None:
    source = write_docx(tmp_path / "实验桥.docx", paragraph("检测结论：桥面存在裂缝。"))
    for name in tuple(name for name in __import__("os").environ if name.startswith("IAIC_")):
        monkeypatch.delenv(name, raising=False)
    output = tmp_path / "missing"
    summary = runner.run_experiment(source, output)
    assert summary["status"] == "configuration_error"
    assert not (output / "enhanced_prediction.json").exists()
