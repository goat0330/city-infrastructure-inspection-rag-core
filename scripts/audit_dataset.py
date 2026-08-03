"""Audit all Word labels/reports and write deterministic JSON and Markdown."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.audit import audit_dataset, render_audit_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="python scripts/audit_dataset.py")
    parser.add_argument("--labels-dir", "--labels_dir", dest="labels_dir", type=Path, required=True)
    parser.add_argument("--reports-dir", "--reports_dir", dest="reports_dir", type=Path, required=True)
    parser.add_argument("--output-dir", "--output_dir", dest="output_dir", type=Path, required=True)
    args = parser.parse_args()

    report = audit_dataset(args.labels_dir, args.reports_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "audit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "audit_report.md").write_text(
        render_audit_markdown(report), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "audit_json": "audit_report.json",
                "audit_markdown": "audit_report.md",
                "labels": report["file_statistics"]["labels"]["total_files"],  # type: ignore[index]
                "reports": report["file_statistics"]["reports"]["total_files"],  # type: ignore[index]
                "parse_failed": report["label_parsing"]["failed"],  # type: ignore[index]
                "quality_flags": report["label_parsing"]["quality_flag_count"],  # type: ignore[index]
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
