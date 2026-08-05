"""Render structured inspection predictions into the production DOCX template."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import _Row
from docx.text.paragraph import Paragraph

from .style_spec import (
    CAUSE_NUMBER_FORMAT,
    DEFECT_CENTERED_COLUMNS,
    DEFECT_LEFT_ALIGNED_COLUMNS,
    RECOMMENDATION_CENTERED_COLUMNS,
    RECOMMENDATION_LEFT_ALIGNED_COLUMNS,
    TREATMENT_NUMBER_FORMAT,
)
from .submission_document import SubmissionDocument, build_submission_document


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_PATH = _REPO_ROOT / "assets" / "templates" / "information_extraction_v1.docx"
DEFAULT_FIELDS_PATH = _REPO_ROOT / "assets" / "templates" / "template_fields.json"
_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}")


def _load_contract(path: str | Path | None) -> Mapping[str, Any]:
    source = Path(path) if path is not None else DEFAULT_FIELDS_PATH
    if not source.is_file():
        raise FileNotFoundError(f"template field contract not found: {source}")
    value = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(value, Mapping):
        raise ValueError("template field contract must be a JSON object")
    return value


def _remove_run(run: Any) -> None:
    parent = run._element.getparent()
    if parent is not None:
        parent.remove(run._element)


def _set_paragraph_text(paragraph: Paragraph, value: object) -> None:
    """Replace the whole slot text while preserving the first run formatting."""

    text = "" if value is None else str(value)
    runs = paragraph.runs
    if runs:
        runs[0].text = text
        for run in runs[1:]:
            _remove_run(run)
    else:
        paragraph.add_run(text)


def _set_cell_text(cell: Any, value: object, *, align: int | None = None) -> None:
    while len(cell.paragraphs) > 1:
        paragraph = cell.paragraphs[-1]._element
        paragraph.getparent().remove(paragraph)
    paragraph = cell.paragraphs[0]
    _set_paragraph_text(paragraph, value)
    paragraph.paragraph_format.space_after = 0
    if align is not None:
        paragraph.alignment = align
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _find_paragraph(document: Document, text: str) -> Paragraph:
    matches = [paragraph for paragraph in document.paragraphs if paragraph.text.strip() == text]
    if len(matches) != 1:
        raise ValueError(f"expected one paragraph {text!r}, found {len(matches)}")
    return matches[0]


def _replace_exact_placeholder(document: Document, placeholder: str, value: object) -> None:
    matches: list[Paragraph] = []
    for paragraph in document.paragraphs:
        if paragraph.text.strip() == placeholder:
            matches.append(paragraph)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    if paragraph.text.strip() == placeholder:
                        matches.append(paragraph)
    if len(matches) != 1:
        raise ValueError(f"placeholder {placeholder!r} expected once, found {len(matches)}")
    _set_paragraph_text(matches[0], value)


def _remove_row(table: Any, row: _Row) -> None:
    table._tbl.remove(row._tr)


def _clone_row_before(table: Any, prototype: _Row) -> _Row:
    tr = deepcopy(prototype._tr)
    prototype._tr.addprevious(tr)
    return _Row(tr, table)


def _ensure_cant_split(row: _Row) -> None:
    trPr = row._tr.get_or_add_trPr()
    if trPr.find(qn("w:cantSplit")) is None:
        trPr.append(OxmlElement("w:cantSplit"))


def _render_recommendations(document: Document, contract: Mapping[str, Any], values: Sequence[Mapping[str, str]]) -> None:
    spec = contract["repeaters"]["recommendations"]
    table = document.tables[int(spec["table_index"])]
    prototype = table.rows[int(spec["prototype_row"])]
    for number, item in enumerate(values, 1):
        row = _clone_row_before(table, prototype)
        _ensure_cant_split(row)
        payload = {
            0: str(number),
            1: item.get("category", ""),
            2: item.get("content", ""),
            3: item.get("location", ""),
        }
        for index, cell in enumerate(row.cells):
            align = WD_ALIGN_PARAGRAPH.LEFT if index in RECOMMENDATION_LEFT_ALIGNED_COLUMNS else WD_ALIGN_PARAGRAPH.CENTER
            _set_cell_text(cell, payload[index], align=align)
    _remove_row(table, prototype)


def _normalized_defect_indexes(values: Sequence[Mapping[str, str]]) -> list[str]:
    result: list[str] = []
    previous_key: str | None = None
    display = 0
    for position, item in enumerate(values):
        raw = str(item.get("index", "")).strip()
        key = raw or previous_key or f"__row_{position}"
        if previous_key is None or key != previous_key:
            display += 1
        result.append(str(display))
        previous_key = key
    return result


def _merge_consecutive_indexes(table: Any) -> None:
    if len(table.rows) <= 2:
        return
    values = [row.cells[0].text.strip() for row in table.rows[1:]]
    start = 0
    while start < len(values):
        end = start
        while end + 1 < len(values) and values[end + 1] == values[start]:
            end += 1
        if end > start and values[start]:
            merged = table.cell(start + 1, 0).merge(table.cell(end + 1, 0))
            _set_cell_text(merged, values[start], align=WD_ALIGN_PARAGRAPH.CENTER)
        start = end + 1


def _render_defects(document: Document, contract: Mapping[str, Any], values: Sequence[Mapping[str, str]]) -> None:
    spec = contract["repeaters"]["defects"]
    table = document.tables[int(spec["table_index"])]
    prototype = table.rows[int(spec["prototype_row"])]
    indexes = _normalized_defect_indexes(values)
    for number, item in zip(indexes, values):
        row = _clone_row_before(table, prototype)
        _ensure_cant_split(row)
        payload = {
            0: number,
            1: item.get("location", ""),
            2: item.get("type", ""),
            3: item.get("description", ""),
            4: item.get("is_new", ""),
            5: item.get("previous_status", ""),
            6: item.get("development_degree", ""),
        }
        for index, cell in enumerate(row.cells):
            align = WD_ALIGN_PARAGRAPH.LEFT if index in DEFECT_LEFT_ALIGNED_COLUMNS else WD_ALIGN_PARAGRAPH.CENTER
            _set_cell_text(cell, payload[index], align=align)
    _remove_row(table, prototype)
    _merge_consecutive_indexes(table)


def _delete_paragraph(paragraph: Paragraph) -> None:
    element = paragraph._element
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


def _clone_paragraph_before(prototype: Paragraph) -> Paragraph:
    element = deepcopy(prototype._p)
    prototype._p.addprevious(element)
    return Paragraph(element, prototype._parent)


def _render_paragraph_repeater(
    document: Document,
    contract: Mapping[str, Any],
    name: str,
    values: Sequence[str],
) -> None:
    spec = contract["repeaters"][name]
    heading = _find_paragraph(document, str(spec["anchor_heading"]))
    paragraphs = document.paragraphs
    heading_index = next(i for i, item in enumerate(paragraphs) if item._p is heading._p)
    offset = int(spec.get("prototype_paragraph_offset", 1))
    if heading_index + offset >= len(paragraphs):
        raise ValueError(f"prototype paragraph missing after {heading.text!r}")
    prototype = paragraphs[heading_index + offset]
    numbering = spec.get("numbering", {})
    mode = str(numbering.get("mode", "none"))
    fmt = str(numbering.get("format", "{n}"))
    for number, value in enumerate(values, int(numbering.get("start", 1))):
        paragraph = _clone_paragraph_before(prototype)
        prefix = fmt.format(n=number) if mode == "renderer_prefix" else ""
        _set_paragraph_text(paragraph, f"{prefix}{value}")
    _delete_paragraph(prototype)


def _all_text(document: Document) -> str:
    values = [paragraph.text for paragraph in document.paragraphs]
    values.extend(
        cell.text
        for table in document.tables
        for row in table.rows
        for cell in row.cells
    )
    return "\n".join(values)


def _validate_rendered(document: Document) -> None:
    remaining = sorted(set(_PLACEHOLDER_RE.findall(_all_text(document))))
    if remaining:
        raise ValueError(f"rendered document contains unresolved placeholders: {remaining[:10]}")
    if len(document.tables) != 3:
        raise ValueError(f"production template must contain exactly three tables, found {len(document.tables)}")
    if len(document.tables[0].columns) != 3:
        raise ValueError("summary table must contain three columns")
    if len(document.tables[1].columns) != 4:
        raise ValueError("recommendation table must contain four columns")
    if len(document.tables[2].columns) != 7:
        raise ValueError("defect table must contain seven columns")


def render_template_report(
    source: object,
    output_path: str | Path,
    *,
    template_path: str | Path | None = None,
    fields_path: str | Path | None = None,
    facility_context: object = None,
    field_states: object = None,
) -> Path:
    """Render one prediction using the frozen production DOCX template."""

    submission = (
        source
        if isinstance(source, SubmissionDocument)
        else build_submission_document(
            source,
            facility_context=facility_context,
            field_states=field_states,
        )
    )
    template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE_PATH
    if not template.is_file():
        raise FileNotFoundError(f"production template not found: {template}")
    contract = _load_contract(fields_path)
    document = Document(template)

    for field, value in submission.scalars.items():
        _replace_exact_placeholder(document, "{{" + field + "}}", value)
    for field in (
        "score_and_grade",
        "history_and_defects",
        "current_structure_state",
        "comprehensive_judgement",
    ):
        _replace_exact_placeholder(document, "{{" + field + "}}", getattr(submission, field))

    _render_recommendations(document, contract, submission.recommendations)
    _render_defects(document, contract, submission.defects)
    _render_paragraph_repeater(document, contract, "causes", submission.causes)
    _render_paragraph_repeater(document, contract, "treatments", submission.treatments)
    _render_paragraph_repeater(document, contract, "safety_impacts", submission.safety_impacts)
    _validate_rendered(document)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    return output
