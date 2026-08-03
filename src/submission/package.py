"""Create and validate deterministic ``tar.gz`` submission packages.

The competition's current delivery contract is a gzip-compressed tar archive
whose root contains one legacy Word ``.doc`` result per test sample.  This
module deliberately validates names and archive layout without trying to parse
binary ``.doc`` contents.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import csv
import gzip
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import unicodedata
from typing import Any


_TEMP_PREFIXES = ("~$", ".~lock.", ".~")
_MANIFEST_COLUMNS = (
    "filename",
    "file_name",
    "name",
    "output_filename",
    "result_filename",
    "报告文件名",
    "文件名",
)


def _normalise_name(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or "").strip())


def _is_temporary_name(name: str) -> bool:
    lowered = name.casefold()
    return any(lowered.startswith(prefix) for prefix in _TEMP_PREFIXES)


def _safe_root_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return (
        bool(name)
        and not path.is_absolute()
        and len(path.parts) == 1
        and path.parts[0] not in {".", ".."}
        and not _is_temporary_name(path.name)
        and not path.name.startswith(".")
    )


def _expected_from_json(payload: Any) -> list[str]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = None
        for key in ("files", "filenames", "expected_files", "samples"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                values = candidate
                break
        if values is None:
            raise ValueError("JSON manifest must contain a file-name list")
    else:
        raise ValueError("JSON manifest must be a list or object")

    result: list[str] = []
    for item in values:
        if isinstance(item, dict):
            value = next((item.get(key) for key in _MANIFEST_COLUMNS if item.get(key)), None)
        else:
            value = item
        name = _normalise_name(value)
        if name:
            result.append(name)
    return result


def _expected_from_csv(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
        if not rows:
            return []
        fieldnames = [field for field in (rows[0].keys() if rows else ()) if field]
        selected = next((field for field in _MANIFEST_COLUMNS if field in fieldnames), None)
        if selected is None:
            selected = fieldnames[0] if fieldnames else None
        if selected is None:
            return []
        return [_normalise_name(row.get(selected)) for row in rows if _normalise_name(row.get(selected))]


def load_expected_names(path: str | Path | None) -> tuple[str, ...] | None:
    """Load exact expected output names from JSON, CSV or line-based text."""

    if path is None:
        return None
    source = Path(path)
    suffix = source.suffix.casefold()
    if suffix == ".json":
        names = _expected_from_json(json.loads(source.read_text(encoding="utf-8-sig")))
    elif suffix == ".csv":
        names = _expected_from_csv(source)
    else:
        names = [
            _normalise_name(line)
            for line in source.read_text(encoding="utf-8-sig").splitlines()
            if _normalise_name(line) and not line.lstrip().startswith("#")
        ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"manifest contains duplicate names: {duplicates}")
    return tuple(names)


def _validate_names(
    names: Sequence[str],
    *,
    extension: str,
    expected_names: Sequence[str] | None,
) -> dict[str, Any]:
    normalised = [_normalise_name(name) for name in names]
    duplicate_names = sorted({name for name in normalised if normalised.count(name) > 1})
    unsafe_names = sorted(name for name in normalised if not _safe_root_name(name))
    wrong_extensions = sorted(
        name for name in normalised if PurePosixPath(name).suffix.casefold() != extension.casefold()
    )

    expected = [_normalise_name(name) for name in expected_names] if expected_names is not None else None
    missing = sorted(set(expected or ()) - set(normalised))
    unexpected = sorted(set(normalised) - set(expected or ())) if expected is not None else []

    failures: list[dict[str, Any]] = []
    if not normalised:
        failures.append({"code": "empty_package", "message": "The package contains no result files."})
    if duplicate_names:
        failures.append({"code": "duplicate_names", "names": duplicate_names})
    if unsafe_names:
        failures.append({"code": "unsafe_or_nested_names", "names": unsafe_names})
    if wrong_extensions:
        failures.append(
            {"code": "invalid_extension", "required": extension, "names": wrong_extensions}
        )
    if missing:
        failures.append({"code": "missing_expected_files", "names": missing})
    if unexpected:
        failures.append({"code": "unexpected_files", "names": unexpected})

    return {
        "valid": not failures,
        "status": "passed" if not failures else "failed",
        "file_count": len(normalised),
        "files": sorted(normalised),
        "required_extension": extension,
        "expected_file_count": len(expected) if expected is not None else None,
        "missing": missing,
        "unexpected": unexpected,
        "failures": failures,
    }


def validate_submission_package(
    package_path: str | Path,
    *,
    expected_names: Sequence[str] | None = None,
    extension: str = ".doc",
) -> dict[str, Any]:
    """Validate archive type, root layout, names and optional exact manifest."""

    path = Path(package_path)
    if not path.is_file():
        return {
            "valid": False,
            "status": "failed",
            "file_name": path.name,
            "file_count": 0,
            "files": [],
            "failures": [{"code": "package_not_found", "message": "Package file does not exist."}],
        }

    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
    except (tarfile.TarError, OSError, EOFError):
        return {
            "valid": False,
            "status": "failed",
            "file_name": path.name,
            "file_count": 0,
            "files": [],
            "failures": [{"code": "invalid_tar_gz", "message": "File is not a readable tar.gz archive."}],
        }

    non_regular = [member.name for member in members if not member.isfile()]
    names = [member.name for member in members if member.isfile()]
    result = _validate_names(names, extension=extension, expected_names=expected_names)
    result["file_name"] = path.name
    result["archive_member_count"] = len(members)
    if non_regular:
        result["failures"].append(
            {"code": "non_regular_members", "names": sorted(_normalise_name(name) for name in non_regular)}
        )
        result["valid"] = False
        result["status"] = "failed"
    return result


def _tar_bytes(files: Iterable[Path]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for source in files:
            data = source.read_bytes()
            info = tarfile.TarInfo(name=_normalise_name(source.name))
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def create_submission_package(
    input_dir: str | Path,
    output_path: str | Path,
    *,
    expected_names: Sequence[str] | None = None,
    extension: str = ".doc",
) -> dict[str, Any]:
    """Create a deterministic root-only tar.gz and validate the result."""

    source_dir = Path(input_dir)
    if not source_dir.is_dir():
        raise NotADirectoryError(f"input directory does not exist: {source_dir}")
    extension = extension if extension.startswith(".") else f".{extension}"

    entries = sorted(source_dir.iterdir(), key=lambda path: _normalise_name(path.name))
    nested = [entry.name for entry in entries if entry.is_dir()]
    files = [entry for entry in entries if entry.is_file()]
    preliminary = _validate_names(
        [entry.name for entry in files], extension=extension, expected_names=expected_names
    )
    if nested:
        preliminary["failures"].append({"code": "nested_directories", "names": nested})
        preliminary["valid"] = False
        preliminary["status"] = "failed"
    if not preliminary["valid"]:
        raise ValueError(json.dumps(preliminary, ensure_ascii=False, sort_keys=True))

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tar_payload = _tar_bytes(files)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(tar_payload)

    result = validate_submission_package(
        destination, expected_names=expected_names, extension=extension
    )
    result["output"] = str(destination)
    if not result["valid"]:
        destination.unlink(missing_ok=True)
        raise RuntimeError(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result
