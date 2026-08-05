import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_round2_narrative_revalidation.py"
spec = importlib.util.spec_from_file_location("revalidate", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_preflight_reports_missing_files_without_model_calls(tmp_path, monkeypatch):
    for key in module.REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)
    result = module._preflight(
        [{"sample_id": "sample", "converted_docx_relative_path": "x.docx"}],
        tmp_path,
        tmp_path / "indexes",
    )
    assert result["status"] == "blocked"
    assert result["samples"][0]["docx_exists"] is False
    assert result["samples"][0]["index_exists"] is False
