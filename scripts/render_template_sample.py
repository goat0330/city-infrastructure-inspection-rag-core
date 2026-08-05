"""Render one prediction JSON with the production template.

Usage:
    python scripts/render_template_sample.py prediction.json output.docx
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.rendering import render_template_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
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
    output = render_template_report(
        args.input,
        args.output,
        template_path=args.template,
        fields_path=args.fields,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
