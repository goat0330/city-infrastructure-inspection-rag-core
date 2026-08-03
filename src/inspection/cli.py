from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Sequence


COMMANDS = (
    "audit",
    "build-gold",
    "convert",
    "parse",
    "route",
    "score",
    "predict",
    "render",
    "validate",
    "package",
    "validate-package",
)


def _add_common_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--labels-dir", type=Path, required=True)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m inspection")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="audit local labels and reports")
    _add_common_data_args(audit)

    gold = subparsers.add_parser("build-gold", help="build Gold JSON/JSONL from label DOCX files")
    _add_common_data_args(gold)

    convert = subparsers.add_parser("convert", help="convert legacy .doc reports to .docx")
    convert.add_argument("--input-dir", type=Path, required=True)
    convert.add_argument("--output-dir", type=Path, required=True)
    convert.add_argument("--state-path", type=Path, required=True)
    convert.add_argument("--soffice-path", type=Path)
    convert.add_argument("--timeout-seconds", type=float, default=300.0)

    parse = subparsers.add_parser("parse", help="parse one DOCX into the Word structure model")
    parse.add_argument("--input", type=Path, required=True)
    parse.add_argument("--output", type=Path)
    parse.add_argument("--source-file")

    route = subparsers.add_parser("route", help="route one DOCX into known report sections")
    route.add_argument("--input", type=Path, required=True)
    route.add_argument("--output", type=Path)
    route.add_argument("--source-file")

    score = subparsers.add_parser("score", help="score prediction JSON/JSONL against Gold")
    score.add_argument("--gold", type=Path, required=True)
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--output", type=Path)
    score.add_argument("--weights", type=Path)

    predict = subparsers.add_parser("predict", help="reserved for the B2 extraction pipeline")
    predict.add_argument("--input", type=Path)
    predict.add_argument("--output", type=Path)

    render = subparsers.add_parser("render", help="render one Gold or prediction JSON record to DOCX")
    render.add_argument("--input", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="validate one rendered DOCX")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--output", type=Path)

    package = subparsers.add_parser(
        "package", help="create a root-only tar.gz containing final .doc result files"
    )
    package.add_argument("--input-dir", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--manifest", type=Path)
    package.add_argument("--extension", default=".doc")

    validate_package = subparsers.add_parser(
        "validate-package", help="validate an existing tar.gz submission package"
    )
    validate_package.add_argument("--input", type=Path, required=True)
    validate_package.add_argument("--manifest", type=Path)
    validate_package.add_argument("--extension", default=".doc")
    validate_package.add_argument("--output", type=Path)
    return parser


def _write_json(payload: object, output: Path | None = None) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(encoded, end="")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")


def _route_payload(model: object, routes: Sequence[object]) -> dict[str, object]:
    source_file = str(getattr(model, "source_file", ""))
    items: list[dict[str, object]] = []
    for route in routes:
        category = getattr(route, "category", "")
        items.append(
            {
                "category": getattr(category, "value", str(category)),
                "heading": asdict(getattr(route, "heading")),
                "blocks": [asdict(block) for block in getattr(route, "blocks")],
                "source": asdict(getattr(route, "source")),
            }
        )
    return {"source_file": source_file, "route_count": len(items), "routes": items}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "audit":
        from src.audit import audit_dataset, render_audit_markdown

        report = audit_dataset(args.labels_dir, args.reports_dir)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        _write_json(report, args.output_dir / "audit_report.json")
        (args.output_dir / "audit_report.md").write_text(
            render_audit_markdown(report), encoding="utf-8"
        )
        _write_json(
            {
                "status": "succeeded",
                "audit_json": str(args.output_dir / "audit_report.json"),
                "audit_markdown": str(args.output_dir / "audit_report.md"),
            }
        )
        return 0

    if args.command == "build-gold":
        from src.gold import build_gold

        payload = build_gold(args.labels_dir, args.reports_dir, args.output_dir)
        _write_json({"status": "succeeded", **payload["statistics"]})  # type: ignore[arg-type]
        return 0

    if args.command == "convert":
        from src.conversion import convert_directory

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
        _write_json({"status": "completed", **result.counts, "state_path": str(args.state_path)})
        return 1 if result.counts["failed"] else 0

    if args.command == "parse":
        from src.parsing import parse_docx

        model = parse_docx(args.input, source_file=args.source_file)
        _write_json(model.to_dict(), args.output)
        return 0

    if args.command == "route":
        from src.parsing import parse_docx
        from src.routing import route_sections

        model = parse_docx(args.input, source_file=args.source_file)
        _write_json(_route_payload(model, route_sections(model)), args.output)
        return 0

    if args.command == "score":
        from src.evaluation import load_records, load_weights, score_dataset

        result = score_dataset(
            load_records(args.gold),
            load_records(args.predictions),
            load_weights(args.weights),
        )
        _write_json(result, args.output)
        return 0

    if args.command == "render":
        from src.rendering import render_report

        try:
            path = render_report(args.input, args.output)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        _write_json({"status": "succeeded", "output": str(path)})
        return 0

    if args.command == "validate":
        from src.submission import validate_submission

        result = validate_submission(args.input)
        _write_json(result, args.output)
        return 0 if result.get("valid") is True else 1

    if args.command == "package":
        from src.submission import create_submission_package, load_expected_names

        try:
            result = create_submission_package(
                args.input_dir,
                args.output,
                expected_names=load_expected_names(args.manifest),
                extension=args.extension,
            )
        except (FileNotFoundError, NotADirectoryError, OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        _write_json(result)
        return 0

    if args.command == "validate-package":
        from src.submission import load_expected_names, validate_submission_package

        result = validate_submission_package(
            args.input,
            expected_names=load_expected_names(args.manifest),
            extension=args.extension,
        )
        _write_json(result, args.output)
        return 0 if result.get("valid") is True else 1

    _write_json(
        {
            "command": args.command,
            "status": "not_implemented",
            "message": "B2 字段抽取器尚未完成；该命令不会生成伪预测。",
        }
    )
    return 2
