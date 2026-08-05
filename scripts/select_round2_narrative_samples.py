#!/usr/bin/env python3
"""Build a high-error + medium-error narrative revalidation sample manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

CORE_SAMPLE_IDS = (
    "2012年-杨公桥A叉口人行通道",
    "2012年-杨公桥EC匝道人行通道",
    "2012年-桂花新村大桥",
    "2012年-梨子湾大桥",
    "2012年-凤中主线桥",
    "2013年-12-035杨公桥立交EC匝道桥",
    "2013年-12-027杨公桥立交DA-ED匝道桥",
    "2013年-12-030杨公桥DC匝道桥",
)
MEDIUM_SAMPLE_IDS = (
    "2012年-道角村大桥",
    "2012年-华岩寺大桥",
    "2012年-果园大桥",
    "2012年-成渝K354+365小桥",
    "2012年-新村分离式立交中桥",
    "2012年-大田坝大桥",
    "2012年-上界路K39+380上跨车行桥",
    "2012年-上界路K39+900上跨车行桥",
)


def _records(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return [dict(item) for item in value]
    if isinstance(value, Mapping) and isinstance(value.get("records"), list):
        return [dict(item) for item in value["records"]]
    raise ValueError(f"records not found in {path}")


def _docx_path(record: Mapping[str, Any]) -> str:
    provenance = record.get("provenance", {})
    source = provenance.get("source_report_relative_path", "") if isinstance(provenance, Mapping) else ""
    return str(Path(str(source)).with_suffix(".docx")).replace("\\", "/")


def build_manifest(gold_path: Path, score_path: Path) -> dict[str, Any]:
    gold = {record["sample_id"]: record for record in _records(gold_path)}
    scores = {record["sample_id"]: record for record in _records(score_path)}
    samples: list[dict[str, Any]] = []
    for group, sample_ids in (("core", CORE_SAMPLE_IDS), ("medium", MEDIUM_SAMPLE_IDS)):
        for sample_id in sample_ids:
            record = gold.get(sample_id)
            score = scores.get(sample_id)
            if record is None or score is None:
                raise ValueError(f"missing Gold or score record: {sample_id}")
            sections = score.get("sections", {})
            samples.append(
                {
                    "sample_id": sample_id,
                    "group": group,
                    "split": record.get("split", "train"),
                    "source_report_relative_path": record.get("provenance", {}).get(
                        "source_report_relative_path", ""
                    ),
                    "converted_docx_relative_path": _docx_path(record),
                    "baseline_total_score": score.get("total_score"),
                    "baseline_section_f1": {
                        key: value.get("f1")
                        for key, value in sections.items()
                        if isinstance(value, Mapping)
                    },
                }
            )
    return {
        "schema_version": "round2-narrative-revalidation-samples-v1",
        "sample_count": len(samples),
        "groups": {
            "core": len(CORE_SAMPLE_IDS),
            "medium": len(MEDIUM_SAMPLE_IDS),
        },
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--score", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_manifest(args.gold, args.score)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "succeeded", "sample_count": result["sample_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
