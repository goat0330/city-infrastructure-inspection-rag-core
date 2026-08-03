from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def cell(text: str = "", *, grid_span: int = 1, vmerge: str | None = None) -> str:
    properties = []
    if grid_span != 1:
        properties.append(f'<w:gridSpan w:val="{grid_span}"/>')
    if vmerge is not None:
        value = f' w:val="{vmerge}"' if vmerge else ""
        properties.append(f"<w:vMerge{value}/>")
    tc_pr = f"<w:tcPr>{''.join(properties)}</w:tcPr>" if properties else ""
    return f"<w:tc>{tc_pr}{paragraph(text)}</w:tc>"


def row(*cells: str) -> str:
    return f"<w:tr>{''.join(cells)}</w:tr>"


def table(*rows: str) -> str:
    return f"<w:tbl><w:tblGrid/>{''.join(rows)}</w:tbl>"


def document_xml(*body_children: str) -> bytes:
    body = "".join(body_children)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>{body}'
        "<w:sectPr/></w:body></w:document>"
    ).encode("utf-8")


def write_docx(path: Path, *body_children: str) -> Path:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as package:
        package.writestr("word/document.xml", document_xml(*body_children))
    return path
