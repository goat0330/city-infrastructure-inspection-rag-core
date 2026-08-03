"""Word-first summary and scoring extraction."""

from .extractor import (
    CONFLICTING_CANDIDATES,
    MISSING_VALUE,
    SummaryCandidate,
    SummaryExtraction,
    extract_summary,
)

__all__ = [
    "CONFLICTING_CANDIDATES",
    "MISSING_VALUE",
    "SummaryCandidate",
    "SummaryExtraction",
    "extract_summary",
]
