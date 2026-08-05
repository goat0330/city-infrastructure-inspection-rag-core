"""Render prediction JSONL to manifest-named DOCX files with the production template."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.submission import render_prediction_batch_template


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("assets/templates/information_extraction_v1.docx"),
    )
    parser.add_argument(
        "--fields",
        type=Path,
        default=Path("assets/templates/template_fields.json"),
    )
    args = parser.parse_args()
    result = render_prediction_batch_template(
        args.predictions,
        args.manifest,
        args.output_dir,
        template_path=args.template,
        fields_path=args.fields,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
