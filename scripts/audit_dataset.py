"""Audit all Word labels/reports and write a deterministic JSON report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audit import audit_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="python scripts/audit_dataset.py")
    parser.add_argument("--labels-dir", "--labels_dir", dest="labels_dir", type=Path, required=True)
    parser.add_argument("--reports-dir", "--reports_dir", dest="reports_dir", type=Path, required=True)
    parser.add_argument("--output-dir", "--output_dir", dest="output_dir", type=Path, required=True)
    args = parser.parse_args()

    report = audit_dataset(args.labels_dir, args.reports_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "audit_report.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
