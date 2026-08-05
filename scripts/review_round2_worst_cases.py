from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.evaluation.scorer import score_dataset
from src.extraction import extract_report


def sample_id_from_name(name: str) -> str:
    if "K38+576" in name:
        return "2012年-上界路K38+576人行天桥"
    if "丁家院" in name:
        return "2012年-丁家院大桥"
    if "杨公桥立交EC" in name:
        return "2013年-12-035杨公桥立交EC匝道桥"
    if "杨公桥EC" in name and "人行通道" in name:
        return "2012年-杨公桥EC匝道人行通道"
    raise ValueError(f"unrecognised review sample: {name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    input_dir = args.review_root / "02_evidence" / "converted-input-docx"
    gold_path = args.review_root / "02_evidence" / "selected-gold" / "selected-gold.jsonl"
    args.output_dir.mkdir(parents=True, exist_ok=True)

    predictions: list[dict[str, object]] = []
    samples: list[dict[str, object]] = []
    for path in sorted(input_dir.glob("*.docx"), key=lambda item: item.stat().st_size):
        probe = extract_report(path)
        sample_id = sample_id_from_name(probe.prediction.summary.bridge_name)
        result = extract_report(path, source_file=sample_id + ".docx")
        prediction = result.prediction.to_dict()
        predictions.append(prediction)
        samples.append(
            {
                "sample_id": sample_id,
                "facility_name": result.prediction.summary.bridge_name,
                "report_date": result.prediction.summary.report_date,
                "defect_count": len(result.prediction.defects),
                "recommendation_count": len(result.prediction.recommendations),
                "recommendations_summary": result.prediction.summary.recommendations_summary,
                "all_defect_indexes_nonempty": all(item.index for item in result.prediction.defects),
                "all_recommendation_categories_nonempty": all(item.category for item in result.prediction.recommendations),
            }
        )

    prediction_path = args.output_dir / "predictions.jsonl"
    prediction_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in predictions),
        encoding="utf-8",
    )
    gold = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line]
    score = score_dataset(gold, predictions)
    report = {
        "sample_count": len(samples),
        "micro_total_score": score["micro_total_score"],
        "macro_total_score": score["macro_total_score"],
        "sections": {
            name: {
                "micro_score": value["score"],
                "macro_score": value["macro_score"],
                "f1": value["f1"],
            }
            for name, value in score["sections"].items()
        },
        "samples": samples,
    }
    (args.output_dir / "review-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
