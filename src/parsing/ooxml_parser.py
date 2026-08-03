from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile
import xml.etree.ElementTree as ET

try:
    from ..contracts import (
        DocumentModel,
        ParagraphBlock,
        SourceAnchor,
        TableBlock,
        TableCell,
        TableRow,
    )
except ImportError:  # pragma: no cover - supports an installed ``src`` layout.
    from contracts import (  # type: ignore[no-redef]
        DocumentModel,
        ParagraphBlock,
        SourceAnchor,
        TableBlock,
        TableCell,
        TableRow,
    )


WORD_DOCUMENT_PATH = "word/document.xml"


@dataclass
class _CellRecord:
    row_index: int
    column_index: int
    raw_text: str
    column_span: int = 1
    row_span: int = 1


def parse_docx(path: str | Path, *, source_file: str | None = None) -> DocumentModel:
    """Parse the main WordprocessingML document from a DOCX package."""

    source_path = Path(path)
    with ZipFile(source_path) as package:
        document_xml = package.read(WORD_DOCUMENT_PATH)
    return parse_document_xml(
        document_xml,
        source_file=source_file if source_file is not None else str(source_path),
    )


def parse_document_xml(
    document_xml: str | bytes,
    *,
    source_file: str = "<document.xml>",
) -> DocumentModel:
    """Build a contract-compatible model from ``word/document.xml`` bytes."""

    root = ET.fromstring(document_xml)
    body = next((child for child in root if _local_name(child.tag) == "body"), None)
    if body is None:
        raise ValueError("word/document.xml does not contain a w:body element")

    blocks = []
    block_index = 0
    paragraph_index = 0
    table_index = 0

    for child in body:
        kind = _local_name(child.tag)
        if kind == "p":
            raw_text = _element_text(child)
            anchor = SourceAnchor(
                source_file,
                block_index,
                raw_text,
                paragraph_index=paragraph_index,
            )
            blocks.append(ParagraphBlock(block_index, raw_text, anchor))
            paragraph_index += 1
            block_index += 1
        elif kind == "tbl":
            table = _parse_table(child, source_file, block_index, table_index)
            blocks.append(table)
            table_index += 1
            block_index += 1

    return DocumentModel(source_file, tuple(blocks))


def _parse_table(
    table_element: ET.Element,
    source_file: str,
    block_index: int,
    table_index: int,
) -> TableBlock:
    active_vertical: dict[int, _CellRecord] = {}
    row_records: list[list[_CellRecord]] = []

    for row_index, row_element in enumerate(_children(table_element, "tr")):
        row_cells: list[_CellRecord] = []
        next_active: dict[int, _CellRecord] = {}
        next_column = _grid_before(row_element)

        for cell_element in _children(row_element, "tc"):
            cell_properties = _first_child(cell_element, "tcPr")
            column_span = _grid_span(cell_properties)
            vertical_merge = _first_child(cell_properties, "vMerge")
            merge_value = _attribute(vertical_merge, "val")
            continuation = (
                vertical_merge is not None
                and merge_value != "restart"
                and active_vertical.get(next_column) is not None
                and active_vertical[next_column].column_index == next_column
            )

            if continuation:
                start_cell = active_vertical[next_column]
                record = _CellRecord(
                    row_index=row_index,
                    column_index=start_cell.column_index,
                    raw_text=_element_text(cell_element),
                    column_span=start_cell.column_span,
                )
                start_cell.row_span += 1
                _mark_active(next_active, record.column_index, record.column_span, start_cell)
                next_column = record.column_index + record.column_span
                row_cells.append(record)
                continue

            record = _CellRecord(
                row_index=row_index,
                column_index=next_column,
                raw_text=_element_text(cell_element),
                column_span=column_span,
            )
            row_cells.append(record)
            if vertical_merge is not None:
                _mark_active(next_active, record.column_index, record.column_span, record)
            next_column += record.column_span

        row_records.append(row_cells)
        active_vertical = next_active

    raw_text = "\n".join(
        "\t".join(cell.raw_text for cell in row_cells) for row_cells in row_records
    )
    rows = tuple(
        TableRow(
            row_index=row_index,
            cells=tuple(
                TableCell(
                    row_index=cell.row_index,
                    column_index=cell.column_index,
                    raw_text=cell.raw_text,
                    row_span=cell.row_span,
                    column_span=cell.column_span,
                    source=SourceAnchor(
                        source_file,
                        block_index,
                        cell.raw_text,
                        table_index=table_index,
                        row_index=cell.row_index,
                        column_index=cell.column_index,
                    ),
                )
                for cell in row_cells
            ),
        )
        for row_index, row_cells in enumerate(row_records)
    )
    table_source = SourceAnchor(
        source_file,
        block_index,
        raw_text,
        table_index=table_index,
    )
    return TableBlock(
        block_index=block_index,
        raw_text=raw_text,
        source=table_source,
        table_index=table_index,
        rows=rows,
    )


def _mark_active(
    active: dict[int, _CellRecord],
    column_index: int,
    column_span: int,
    start_cell: _CellRecord,
) -> None:
    for column in range(column_index, column_index + column_span):
        active[column] = start_cell


def _element_text(element: ET.Element) -> str:
    pieces: list[str] = []
    for descendant in element.iter():
        kind = _local_name(descendant.tag)
        if kind in {"t", "delText", "instrText"}:
            pieces.append(descendant.text or "")
        elif kind == "tab":
            pieces.append("\t")
        elif kind in {"br", "cr"}:
            pieces.append("\n")
    return "".join(pieces)


def _grid_span(cell_properties: ET.Element | None) -> int:
    value = _attribute(_first_child(cell_properties, "gridSpan"), "val")
    try:
        return max(1, int(value)) if value is not None else 1
    except ValueError:
        return 1


def _grid_before(row_element: ET.Element) -> int:
    row_properties = _first_child(row_element, "trPr")
    value = _attribute(_first_child(row_properties, "gridBefore"), "val")
    try:
        return max(0, int(value)) if value is not None else 0
    except ValueError:
        return 0


def _children(element: ET.Element, local_name: str) -> Iterable[ET.Element]:
    return (child for child in element if _local_name(child.tag) == local_name)


def _first_child(element: ET.Element | None, local_name: str) -> ET.Element | None:
    if element is None:
        return None
    return next(_children(element, local_name), None)


def _attribute(element: ET.Element | None, local_name: str) -> str | None:
    if element is None:
        return None
    for name, value in element.attrib.items():
        if _local_name(name) == local_name:
            return value
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
