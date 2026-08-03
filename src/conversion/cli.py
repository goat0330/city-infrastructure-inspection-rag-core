"""Command-line entry point for the legacy Word conversion batch."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .converter import convert_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="convert_reports")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--soffice-path", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = convert_directory(
            args.input_dir,
            args.output_dir,
            args.state_path,
            args.soffice_path,
            timeout_seconds=args.timeout_seconds,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        parser.error(str(exc))

    counts = result.counts
    print(
        json.dumps(
            {
                "converted": counts["success"],
                "skipped": counts["skipped"],
                "failed": counts["failed"],
                "state_path": str(args.state_path.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
