from __future__ import annotations

from pathlib import Path

from src.evaluation import score_record
from src.gold import parse_label_docx
from src.rendering import render_report


def _gold_record() -> dict[str, object]:
    return {
        "sample_id": "2012年-测试桥",
        "split": "train",
        "summary": {
            "bridge_name": "测试桥",
            "bridge_id": "",
            "report_date": "2026年8月",
            "overall_score": "88.0",
            "overall_grade": "B级",
            "superstructure_score": "87.0",
            "superstructure_grade": "B级",
            "substructure_score": "90.0",
            "substructure_grade": "A级",
            "deck_score": "86.0",
            "deck_grade": "B级",
            "previous_overall_score": "无",
            "previous_overall_grade": "无",
            "trend": "无",
            "overall_conclusion": "总体状况良好",
            "risk_points": "局部裂缝",
            "recommendations_summary": "1条尽快维修建议",
        },
        "detailed_conclusion": ["经综合评定，该桥总体技术状况良好。"],
        "recommendations": [
            {"index": "1", "category": "尽快维修", "content": "修复裂缝", "location": "桥面"}
        ],
        "defects": [
            {
                "index": "1",
                "location": "桥面",
                "defect_type": "裂缝",
                "description": "局部纵向裂缝",
                "is_new": "否",
                "previous_status": "无",
                "development": "无",
            }
        ],
        "causes": ["车辆荷载与材料老化共同作用。"],
        "treatments": ["对裂缝进行封闭处理。"],
        "safety_impact": ["当前不影响承载能力，但会影响耐久性。"],
        "statistics": {},
        "provenance": {
            "label_relative_path": "2012年/测试桥.docx",
            "source_report_relative_path": "2012年/测试桥.doc",
            "raw_report_included": False,
            "derivation": "synthetic test fixture",
        },
        "quality_flags": [],
    }


def test_gold_render_parse_roundtrip_scores_100(tmp_path: Path) -> None:
    labels_root = tmp_path / "labels"
    output = labels_root / "2012年" / "测试桥.docx"
    render_report(_gold_record(), output)

    parsed = parse_label_docx(output, labels_root, "2012年/测试桥.doc")
    result = score_record(_gold_record(), parsed)

    assert result["total_score"] == 100.0
