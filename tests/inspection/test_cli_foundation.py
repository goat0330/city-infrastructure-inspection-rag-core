from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tarfile

from tests.fixtures.word.ooxml_factory import paragraph, write_docx


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_help_lists_foundation_commands() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "inspection", "--help"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for command in ("route", "render", "validate", "package", "validate-package"):
        assert command in result.stdout


def test_route_command_writes_json(tmp_path: Path) -> None:
    source = write_docx(tmp_path / "sample.docx", paragraph("5.2 技术状况评分"))
    output = tmp_path / "routes.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "inspection",
            "route",
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
    assert payload["route_count"] == 1
    assert payload["routes"][0]["category"] == "scoring"


def test_render_and_validate_commands(tmp_path: Path) -> None:
    prediction = {
        "sample_id": "sample-1",
        "schema_version": "prediction-v1",
        "summary": {
            "bridge_name": "测试桥",
            "report_date": "2026年8月",
            "overall_score": "88.0",
            "overall_grade": "B级",
            "superstructure_score": "87.0",
            "superstructure_grade": "B级",
            "substructure_score": "90.0",
            "substructure_grade": "A级",
            "deck_score": "86.0",
            "deck_grade": "B级",
            "previous_overall_score": "无",
            "previous_overall_grade": "无",
            "trend": "无",
            "overall_conclusion": "总体状况良好",
            "risk_points": "局部裂缝",
            "recommendations_summary": "1条尽快维修建议",
        },
        "detailed_conclusion": ["结论"],
        "recommendations": [
            {"index": "1", "category": "尽快维修", "content": "修复", "location": "桥面"}
        ],
        "defects": [
            {
                "index": "1",
                "location": "桥面",
                "defect_type": "裂缝",
                "description": "局部裂缝",
                "is_new": "否",
                "previous_status": "无",
                "development": "无",
            }
        ],
        "causes": ["原因"],
        "treatments": ["处置"],
        "safety_impact": ["影响"],
    }
    source = tmp_path / "prediction.json"
    source.write_text(json.dumps(prediction, ensure_ascii=False), encoding="utf-8")
    document = tmp_path / "result.docx"
    subprocess.run(
        [sys.executable, "-m", "inspection", "render", "--input", str(source), "--output", str(document)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    validation = tmp_path / "validation.json"
    subprocess.run(
        [sys.executable, "-m", "inspection", "validate", "--input", str(document), "--output", str(validation)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert document.is_file()
    assert json.loads(validation.read_text(encoding="utf-8"))["valid"] is True


def test_package_command_creates_tar_gz(tmp_path: Path) -> None:
    input_dir = tmp_path / "final-doc"
    input_dir.mkdir()
    (input_dir / "测试桥.doc").write_bytes(b"legacy-doc")
    output = tmp_path / "submission.tar.gz"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "inspection",
            "package",
            "--input-dir",
            str(input_dir),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["valid"] is True
    with tarfile.open(output, "r:gz") as archive:
        assert archive.getnames() == ["测试桥.doc"]


def test_validate_package_command_checks_existing_archive(tmp_path: Path) -> None:
    input_dir = tmp_path / "final-doc-validate"
    input_dir.mkdir()
    (input_dir / "A.doc").write_bytes(b"legacy-doc")
    package = tmp_path / "submission-validate.tar.gz"
    subprocess.run(
        [sys.executable, "-m", "inspection", "package", "--input-dir", str(input_dir), "--output", str(package)],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    output = tmp_path / "package-validation.json"
    subprocess.run(
        [sys.executable, "-m", "inspection", "validate-package", "--input", str(package), "--output", str(output)],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    assert json.loads(output.read_text(encoding="utf-8"))["valid"] is True
