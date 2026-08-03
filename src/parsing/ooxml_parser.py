from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Mapping
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from ..contracts import (
    DocumentModel,
    ImageRelation,
    ParagraphBlock,
    SourceAnchor,
    TableBlock,
    TableCell,
    TableRow,
)


WORD_DOCUMENT_PATH = "word/document.xml"
WORD_RELATIONSHIPS_PATH = "word/_rels/document.xml.rels"
WORD_STYLES_PATH = "word/styles.xml"


@dataclass
class _CellRecord:
    row_index: int
    column_index: int
    raw_text: str
    column_span: int = 1
    row_span: int = 1
    is_merge_continuation: bool = False
    merge_origin_row: int | None = None
    merge_origin_column: int | None = None


def parse_docx(path: str | Path, *, source_file: str | None = None) -> DocumentModel:
    """Parse the main WordprocessingML document from a DOCX package."""

    source_path = Path(path)
    with ZipFile(source_path) as package:
        document_xml = package.read(WORD_DOCUMENT_PATH)
        relationships_xml = (
            package.read(WORD_RELATIONSHIPS_PATH)
            if WORD_RELATIONSHIPS_PATH in package.namelist()
            else None
        )
        styles_xml = (
            package.read(WORD_STYLES_PATH)
            if WORD_STYLES_PATH in package.namelist()
            else None
        )
    return parse_document_xml(
        document_xml,
        source_file=source_file if source_file is not None else str(source_path),
        relationships=_relationship_targets(relationships_xml),
        heading_styles=_heading_styles(styles_xml),
    )


def parse_document_xml(
    document_xml: str | bytes,
    *,
    source_file: str = "<document.xml>",
    relationships: Mapping[str, str] | None = None,
    heading_styles: Mapping[str, int] | None = None,
) -> DocumentModel:
    """Build a contract-compatible model from ``word/document.xml`` bytes."""

    relationships = relationships or {}
    heading_styles = heading_styles or {}
    root = ET.fromstring(document_xml)
    body = next((child for child in root if _local_name(child.tag) == "body"), None)
    if body is None:
        raise ValueError("word/document.xml does not contain a w:body element")

    blocks = []
    images: list[ImageRelation] = []
    block_index = 0
    paragraph_index = 0
    table_index = 0

    for child in body:
        kind = _local_name(child.tag)
        if kind == "p":
            raw_text = _element_text(child)
            style_id = _paragraph_style_id(child)
            anchor = SourceAnchor(
                source_file,
                block_index,
                raw_text,
                paragraph_index=paragraph_index,
            )
            blocks.append(
                ParagraphBlock(
                    block_index,
                    raw_text,
                    anchor,
                    heading_level=heading_styles.get(style_id) if style_id else None,
                    style_id=style_id,
                )
            )
            images.extend(
                _image_relations(
                    child,
                    relationships,
                    anchor,
                )
            )
            paragraph_index += 1
            block_index += 1
        elif kind == "tbl":
            table, table_images = _parse_table(
                child,
                source_file,
                block_index,
                table_index,
                relationships,
            )
            blocks.append(table)
            images.extend(table_images)
            table_index += 1
            block_index += 1

    return DocumentModel(source_file, tuple(blocks), tuple(images))


def _parse_table(
    table_element: ET.Element,
    source_file: str,
    block_index: int,
    table_index: int,
    relationships: Mapping[str, str],
) -> tuple[TableBlock, list[ImageRelation]]:
    active_vertical: dict[int, _CellRecord] = {}
    row_records: list[list[_CellRecord]] = []
    images: list[ImageRelation] = []

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
                    is_merge_continuation=True,
                    merge_origin_row=start_cell.row_index,
                    merge_origin_column=start_cell.column_index,
                )
                start_cell.row_span += 1
                _mark_active(next_active, record.column_index, record.column_span, start_cell)
                next_column = record.column_index + record.column_span
                row_cells.append(record)
            else:
                record = _CellRecord(
                    row_index=row_index,
                    column_index=next_column,
                    raw_text=_element_text(cell_element),
                    column_span=column_span,
                    merge_origin_row=row_index,
                    merge_origin_column=next_column,
                )
                row_cells.append(record)
                if vertical_merge is not None:
                    _mark_active(next_active, record.column_index, record.column_span, record)
                next_column += record.column_span

            cell_anchor = SourceAnchor(
                source_file,
                block_index,
                record.raw_text,
                table_index=table_index,
                row_index=row_index,
                column_index=record.column_index,
            )
            images.extend(
                _image_relations(cell_element, relationships, cell_anchor)
            )

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
                    is_merge_continuation=cell.is_merge_continuation,
                    merge_origin_row=cell.merge_origin_row,
                    merge_origin_column=cell.merge_origin_column,
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
    return (
        TableBlock(
            block_index=block_index,
            raw_text=raw_text,
            source=table_source,
            table_index=table_index,
            rows=rows,
        ),
        images,
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
    """Return visible text only; omit deleted text and Word field instructions."""

    pieces: list[str] = []
    for descendant in element.iter():
        kind = _local_name(descendant.tag)
        if kind == "t":
            pieces.append(descendant.text or "")
        elif kind == "tab":
            pieces.append("\t")
        elif kind in {"br", "cr"}:
            pieces.append("\n")
        elif kind == "noBreakHyphen":
            pieces.append("-")
        elif kind == "softHyphen":
            pieces.append("\u00ad")
    return "".join(pieces)


def _paragraph_style_id(paragraph: ET.Element) -> str | None:
    properties = _first_child(paragraph, "pPr")
    return _attribute(_first_child(properties, "pStyle"), "val")


def _relationship_targets(xml: bytes | None) -> dict[str, str]:
    if not xml:
        return {}
    root = ET.fromstring(xml)
    result: dict[str, str] = {}
    for relationship in root:
        if _local_name(relationship.tag) != "Relationship":
            continue
        relationship_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        relationship_type = relationship.attrib.get("Type", "")
        if relationship_id and target and relationship_type.endswith("/image"):
            result[relationship_id] = target
    return result


def _heading_styles(xml: bytes | None) -> dict[str, int]:
    if not xml:
        return {}
    root = ET.fromstring(xml)
    result: dict[str, int] = {}
    for style in root:
        if _local_name(style.tag) != "style":
            continue
        if _attribute(style, "type") not in {None, "paragraph"}:
            continue
        style_id = _attribute(style, "styleId")
        if not style_id:
            continue
        name = _attribute(_first_child(style, "name"), "val") or ""
        outline = None
        paragraph_properties = _first_child(style, "pPr")
        outline_value = _attribute(_first_child(paragraph_properties, "outlineLvl"), "val")
        if outline_value is not None:
            try:
                outline = int(outline_value) + 1
            except ValueError:
                outline = None
        if outline is None:
            match = re.search(r"(?:heading|标题)\s*([1-9])", name, re.IGNORECASE)
            if match:
                outline = int(match.group(1))
        if outline is not None:
            result[style_id] = outline
    return result


def _image_relations(
    element: ET.Element,
    relationships: Mapping[str, str],
    anchor: SourceAnchor,
) -> list[ImageRelation]:
    result: list[ImageRelation] = []
    seen: set[str] = set()
    for descendant in element.iter():
        kind = _local_name(descendant.tag)
        relationship_id = None
        if kind == "blip":
            relationship_id = _attribute(descendant, "embed") or _attribute(descendant, "link")
        elif kind == "imagedata":
            relationship_id = _attribute(descendant, "id")
        if not relationship_id or relationship_id in seen:
            continue
        target = relationships.get(relationship_id)
        if target is None:
            continue
        seen.add(relationship_id)
        name = ""
        description = ""
        for candidate in element.iter():
            if _local_name(candidate.tag) == "docPr":
                name = candidate.attrib.get("name", "")
                description = candidate.attrib.get("descr", "")
                break
        result.append(
            ImageRelation(
                relationship_id=relationship_id,
                target=target,
                source=anchor,
                name=name,
                description=description,
            )
        )
    return result


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
