"""Build deterministic Gold JSON from readable label DOCX files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.gold import build_gold  # noqa: E402


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
                "audit_markdown": "audit_report.md",
                **payload["statistics"],  # type: ignore[dict-item]
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
