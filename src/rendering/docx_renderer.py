"""Render Gold records and inspection predictions into a readable DOCX."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.shared import Pt

from ..contracts.prediction import InspectionPrediction

SUMMARY_FIELDS = (
    ("bridge_name", "桥梁名称"), ("report_date", "报告日期"),
    ("overall_score", "总体评分"), ("overall_grade", "总体等级"),
    ("superstructure_score", "上部结构评分"), ("superstructure_grade", "上部结构等级"),
    ("substructure_score", "下部结构评分"), ("substructure_grade", "下部结构等级"),
    ("deck_score", "桥面系评分"), ("deck_grade", "桥面系等级"),
    ("previous_overall_score", "上一次总体评分"), ("previous_overall_grade", "上一次总体等级"),
    ("trend", "病害发展趋势与具体说明"), ("overall_conclusion", "总体结论"),
    ("risk_points", "主要风险点"), ("recommendations_summary", "建议"),
)
RECOMMENDATION_COLUMNS = (("index", "序号"), ("category", "建议类别"), ("content", "建议内容"), ("location", "病害部位"))
DEFECT_COLUMNS = (("index", "序号"), ("location", "病害部位"), ("defect_type", "病害类型"), ("description", "病害描述"), ("is_new", "是否新增"), ("previous_status", "上一次定检状态"), ("development", "发展程度"))
TEXT_SECTIONS = (("detailed_conclusion", "详细结论"), ("causes", "病害成因"), ("treatments", "处置建议"), ("safety_impact", "安全影响"))


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _items(value: Any) -> list[Any]:
    if value is None: return []
    if isinstance(value, (str, bytes)): return [value]
    if isinstance(value, Sequence): return list(value)
    return [value]


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping): return value
    if is_dataclass(value): return asdict(value)
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else {}


def _load_source(source: Any) -> Mapping[str, Any] | list[Any]:
    if isinstance(source, Mapping): return source
    if isinstance(source, InspectionPrediction): return source.to_dict()
    if isinstance(source, Path): return json.loads(source.read_text(encoding="utf-8-sig"))
    if isinstance(source, str):
        stripped = source.lstrip()
        return json.loads(source) if stripped.startswith(("{", "[")) else json.loads(Path(source).read_text(encoding="utf-8-sig"))
    converted = _mapping(source)
    if converted: return converted
    raise TypeError("unsupported render input")


def _record(source: Any) -> Mapping[str, Any]:
    payload = _load_source(source)
    if isinstance(payload, list):
        if len(payload) != 1: raise ValueError("rendering requires exactly one record")
        payload = payload[0]
    if "records" in payload and "summary" not in payload:
        records = payload["records"]
        if not isinstance(records, list) or len(records) != 1: raise ValueError("Gold JSON must contain exactly one record")
        payload = records[0]
    if not isinstance(payload, Mapping): raise ValueError("record must be an object")
    return payload


def _set_cell(cell: Any, value: Any) -> None:
    cell.text = _text(value)
    for paragraph in cell.paragraphs: paragraph.paragraph_format.space_after = Pt(0)


def _row_value(row: Any, field: str) -> str:
    return _text(row.get(field, "")) if isinstance(row, Mapping) else _text(getattr(row, field, ""))


def _add_table(document: Any, columns: Sequence[tuple[str, str]], rows: Any) -> Any:
    table = document.add_table(rows=1, cols=len(columns)); table.style = "Table Grid"
    for cell, (_, label) in zip(table.rows[0].cells, columns): _set_cell(cell, label)
    data_rows = _items(rows)
    for row_data in data_rows:
        row = table.add_row()
        for cell, (field, _) in zip(row.cells, columns): _set_cell(cell, _row_value(row_data, field))
    _merge_repeated_indexes(table, len(data_rows)); return table


def _merge_repeated_indexes(table: Any, count: int) -> None:
    values = [table.rows[i + 1].cells[0].text.strip() for i in range(count)]
    start = 0
    while start < count:
        value, end = values[start], start
        while value and end + 1 < count and values[end + 1] == value: end += 1
        if end > start:
            merged = table.cell(start + 1, 0).merge(table.cell(end + 1, 0)); _set_cell(merged, value)
            merged.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        start = end + 1


def _add_text_section(document: Any, title: str, values: Any) -> None:
    document.add_heading(title, level=1)
    for value in _items(values): document.add_paragraph(_text(value))


def render_report(source: Any, output_path: Path | str) -> Path:
    """Render one Gold or prediction record with the official section order."""

    record = _record(source)
    document = Document()
    document.add_heading("城市基础设施定检报告信息提取", level=0)

    document.add_heading("1、简要信息（20 分）", level=1)
    summary = _mapping(record.get("summary"))
    table = document.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    _set_cell(table.rows[0].cells[0], "字段")
    _set_cell(table.rows[0].cells[1], "内容")
    for field, label in SUMMARY_FIELDS:
        row = table.add_row()
        _set_cell(row.cells[0], label)
        _set_cell(row.cells[1], summary.get(field, ""))

    document.add_heading("2、详细信息（80 分）", level=1)
    _add_text_section(document, "（1）详细结论（15 分）", record.get("detailed_conclusion"))

    document.add_heading("（2）建议明细（20 分）", level=1)
    _add_table(document, RECOMMENDATION_COLUMNS, record.get("recommendations"))

    document.add_heading("（3）病害列表（30 分）", level=1)
    _add_table(document, DEFECT_COLUMNS, record.get("defects"))

    _add_text_section(document, "病害成因（5 分）", record.get("causes"))
    _add_text_section(document, "处置建议（5 分）", record.get("treatments"))
    _add_text_section(document, "安全影响（5 分）", record.get("safety_impact"))

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path
