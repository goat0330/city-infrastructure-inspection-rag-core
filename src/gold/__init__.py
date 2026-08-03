"""Deterministic Gold JSON extraction from Word label documents."""

from .parser import LabelParseError, parse_label_docx

__all__ = ["LabelParseError", "parse_label_docx"]
