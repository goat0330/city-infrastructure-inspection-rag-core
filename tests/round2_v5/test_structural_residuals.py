from __future__ import annotations

from src.contracts import DocumentModel, ParagraphBlock, SourceAnchor, TableBlock, TableCell, TableRow
from src.extraction.defects.extractor import _locate_tables
from src.extraction.recommendations.extractor import _numbered_items
from src.extraction.summary.extractor import _normalise_bridge_name, extract_summary
from src.extraction.text_sections import _clean_detailed_fact
from src.routing import SectionCategory, SectionRoute


def anchor(block: int, text: str = "") -> SourceAnchor:
    return SourceAnchor("sample.docx", block, text)


def cell(row: int, col: int, text: str, block: int = 0) -> TableCell:
    return TableCell(row, col, text, source=SourceAnchor("sample.docx", block, text, table_index=block, row_index=row, column_index=col))


def table(block: int, rows: list[list[str]]) -> TableBlock:
    table_rows = tuple(TableRow(i, tuple(cell(i, j, value, block) for j, value in enumerate(values))) for i, values in enumerate(rows))
    raw = "\n".join("\t".join(values) for values in rows)
    return TableBlock(block, raw, anchor(block, raw), table_index=block, rows=table_rows)


def test_cover_table_detection_range_end_is_report_date() -> None:
    cover = table(0, [["工程名称", "上界路K38+576人行天桥"], ["检验日期（Sampling date）", "2012/3/24～6/12"]])
    result = extract_summary(DocumentModel("sample.docx", (cover,)), routes=())
    assert result.summary.report_date == "2012年6月12日"


def test_compact_numbered_recommendations_are_split() -> None:
    items = _numbered_items("建议如下：1.修复裂缝；2.清理排水孔；3.加强观察。")
    assert [item.index for item in items] == ["1", "2", "3"]
    assert [item.text.rstrip("；。") for item in items] == ["修复裂缝", "清理排水孔", "加强观察"]


def test_formal_conclusion_keeps_result_not_raw_calculation() -> None:
    text = "承载能力满足设计荷载要求；截面号18，实测值10.30MPa，理论值46.87MPa，校验系数0.211。"
    cleaned = _clean_detailed_fact(text, formal=True)
    assert "承载能力满足设计荷载要求" in cleaned
    assert "截面号" not in cleaned


def test_misrouted_ordinary_table_falls_back_to_real_defect_table() -> None:
    ordinary = table(0, [["检测项目", "检测结果"], ["强度", "满足要求"]])
    defect = table(1, [["序号", "位置", "病害种类", "病害情况"], ["1", "梁底", "裂缝", "竖向裂缝"]])
    heading = ParagraphBlock(2, "病害检查", anchor(2, "病害检查"), heading_level=1)
    route = SectionRoute(SectionCategory.DEFECT_TABLE, heading, (ordinary,), heading.source)
    tables, used_fallback = _locate_tables(DocumentModel("sample.docx", (ordinary, defect, heading)), (route,))
    assert used_fallback is True
    assert tables == (defect,)


def test_facility_name_preserves_source_roman_numeral_spelling() -> None:
    assert _normalise_bridge_name("主线III号桥") == "主线III号桥"
    assert _normalise_bridge_name("主线Ⅲ号桥") == "主线Ⅲ号桥"
