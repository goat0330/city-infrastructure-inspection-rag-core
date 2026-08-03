"""Deterministic extraction of maintenance recommendations from Word models."""

from .extractor import (
    RECOMMENDATION_CATEGORIES,
    RecommendationExtractionResult,
    extract_recommendations,
)

__all__ = [
    "RECOMMENDATION_CATEGORIES",
    "RecommendationExtractionResult",
    "extract_recommendations",
]
