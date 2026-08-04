from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
import tarfile

from src.submission.batch import convert_docx_batch, render_prediction_batch
from src.submission.package import create_submission_package


def _prediction(sample_id: str) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "summary": {"bridge_name": sample_id, "report_date": "2026年8月"},
        "detailed_conclusion": ["总体状况良好"],
        "recommendations": [],
        "defects": [],
        "causes": [],
        "treatments": [],
        "safety_impact": [],
    }


class FakeDocSoffice:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str]) -> CompletedProcess[str]:
        self.calls.append(command)
        source = Path(command[-1])
        output_dir = Path(command[command.index("--outdir") + 1])
        (output_dir / f"{source.stem}.doc").write_bytes(b"legacy-doc")
        return CompletedProcess(command, 0, stdout="converted", stderr="")


def test_prediction_to_docx_to_doc_to_tar_has_three_root_entries(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"records": [{"sample_id": name, "filename": f"{name}.doc"} for name in ("A", "B", "C")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    predictions = tmp_path / "prediction.jsonl"
    predictions.write_text(
        "\n".join(json.dumps(_prediction(name), ensure_ascii=False) for name in ("A", "B", "C")) + "\n",
        encoding="utf-8",
    )

    rendered = tmp_path / "rendered-docx"
    render_result = render_prediction_batch(predictions, manifest, rendered)
    assert render_result["valid"] is True
    assert render_result["input_count"] == render_result["output_count"] == 3
    assert sorted(path.name for path in rendered.iterdir()) == ["A.docx", "B.docx", "C.docx"]

    final_doc = tmp_path / "final-doc"
    fake = FakeDocSoffice()
    convert_result = convert_docx_batch(
        rendered,
        final_doc,
        manifest,
        soffice_path="fake-soffice",
        runner=fake,
    )
    assert convert_result["valid"] is True
    assert convert_result["input_count"] == convert_result["output_count"] == 3
    assert sorted(path.name for path in final_doc.iterdir()) == ["A.doc", "B.doc", "C.doc"]
    assert all("doc:MS Word 97" in call for call in fake.calls)

    code_dir = tmp_path / "code"
    design_dir = tmp_path / "design"
    code_dir.mkdir()
    design_dir.mkdir()
    (code_dir / "run.py").write_text("pass\n", encoding="utf-8")
    (design_dir / "plan.md").write_text("# plan\n", encoding="utf-8")

    package = tmp_path / "submission.tar.gz"
    package_result = create_submission_package(
        final_doc,
        package,
        code_dir=code_dir,
        design_dir=design_dir,
        expected_names=("A.doc", "B.doc", "C.doc"),
    )
    assert package_result["valid"] is True
    with tarfile.open(package, "r:gz") as archive:
        assert archive.getnames() == [
            "code",
            "code/run.py",
            "design",
            "design/plan.md",
            "result",
            "result/A.doc",
            "result/B.doc",
            "result/C.doc",
        ]
        result_files = [
            name for name in archive.getnames() if name.startswith("result/") and name.endswith(".doc")
        ]
        assert len(result_files) == 3
