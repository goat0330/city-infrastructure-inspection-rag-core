"""Create and validate deterministic official ``tar.gz`` submissions.

The competition requires three top-level directories: ``code/``,
``design/`` and ``result/``.  Result documents must be legacy Word ``.doc``
files directly under ``result/``; code and design files may be nested below
their respective directories.
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


_REQUIRED_TOP_LEVELS = ("code", "design", "result")
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


def _normalise_member_name(value: object) -> str:
    return _normalise_name(value).replace("\\", "/")


def _is_temporary_name(name: str) -> bool:
    lowered = name.casefold()
    return any(lowered.startswith(prefix) for prefix in _TEMP_PREFIXES)


def _safe_root_name(name: str) -> bool:
    path = PurePosixPath(_normalise_member_name(name))
    return (
        bool(name)
        and not path.is_absolute()
        and len(path.parts) == 1
        and path.parts[0] not in {".", ".."}
        and not _is_temporary_name(path.name)
        and not path.name.startswith(".")
    )


def _safe_tree_name(name: str) -> bool:
    path = PurePosixPath(_normalise_member_name(name).rstrip("/"))
    return bool(name) and not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


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
    """Load exact expected result names from JSON, CSV or line-based text."""

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
        failures.append({"code": "empty_package", "message": "The result directory contains no result files."})
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


def _merge_failures(result: dict[str, Any], failures: Iterable[dict[str, Any]]) -> None:
    extra = list(failures)
    if not extra:
        return
    result["failures"].extend(extra)
    result["valid"] = False
    result["status"] = "failed"


def validate_submission_package(
    package_path: str | Path,
    *,
    expected_names: Sequence[str] | None = None,
    extension: str = ".doc",
) -> dict[str, Any]:
    """Validate official archive layout, result names and optional manifest."""

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

    member_names = [_normalise_member_name(member.name) for member in members]
    unsafe_members = sorted(
        name for name in member_names if not _safe_tree_name(name.rstrip("/"))
    )
    directories = {
        name.rstrip("/")
        for name, member in zip(member_names, members)
        if member.isdir()
    }
    regular_files = [
        name.rstrip("/")
        for name, member in zip(member_names, members)
        if member.isfile()
    ]
    non_regular = [
        name for name, member in zip(member_names, members) if not member.isfile() and not member.isdir()
    ]

    result_files: list[str] = []
    result_nested: list[str] = []
    code_files: list[str] = []
    design_files: list[str] = []
    root_files: list[str] = []
    unknown_members: list[str] = []
    top_level_dirs = set()

    for name in (*directories, *regular_files):
        top_level_dirs.add(name.split("/", 1)[0])

    for name in regular_files:
        if name.startswith("result/"):
            relative = name[len("result/") :]
            if "/" in relative:
                result_nested.append(name)
            else:
                result_files.append(relative)
        elif name.startswith("code/"):
            code_files.append(name)
        elif name.startswith("design/"):
            design_files.append(name)
        else:
            root_files.append(name)

    for name in directories:
        if name and name.split("/", 1)[0] not in _REQUIRED_TOP_LEVELS:
            unknown_members.append(name)

    result = _validate_names(result_files, extension=extension, expected_names=expected_names)
    result.update(
        {
            "file_name": path.name,
            "archive_member_count": len(members),
            "required_directories": list(_REQUIRED_TOP_LEVELS),
            "top_level_directories": sorted(top_level_dirs),
            "code_file_count": len(code_files),
            "design_file_count": len(design_files),
            "result_file_count": len(result_files),
        }
    )

    layout_failures: list[dict[str, Any]] = []
    for directory in _REQUIRED_TOP_LEVELS:
        present = directory in directories or any(
            name.startswith(f"{directory}/") for name in (*regular_files, *directories)
        )
        if not present:
            layout_failures.append({"code": "missing_required_directory", "name": f"{directory}/"})
    if not code_files:
        layout_failures.append({"code": "empty_code_directory", "name": "code/"})
    if not design_files:
        layout_failures.append({"code": "empty_design_directory", "name": "design/"})
    if root_files:
        layout_failures.append({"code": "root_files_forbidden", "names": sorted(root_files)})
    if result_nested:
        layout_failures.append({"code": "nested_result_files", "names": sorted(result_nested)})
    if unknown_members:
        layout_failures.append({"code": "unexpected_top_level_members", "names": sorted(set(unknown_members))})
    if unsafe_members:
        layout_failures.append({"code": "unsafe_archive_members", "names": unsafe_members})
    if non_regular:
        layout_failures.append({"code": "non_regular_members", "names": sorted(non_regular)})
    _merge_failures(result, layout_failures)
    return result


def _collect_tree(root: Path, prefix: str) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = [(f"{prefix}/", root)]
    children = sorted(root.rglob("*"), key=lambda path: _normalise_member_name(path.relative_to(root).as_posix()))
    for source in children:
        if source.is_symlink():
            raise ValueError(f"symlinks are not allowed in {prefix}: {source}")
        relative = source.relative_to(root).as_posix()
        member_name = f"{prefix}/{relative}"
        if source.is_dir():
            entries.append((f"{member_name}/", source))
        elif source.is_file():
            entries.append((member_name, source))
        else:
            raise ValueError(f"unsupported filesystem entry: {source}")
    return entries


def _tar_bytes(entries: Iterable[tuple[str, Path]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for member_name, source in entries:
            member_name = _normalise_member_name(member_name)
            if source.is_dir():
                info = tarfile.TarInfo(name=member_name.rstrip("/") + "/")
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.size = 0
                info.mtime = 0
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                archive.addfile(info)
                continue
            data = source.read_bytes()
            info = tarfile.TarInfo(name=member_name)
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
    code_dir: str | Path,
    design_dir: str | Path,
    expected_names: Sequence[str] | None = None,
    extension: str = ".doc",
) -> dict[str, Any]:
    """Create a deterministic official ``code/design/result`` tar.gz."""

    result_root = Path(input_dir)
    code_root = Path(code_dir)
    design_root = Path(design_dir)
    if not result_root.is_dir():
        raise NotADirectoryError(f"result directory does not exist: {result_root}")
    if not code_root.is_dir():
        raise NotADirectoryError(f"code directory does not exist: {code_root}")
    if not design_root.is_dir():
        raise NotADirectoryError(f"design directory does not exist: {design_root}")
    extension = extension if extension.startswith(".") else f".{extension}"

    entries = sorted(result_root.iterdir(), key=lambda path: _normalise_name(path.name))
    nested = [entry.name for entry in entries if entry.is_dir()]
    files = [entry for entry in entries if entry.is_file()]
    preliminary = _validate_names(
        [entry.name for entry in files], extension=extension, expected_names=expected_names
    )
    if nested:
        preliminary["failures"].append({"code": "nested_result_directories", "names": nested})
        preliminary["valid"] = False
        preliminary["status"] = "failed"
    code_entries = _collect_tree(code_root, "code")
    design_entries = _collect_tree(design_root, "design")
    if len(code_entries) == 1:
        preliminary["failures"].append({"code": "empty_code_directory", "name": "code/"})
    if len(design_entries) == 1:
        preliminary["failures"].append({"code": "empty_design_directory", "name": "design/"})
    if not preliminary["valid"]:
        raise ValueError(json.dumps(preliminary, ensure_ascii=False, sort_keys=True))

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive_entries = [*code_entries, *design_entries, ("result/", result_root)]
    archive_entries.extend((f"result/{_normalise_name(source.name)}", source) for source in files)
    tar_payload = _tar_bytes(archive_entries)
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
