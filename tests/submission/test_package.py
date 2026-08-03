from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile

import pytest

from src.submission.package import (
    create_submission_package,
    load_expected_names,
    validate_submission_package,
)


def _write_doc(path: Path, payload: bytes = b"legacy-word-placeholder") -> Path:
    path.write_bytes(payload)
    return path


def test_creates_deterministic_root_only_tar_gz(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    source.mkdir()
    _write_doc(source / "B.doc", b"b")
    _write_doc(source / "A.doc", b"a")

    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    result = create_submission_package(source, first, expected_names=("A.doc", "B.doc"))
    create_submission_package(source, second, expected_names=("A.doc", "B.doc"))

    assert result["valid"] is True
    assert result["files"] == ["A.doc", "B.doc"]
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    with tarfile.open(first, "r:gz") as archive:
        assert archive.getnames() == ["A.doc", "B.doc"]
        assert all(member.isfile() for member in archive.getmembers())


def test_rejects_nested_or_wrong_extension_inputs(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "result.docx").write_bytes(b"x")

    with pytest.raises(ValueError) as error:
        create_submission_package(source, tmp_path / "bad.tar.gz")

    payload = json.loads(str(error.value))
    codes = {failure["code"] for failure in payload["failures"]}
    assert "nested_directories" in codes
    assert "invalid_extension" in codes


def test_manifest_reports_missing_and_unexpected_files(tmp_path: Path) -> None:
    source = tmp_path / "docs"
    source.mkdir()
    _write_doc(source / "A.doc")

    with pytest.raises(ValueError) as error:
        create_submission_package(
            source,
            tmp_path / "submission.tar.gz",
            expected_names=("A.doc", "B.doc"),
        )

    payload = json.loads(str(error.value))
    assert payload["missing"] == ["B.doc"]
    assert payload["unexpected"] == []


def test_validates_invalid_archive_without_exception(tmp_path: Path) -> None:
    bad = tmp_path / "submission.tar.gz"
    bad.write_bytes(b"not a tar archive")

    result = validate_submission_package(bad)

    assert result["valid"] is False
    assert result["failures"][0]["code"] == "invalid_tar_gz"


def test_loads_csv_and_json_manifests(tmp_path: Path) -> None:
    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text("filename\nA.doc\nB.doc\n", encoding="utf-8")
    json_path = tmp_path / "manifest.json"
    json_path.write_text(json.dumps({"files": ["A.doc", {"filename": "B.doc"}]}), encoding="utf-8")

    assert load_expected_names(csv_path) == ("A.doc", "B.doc")
    assert load_expected_names(json_path) == ("A.doc", "B.doc")
