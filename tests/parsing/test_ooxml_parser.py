from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.contracts import ParagraphBlock, TableBlock  # noqa: E402
from src.parsing import parse_docx  # noqa: E402
from tests.fixtures.word.ooxml_factory import (  # noqa: E402
    cell,
    paragraph,
    row,
    table,
    write_docx,
)


def _fixture(path: Path) -> Path:
    word_table = table(
        row(cell("A", grid_span=2), cell("B")),
        row(cell("V", vmerge="restart"), cell("R1")),
        row(cell("", vmerge="continue"), cell("R2")),
        row(cell("C"), cell("R3")),
    )
    return write_docx(path, paragraph("before"), word_table, paragraph("after"))


def test_preserves_body_order_merges_and_source_anchors(tmp_path: Path) -> None:
    model = parse_docx(_fixture(tmp_path / "generated.docx"), source_file="fixture.docx")

    assert [type(block) for block in model.blocks] == [
        ParagraphBlock,
        TableBlock,
        ParagraphBlock,
    ]
    assert [block.block_index for block in model.blocks] == [0, 1, 2]
    assert [block.raw_text for block in model.blocks] == ["before", "A\tB\nV\tR1\n\tR2\nC\tR3", "after"]

    before, parsed_table, after = model.blocks
    assert before.source.paragraph_index == 0
    assert after.source.paragraph_index == 1
    assert parsed_table.source.table_index == 0

    assert [(cell.column_index, cell.column_span) for cell in parsed_table.rows[0].cells] == [
        (0, 2),
        (2, 1),
    ]
    assert [(cell.row_index, cell.column_index, cell.raw_text) for cell in parsed_table.rows[1].cells] == [
        (1, 0, "V"),
        (1, 1, "R1"),
    ]
    assert parsed_table.rows[1].cells[0].row_span == 2
    continuation = parsed_table.rows[2].cells[0]
    assert continuation.raw_text == ""
    assert continuation.is_merge_continuation is True
    assert continuation.merge_origin_row == 1
    assert continuation.merge_origin_column == 0
    assert continuation.row_index == 2
    assert continuation.column_index == 0
    assert continuation.source is not None
    assert continuation.source.table_index == 0
    assert continuation.source.row_index == 2
    assert continuation.source.column_index == 0
    assert continuation.source.raw_text == ""
    assert [cell.raw_text for cell in parsed_table.rows[1].cells[1:]] == ["R1"]
    assert [cell.raw_text for cell in parsed_table.rows[2].cells[1:]] == ["R2"]
    assert [cell.raw_text for cell in parsed_table.rows[3].cells[1:]] == ["R3"]


def test_repeated_parsing_is_stable(tmp_path: Path) -> None:
    path = _fixture(tmp_path / "generated.docx")

    first = parse_docx(path, source_file="fixture.docx")
    second = parse_docx(path, source_file="fixture.docx")

    assert first == second
    assert first.to_dict() == second.to_dict()


def test_heading_images_and_visible_text_only() -> None:
    from tests.fixtures.word.ooxml_factory import document_xml
    from src.parsing import parse_document_xml

    xml = document_xml(
        '<w:p><w:pPr><w:pStyle w:val="HeadingX"/></w:pPr>'
        '<w:r><w:t>当前</w:t></w:r>'
        '<w:del><w:r><w:delText>删除</w:delText></w:r></w:del>'
        '<w:r><w:instrText>PAGE</w:instrText></w:r>'
        '<w:r><w:drawing><a:blip xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="rId9"/>'
        '</w:drawing></w:r></w:p>'
    )
    model = parse_document_xml(
        xml,
        source_file="fixture.docx",
        relationships={"rId9": "media/image1.png"},
        heading_styles={"HeadingX": 2},
    )
    paragraph_block = model.blocks[0]
    assert paragraph_block.raw_text == "当前"
    assert paragraph_block.heading_level == 2
    assert paragraph_block.style_id == "HeadingX"
    assert len(model.images) == 1
    assert model.images[0].relationship_id == "rId9"
    assert model.images[0].target == "media/image1.png"
