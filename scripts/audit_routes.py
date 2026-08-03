"""Audit section-routing coverage across a converted DOCX corpus."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts import TableBlock
from src.parsing import parse_docx
from src.routing import SectionCategory, route_sections


CORE_CATEGORIES = (SectionCategory.DEFECT_TABLE.value, SectionCategory.RECOMMENDATIONS.value)
ALL_CATEGORIES = tuple(category.value for category in SectionCategory)


def _preview(value: object, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _bucket(value: int) -> str:
    if value < 50:
        return "0-49"
    if value < 200:
        return "50-199"
    if value < 500:
        return "200-499"
    return "500+"


def _route_record(path: Path, input_dir: Path) -> dict[str, Any]:
    relative_path = path.relative_to(input_dir).as_posix()
    try:
        model = parse_docx(path, source_file=relative_path)
        routes = route_sections(model)
    except Exception as exc:  # per-document audit must not stop the corpus run
        return {
            "source_file": relative_path,
            "status": "parse_failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    category_counts = Counter(route.category.value for route in routes)
    block_owners: defaultdict[int, list[int]] = defaultdict(list)
    source_keys: Counter[tuple[str, int, int | None]] = Counter()
    route_items: list[dict[str, Any]] = []
    for route_index, route in enumerate(routes):
        block_indices = [block.block_index for block in route.blocks]
        table_indices = [
            block.table_index for block in route.blocks if isinstance(block, TableBlock)
        ]
        for block_index in block_indices:
            block_owners[block_index].append(route_index)
        source_keys[
            (route.category.value, route.source.block_index, route.source.table_index)
        ] += 1
        route_items.append(
            {
                "route_index": route_index,
                "category": route.category.value,
                "heading": _preview(route.heading.raw_text),
                "source": asdict(route.source),
                "block_indices": block_indices,
                "table_indices": table_indices,
                "block_count": len(block_indices),
                "table_count": len(table_indices),
            }
        )

    duplicate_source_hits = [
        {
            "category": category,
            "block_index": block_index,
            "table_index": table_index,
            "count": count,
        }
        for (category, block_index, table_index), count in sorted(source_keys.items())
        if count > 1
    ]
    overlapping_blocks = [
        {"block_index": block_index, "route_indices": route_indices}
        for block_index, route_indices in sorted(block_owners.items())
        if len(route_indices) > 1
    ]

    paragraphs = len(model.blocks) - sum(isinstance(block, TableBlock) for block in model.blocks)
    tables = sum(isinstance(block, TableBlock) for block in model.blocks)
    present = {category for category, count in category_counts.items() if count}
    route_signature = "+".join(category for category in ALL_CATEGORIES if category in present) or "none"
    template_cluster = f"p{_bucket(paragraphs)}|t{_bucket(tables)}|routes={route_signature}"
    return {
        "source_file": relative_path,
        "status": "parsed",
        "paragraph_count": paragraphs,
        "table_count": tables,
        "block_count": len(model.blocks),
        "route_count": len(routes),
        "category_counts": {category: category_counts.get(category, 0) for category in ALL_CATEGORIES},
        "missing_categories": [category for category in ALL_CATEGORIES if category not in present],
        "missing_core_categories": [category for category in CORE_CATEGORIES if category not in present],
        "core_route_success": all(category in present for category in CORE_CATEGORIES),
        "duplicate_source_hits": duplicate_source_hits,
        "overlapping_blocks": overlapping_blocks,
        "template_cluster": template_cluster,
        "routes": route_items,
    }


def audit_routes(input_dir: str | Path) -> dict[str, Any]:
    root = Path(input_dir)
    if not root.is_dir():
        raise NotADirectoryError(f"input directory does not exist: {root}")
    paths = sorted(root.rglob("*.docx"), key=lambda path: path.relative_to(root).as_posix())
    records = [_route_record(path, root) for path in paths]
    parsed = [record for record in records if record["status"] == "parsed"]
    failed = [record for record in records if record["status"] != "parsed"]
    category_hits = {
        category: sum(record["category_counts"][category] > 0 for record in parsed)
        for category in ALL_CATEGORIES
    }
    template_data: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"reports": 0, "parsed": 0, "parse_failed": 0, "core_route_success": 0}
    )
    for record in records:
        cluster = record.get("template_cluster", "parse_failed")
        item = template_data[cluster]
        item["reports"] += 1
        if record["status"] == "parsed":
            item["parsed"] += 1
            item["core_route_success"] += int(record["core_route_success"])
        else:
            item["parse_failed"] += 1

    return {
        "version": 1,
        "input_dir": str(root),
        "report_count": len(paths),
        "parsed_count": len(parsed),
        "parse_failed_count": len(failed),
        "category_hit_counts": category_hits,
        "category_coverage": {
            category: (category_hits[category] / len(parsed) if parsed else 0.0)
            for category in ALL_CATEGORIES
        },
        "core_route_success_count": sum(record["core_route_success"] for record in parsed),
        "duplicate_source_hit_report_count": sum(
            bool(record.get("duplicate_source_hits")) for record in parsed
        ),
        "overlap_report_count": sum(bool(record.get("overlapping_blocks")) for record in parsed),
        "missing_core_category_counts": {
            category: sum(category in record.get("missing_core_categories", []) for record in parsed)
            for category in CORE_CATEGORIES
        },
        "template_clusters": dict(sorted(template_data.items())),
        "records": records,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# P1 全量章节路由审计",
        "",
        "> 仅记录结构统计、路由类别、来源锚点和质量标记；不写入报告正文。",
        "",
        "## 汇总",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| DOCX 数量 | {report['report_count']} |",
        f"| 解析成功 | {report['parsed_count']} |",
        f"| 解析失败 | {report['parse_failed_count']} |",
        f"| 核心路由同时命中 | {report['core_route_success_count']} |",
        f"| 重复来源命中报告 | {report['duplicate_source_hit_report_count']} |",
        f"| 路由块重叠报告 | {report['overlap_report_count']} |",
        "",
        "## 类别覆盖",
        "",
        "| 类别 | 命中报告数 | 覆盖率 |",
        "| --- | ---: | ---: |",
    ]
    for category in ALL_CATEGORIES:
        lines.append(
            f"| `{category}` | {report['category_hit_counts'][category]} | "
            f"{report['category_coverage'][category]:.1%} |"
        )
    lines.extend(
        [
            "",
            "## 核心缺失",
            "",
            "| 类别 | 缺失报告数 |",
            "| --- | ---: |",
        ]
    )
    for category, count in report["missing_core_category_counts"].items():
        lines.append(f"| `{category}` | {count} |")
    lines.extend(
        [
            "",
            "## 模板簇",
            "",
            "| 模板簇 | 报告 | 解析成功 | 解析失败 | 核心路由成功 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for cluster, values in report["template_clusters"].items():
        lines.append(
            f"| `{cluster}` | {values['reports']} | {values['parsed']} | "
            f"{values['parse_failed']} | {values['core_route_success']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    report = audit_routes(args.input_dir)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "completed",
                "report_count": report["report_count"],
                "parsed_count": report["parsed_count"],
                "parse_failed_count": report["parse_failed_count"],
                "core_route_success_count": report["core_route_success_count"],
                "output_json": str(args.output_json),
                "output_md": str(args.output_md),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
