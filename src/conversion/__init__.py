"""Minimal, resumable conversion of legacy Word documents."""

from .converter import BatchResult, convert_directory, find_soffice

__all__ = ["BatchResult", "convert_directory", "find_soffice"]
