from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceAnchor:
    """Location evidence available from Word structure without inventing pages."""

    source_file: str
    block_index: int
    raw_text: str
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    paragraph_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
