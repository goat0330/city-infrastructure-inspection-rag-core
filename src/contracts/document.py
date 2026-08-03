from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .evidence import SourceAnchor


@dataclass(frozen=True)
class DocumentBlock:
    block_index: int
    raw_text: str
    source: SourceAnchor


@dataclass(frozen=True)
class ParagraphBlock(DocumentBlock):
    heading_level: int | None = None
    style_id: str | None = None


@dataclass(frozen=True)
class TableCell:
    row_index: int
    column_index: int
    raw_text: str
    row_span: int = 1
    column_span: int = 1
    source: SourceAnchor | None = None
    is_merge_continuation: bool = False
    merge_origin_row: int | None = None
    merge_origin_column: int | None = None


@dataclass(frozen=True)
class TableRow:
    row_index: int
    cells: tuple[TableCell, ...]


@dataclass(frozen=True)
class TableBlock(DocumentBlock):
    table_index: int = 0
    rows: tuple[TableRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ImageRelation:
    """An image relationship referenced from the main document body."""

    relationship_id: str
    target: str
    source: SourceAnchor
    name: str = ""
    description: str = ""


@dataclass(frozen=True)
class DocumentModel:
    source_file: str
    blocks: tuple[DocumentBlock, ...]
    images: tuple[ImageRelation, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
