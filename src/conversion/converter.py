"""Recoverable batch conversion from ``.doc`` to validated ``.docx`` files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


Record = dict[str, Any]
CommandRunner = Callable[[Sequence[str]], Any]


@dataclass(frozen=True)
class BatchResult:
    """The records written by one batch invocation."""

    records: tuple[Record, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "success": sum(record["status"] == "success" for record in self.records),
            "skipped": sum(record["status"] == "skipped" for record in self.records),
            "failed": sum(record["status"] == "failed" for record in self.records),
        }


def find_soffice(soffice_path: str | Path | None = None) -> str:
    """Resolve an explicitly supplied or PATH-provided LibreOffice executable."""

    def prefer_windows_executable(value: str) -> str:
        if os.name != "nt":
            return value
        candidate = Path(value)
        if candidate.suffix.casefold() in {".com", ".cmd"}:
            executable = candidate.with_suffix(".exe")
            if executable.is_file():
                return str(executable.resolve())
        return value

    if soffice_path is not None:
        value = os.fspath(soffice_path)
        candidate = Path(value)
        if candidate.is_file():
            return prefer_windows_executable(str(candidate.resolve()))
        located = shutil.which(value)
        if located:
            return prefer_windows_executable(located)
        raise FileNotFoundError(f"LibreOffice executable not found: {value}")

    names = ("soffice.exe", "soffice", "libreoffice") if os.name == "nt" else ("soffice", "libreoffice")
    for name in names:
        located = shutil.which(name)
        if located:
            return prefer_windows_executable(located)
    raise FileNotFoundError(
        "LibreOffice executable not found on PATH; pass --soffice-path to the CLI"
    )


def _word_com_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import pythoncom
        from win32com.client import DispatchEx
    except ImportError:
        return False

    try:
        pythoncom.CoInitialize()
    except Exception:
        return False
    application = None
    try:
        application = DispatchEx("Word.Application")
        application.Visible = False
        application.DisplayAlerts = 0
        return True
    except Exception:
        return False
    finally:
        if application is not None:
            try:
                application.Quit(0)
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _resolve_conversion_engine(engine: str, runner: CommandRunner | None) -> str:
    if engine not in {"auto", "word", "libreoffice"}:
        raise ValueError("engine must be one of: auto, word, libreoffice")
    if runner is not None:
        if engine == "word":
            raise ValueError("a custom runner can only be used with engine=libreoffice")
        return "libreoffice"
    if engine == "auto":
        return "word" if _word_com_available() else "libreoffice"
    if engine == "word" and not _word_com_available():
        raise RuntimeError("Microsoft Word COM is not available on this Windows installation")
    return engine


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_records(state_path: Path) -> dict[str, Record]:
    if not state_path.is_file():
        return {}

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    raw_records: Any
    if isinstance(payload, dict):
        raw_records = payload.get("records", [])
    else:
        raw_records = payload

    if isinstance(raw_records, dict):
        values = raw_records.values()
    elif isinstance(raw_records, list):
        values = raw_records
    else:
        return {}

    records: dict[str, Record] = {}
    for record in values:
        if isinstance(record, dict) and record.get("source"):
            records[_path_key(record["source"])] = record
    return records


def _write_state(state_path: Path, records: dict[str, Record]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    ordered = [records[key] for key in sorted(records)]
    payload = {"version": 2, "records": ordered}
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{state_path.name}.",
            suffix=".tmp",
            dir=state_path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, state_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _record(
    source: Path,
    target: Path,
    status: str,
    duration: float,
    source_size: int | None,
    source_sha256: str | None,
    source_mtime_ns: int | None,
    target_size: int | None,
    target_mtime_ns: int | None,
    error: str | None,
) -> Record:
    return {
        "source": str(source),
        "target": str(target),
        "status": status,
        "duration": duration,
        "duration_ms": round(duration * 1000, 3),
        "source_size": source_size,
        "source_sha256": source_sha256,
        "source_mtime_ns": source_mtime_ns,
        "target_size": target_size,
        "target_mtime_ns": target_mtime_ns,
        "target_is_usable": status in {"success", "skipped"},
        "error": error,
    }


def _validate_docx(path: Path) -> None:
    from docx import Document

    Document(str(path))


def _is_valid_docx(path: Path) -> bool:
    try:
        _validate_docx(path)
    except Exception:
        return False
    return True


def _can_skip(previous: Record, source: Path, target: Path) -> bool:
    if previous.get("status") not in {"success", "skipped"}:
        return False
    try:
        source_stat = source.stat()
        target_stat = target.stat()
    except OSError:
        return False

    if previous.get("source_size") != source_stat.st_size:
        return False
    previous_hash = previous.get("source_sha256")
    if not isinstance(previous_hash, str) or previous_hash != _sha256(source):
        return False
    previous_target_size = previous.get("target_size")
    if isinstance(previous_target_size, int) and previous_target_size != target_stat.st_size:
        return False
    return _is_valid_docx(target)


def _command_error(result: Any) -> str:
    returncode = getattr(result, "returncode", 0)
    output = "\n".join(
        value.strip()
        for value in (getattr(result, "stdout", ""), getattr(result, "stderr", ""))
        if value and value.strip()
    )
    message = f"LibreOffice exited with code {returncode}"
    return f"{message}: {output}" if output else message


def _find_converted_file(temp_output: Path, source: Path) -> Path:
    expected = temp_output / f"{source.stem}.docx"
    if expected.is_file():
        return expected
    candidates = sorted(path for path in temp_output.glob("*.docx") if path.is_file())
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError("LibreOffice completed without producing a .docx file")
    raise RuntimeError("LibreOffice produced multiple ambiguous .docx files")


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _default_runner(
    command: Sequence[str], timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        raise TimeoutError(
            f"LibreOffice conversion timed out after {timeout_seconds:g}s"
        ) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _convert_one(
    source: Path,
    target: Path,
    soffice: str,
    runner: CommandRunner,
    *,
    timeout_seconds: float,
    wait_for_stable_output: bool,
) -> Record:
    started = time.perf_counter()
    source_size: int | None = None
    source_hash: str | None = None
    source_mtime_ns: int | None = None
    try:
        source_stat = source.stat()
        source_size = source_stat.st_size
        source_mtime_ns = source_stat.st_mtime_ns
        source_hash = _sha256(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="conversion-", dir=str(target.parent)
        ) as temporary:
            temporary_root = Path(temporary)
            temp_output = temporary_root / "output"
            profile = temporary_root / "profile"
            temp_output.mkdir()
            profile.mkdir()
            command = [
                soffice,
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--headless",
                "--convert-to",
                "docx:Office Open XML Text",
                "--outdir",
                str(temp_output),
                str(source),
            ]
            result = runner(command)
            if getattr(result, "returncode", 0) != 0:
                raise RuntimeError(_command_error(result))

            converted = _wait_for_converted_file(
                temp_output,
                source,
                ".docx",
                timeout_seconds,
                stable_polls=2 if wait_for_stable_output else 0,
            )
            _validate_docx(converted)
            os.replace(converted, target)
            target_stat = target.stat()
        duration = time.perf_counter() - started
        return _record(
            source,
            target,
            "success",
            duration,
            source_size,
            source_hash,
            source_mtime_ns,
            target_stat.st_size,
            target_stat.st_mtime_ns,
            None,
        )
    except Exception as exc:
        duration = time.perf_counter() - started
        return _record(
            source,
            target,
            "failed",
            duration,
            source_size,
            source_hash,
            source_mtime_ns,
            None,
            None,
            f"{type(exc).__name__}: {exc}",
        )


def _discover_sources(input_dir: Path) -> list[Path]:
    return sorted(
        (path for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".doc"),
        key=lambda path: str(path.relative_to(input_dir)).lower(),
    )


def convert_directory(
    input_dir: str | Path,
    output_dir: str | Path,
    state_path: str | Path,
    soffice_path: str | Path | None = None,
    *,
    timeout_seconds: float = 300.0,
    runner: CommandRunner | None = None,
) -> BatchResult:
    """Convert all legacy Word files, persisting state after every file."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    input_root = Path(input_dir).resolve()
    if not input_root.exists():
        raise FileNotFoundError(f"Input directory not found: {input_root}")
    if not input_root.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_root}")

    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    state_file = Path(state_path).resolve()
    previous = _load_records(state_file)
    sources = _discover_sources(input_root)

    if runner is None:
        executable = find_soffice(soffice_path) if sources else "soffice"
        command_runner: CommandRunner = lambda command: _default_runner(command, timeout_seconds)
    else:
        executable = str(soffice_path) if soffice_path is not None else "soffice"
        command_runner = runner

    records: dict[str, Record] = {}
    for source in sources:
        source = source.resolve()
        relative = source.relative_to(input_root)
        target = (output_root / relative).with_suffix(".docx").resolve()
        key = _path_key(source)
        previous_record = previous.get(key)
        if previous_record is not None and _can_skip(previous_record, source, target):
            started = time.perf_counter()
            source_stat = source.stat()
            target_stat = target.stat()
            records[key] = _record(
                source,
                target,
                "skipped",
                time.perf_counter() - started,
                source_stat.st_size,
                _sha256(source),
                source_stat.st_mtime_ns,
                target_stat.st_size,
                target_stat.st_mtime_ns,
                None,
            )
        else:
            records[key] = _convert_one(
                source,
                target,
                executable,
                command_runner,
                timeout_seconds=timeout_seconds,
                wait_for_stable_output=runner is None,
            )
        _write_state(state_file, records)

    if not sources:
        _write_state(state_file, records)
    ordered = tuple(records[key] for key in sorted(records))
    return BatchResult(ordered)


def _find_converted_file_with_suffix(
    temp_output: Path, source: Path, suffix: str
) -> Path:
    expected = temp_output / f"{source.stem}{suffix}"
    if expected.is_file():
        return expected
    candidates = sorted(
        path for path in temp_output.iterdir()
        if path.is_file() and path.suffix.casefold() == suffix.casefold()
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError(
            f"LibreOffice completed without producing a {suffix} file"
        )
    raise RuntimeError("LibreOffice produced multiple ambiguous output files")


def _wait_for_converted_file(
    temp_output: Path,
    source: Path,
    suffix: str,
    timeout_seconds: float,
    *,
    stable_polls: int = 0,
) -> Path:
    """Wait for a launcher-backed converter to finish writing its output."""

    deadline = time.monotonic() + timeout_seconds
    previous_path: Path | None = None
    previous_size: int | None = None
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            candidate = (
                _find_converted_file(temp_output, source)
                if suffix.casefold() == ".docx"
                else _find_converted_file_with_suffix(temp_output, source, suffix)
            )
            size = candidate.stat().st_size
            if size > 0:
                if candidate == previous_path and size == previous_size:
                    stable_count += 1
                else:
                    stable_count = 0
                previous_path = candidate
                previous_size = size
                if stable_count >= stable_polls:
                    return candidate
        except (FileNotFoundError, RuntimeError):
            pass
        time.sleep(0.25)

    raise RuntimeError(
        f"LibreOffice did not produce a stable {suffix} file within "
        f"{timeout_seconds:g}s"
    )


def _wait_for_nonempty_file(path: Path, timeout_seconds: float) -> Path:
    deadline = time.monotonic() + timeout_seconds
    previous_size: int | None = None
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size > 0:
            if size == previous_size:
                stable_count += 1
            else:
                stable_count = 0
            previous_size = size
            if stable_count >= 2:
                return path
        time.sleep(0.25)
    raise RuntimeError(f"converter did not produce a stable file within {timeout_seconds:g}s: {path}")


def _convert_docx_one(
    source: Path,
    target: Path,
    soffice: str,
    runner: CommandRunner,
    *,
    timeout_seconds: float,
    wait_for_stable_output: bool,
) -> Record:
    started = time.perf_counter()
    source_size: int | None = None
    source_hash: str | None = None
    source_mtime_ns: int | None = None
    try:
        source_stat = source.stat()
        source_size = source_stat.st_size
        source_mtime_ns = source_stat.st_mtime_ns
        source_hash = _sha256(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="docx-to-doc-", dir=str(target.parent)
        ) as temporary:
            temporary_root = Path(temporary)
            temp_output = temporary_root / "output"
            profile = temporary_root / "profile"
            temp_output.mkdir()
            profile.mkdir()
            command = [
                soffice,
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--headless",
                "--convert-to",
                "doc:MS Word 97",
                "--outdir",
                str(temp_output),
                str(source),
            ]
            result = runner(command)
            if getattr(result, "returncode", 0) != 0:
                raise RuntimeError(_command_error(result))
            converted = _wait_for_converted_file(
                temp_output,
                source,
                ".doc",
                timeout_seconds,
                stable_polls=2 if wait_for_stable_output else 0,
            )
            if converted.stat().st_size <= 0:
                raise RuntimeError("LibreOffice produced an empty .doc file")
            os.replace(converted, target)
            target_stat = target.stat()
        duration = time.perf_counter() - started
        return _record(
            source,
            target,
            "success",
            duration,
            source_size,
            source_hash,
            source_mtime_ns,
            target_stat.st_size,
            target_stat.st_mtime_ns,
            None,
        )
    except Exception as exc:
        duration = time.perf_counter() - started
        return _record(
            source,
            target,
            "failed",
            duration,
            source_size,
            source_hash,
            source_mtime_ns,
            None,
            None,
            f"{type(exc).__name__}: {exc}",
        )


def _clean_final_doc_directory(output_root: Path) -> None:
    """Remove only known temporary/intermediate files from a final-doc root."""

    for path in output_root.iterdir():
        if path.is_dir() and path.name.lstrip(".").startswith("docx-to-doc-"):
            shutil.rmtree(path)
            continue
        if not path.is_file():
            continue
        if (
            path.suffix.casefold() in {".doc", ".docx", ".tmp", ".lock"}
            or path.name.startswith(("~$", ".~lock.", ".~"))
        ):
            path.unlink()


def _convert_docx_with_word(
    sources: Sequence[Path],
    output_root: Path,
    timeout_seconds: float,
) -> tuple[Record, ...]:
    """Convert final DOCX files through the installed Word COM server."""

    import pythoncom
    from win32com.client import DispatchEx

    metadata: dict[Path, tuple[int, str, int]] = {}
    for source in sources:
        stat = source.stat()
        metadata[source] = (stat.st_size, _sha256(source), stat.st_mtime_ns)

    pythoncom.CoInitialize()
    application = None
    document = None
    records: list[Record] = []
    try:
        application = DispatchEx("Word.Application")
        application.Visible = False
        application.DisplayAlerts = 0
        for source in sources:
            started = time.perf_counter()
            target = output_root / f"{source.stem}.doc"
            source_size, source_hash, source_mtime_ns = metadata[source]
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Positional arguments avoid a Word COM named-argument hang
                # observed on large reports with the installed Office build.
                document = application.Documents.Open(str(source), False, True, False)
                document.SaveAs2(str(target), 0)
                document.Close(0)
                document = None
                target_stat = _wait_for_nonempty_file(target, timeout_seconds).stat()
                records.append(
                    _record(
                        source,
                        target,
                        "success",
                        time.perf_counter() - started,
                        source_size,
                        source_hash,
                        source_mtime_ns,
                        target_stat.st_size,
                        target_stat.st_mtime_ns,
                        None,
                    )
                )
            except Exception as exc:
                if document is not None:
                    try:
                        document.Close(0)
                    except Exception:
                        pass
                    document = None
                records.append(
                    _record(
                        source,
                        target,
                        "failed",
                        time.perf_counter() - started,
                        source_size,
                        source_hash,
                        source_mtime_ns,
                        None,
                        None,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
        return tuple(records)
    finally:
        if document is not None:
            try:
                document.Close(0)
            except Exception:
                pass
        if application is not None:
            try:
                application.Quit(0)
            except Exception:
                pass
        pythoncom.CoUninitialize()


def convert_docx_directory(
    input_dir: str | Path,
    output_dir: str | Path,
    soffice_path: str | Path | None = None,
    *,
    timeout_seconds: float = 300.0,
    runner: CommandRunner | None = None,
    engine: str = "auto",
) -> BatchResult:
    """Convert rendered DOCX files to final root-level ``.doc`` files."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    input_root = Path(input_dir).resolve()
    if not input_root.exists():
        raise FileNotFoundError(f"Input directory not found: {input_root}")
    if not input_root.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_root}")

    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    _clean_final_doc_directory(output_root)
    sources = sorted(
        (path for path in input_root.iterdir() if path.is_file() and path.suffix.casefold() == ".docx"),
        key=lambda path: path.name.casefold(),
    )
    target_names = [f"{source.stem}.doc" for source in sources]
    if len(target_names) != len({name.casefold() for name in target_names}):
        raise ValueError("DOCX input names collide after conversion to .doc")

    resolved_engine = _resolve_conversion_engine(engine, runner)
    if resolved_engine == "word" and sources:
        records = _convert_docx_with_word(sources, output_root, timeout_seconds)
    else:
        executable = find_soffice(soffice_path) if sources and runner is None else (
            str(soffice_path) if soffice_path is not None else "soffice"
        )
        command_runner: CommandRunner = (
            runner
            if runner is not None
            else lambda command: _default_runner(command, timeout_seconds)
        )
        records = tuple(
            _convert_docx_one(
                source,
                output_root / f"{source.stem}.doc",
                executable,
                command_runner,
                timeout_seconds=timeout_seconds,
                wait_for_stable_output=runner is None,
            )
            for source in sources
        )
    return BatchResult(records)
