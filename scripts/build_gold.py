"""Build deterministic Gold JSON from readable label DOCX files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audit import audit_dataset, document_files  # noqa: E402
from src.gold import LabelParseError, parse_label_docx  # noqa: E402


def _pairing_by_label(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        str(entry["label_relative_path"]): entry
        for entry in report["pairing"]["entries"]  # type: ignore[index]
    }


def build_gold(labels_dir: Path | str, reports_dir: Path | str, output_dir: Path | str) -> dict[str, object]:
    labels_root = Path(labels_dir)
    report = audit_dataset(labels_root, reports_dir)
    pairing = _pairing_by_label(report)
    records: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for label_path in document_files(labels_root):
        label_relative = label_path.relative_to(labels_root).as_posix()
        pair = pairing[label_relative]
        source_report = None
        if pair["status"] in {"paired_exact", "paired_fuzzy"}:
            source_report = pair["report_relative_paths"][0]  # type: ignore[index]
        try:
            record = parse_label_docx(
                label_path,
                labels_root,
                source_report_relative_path=source_report,
            )
        except LabelParseError as exc:
            failures.append(
                {
                    "label_relative_path": label_relative,
                    "format": label_path.suffix.casefold(),
                    "status": "failed",
                    "error_code": exc.code,
                    "error": str(exc),
                }
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            failures.append(
                {
                    "label_relative_path": label_relative,
                    "format": label_path.suffix.casefold(),
                    "status": "failed",
                    "error_code": "unexpected_parse_error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            records.append(record)

    records.sort(key=lambda record: str(record["sample_id"]))
    failures.sort(key=lambda entry: str(entry["label_relative_path"]))
    payload = {
        "gold_version": 1,
        "records": records,
        "failed": failures,
        "statistics": {
            "label_count": len(records) + len(failures),
            "record_count": len(records),
            "failed_count": len(failures),
        },
    }
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "audit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "gold.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (output_root / "gold.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(prog="python scripts/build_gold.py")
    parser.add_argument("--labels-dir", "--labels_dir", dest="labels_dir", type=Path, required=True)
    parser.add_argument("--reports-dir", "--reports_dir", dest="reports_dir", type=Path, required=True)
    parser.add_argument("--output-dir", "--output_dir", dest="output_dir", type=Path, required=True)
    args = parser.parse_args()
    payload = build_gold(args.labels_dir, args.reports_dir, args.output_dir)
    print(
        json.dumps(
            {
                "gold_json": "gold.json",
                "gold_jsonl": "gold.jsonl",
                "audit_report": "audit_report.json",
                **payload["statistics"],  # type: ignore[dict-item]
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
