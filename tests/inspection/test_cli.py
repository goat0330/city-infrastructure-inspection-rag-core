from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tests.fixtures.word.ooxml_factory import paragraph, write_docx


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_python_m_inspection_is_runnable_from_repo_root() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "inspection", "--help"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "build-gold" in result.stdout
    assert "convert" in result.stdout


def test_parse_command_writes_document_model(tmp_path: Path) -> None:
    source = write_docx(tmp_path / "sample.docx", paragraph("桥梁名称"))
    output = tmp_path / "parsed.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "inspection",
            "parse",
            "--input",
            str(source),
            "--output",
            str(output),
            "--source-file",
            "sample.docx",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["source_file"] == "sample.docx"
    assert payload["blocks"][0]["raw_text"] == "桥梁名称"


def test_predict_requires_input_and_output_after_b2_integration() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "inspection", "predict"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--input" in result.stderr
    assert "--output" in result.stderr
