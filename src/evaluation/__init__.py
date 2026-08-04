"""Deterministic scoring utilities for structured inspection predictions."""

from .alignment import AlignmentError, align_prediction_records
from .scorer import (
    DEFAULT_WEIGHTS,
    load_records,
    load_weights,
    normalize_text,
    score_dataset,
    score_record,
)

__all__ = [
    "AlignmentError",
    "DEFAULT_WEIGHTS",
    "align_prediction_records",
    "load_records",
    "load_weights",
    "normalize_text",
    "score_dataset",
    "score_record",
]
