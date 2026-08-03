"""Native WordprocessingML parsing helpers."""

from .ooxml_parser import parse_docx, parse_document_xml

__all__ = ["parse_docx", "parse_document_xml"]
