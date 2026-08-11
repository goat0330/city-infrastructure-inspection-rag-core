"""Deterministic extraction of maintenance recommendations from Word models."""

from .location_mapper import (
    deterministic_recommendation_location,
    is_suspect_recommendation_location,
    is_valid_recommendation_location,
    legal_recommendation_locations,
    map_recommendation_locations,
)

from .extractor import (
    RECOMMENDATION_CATEGORIES,
    RecommendationExtractionResult,
    extract_recommendations,
)

__all__ = [
    "RECOMMENDATION_CATEGORIES",
    "RecommendationExtractionResult",
    "extract_recommendations",
    "deterministic_recommendation_location",
    "is_suspect_recommendation_location",
    "is_valid_recommendation_location",
    "legal_recommendation_locations",
    "map_recommendation_locations",
]
