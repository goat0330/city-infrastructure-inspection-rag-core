"""Deterministic routing of parsed Word blocks into report sections."""

from .section_router import (
    SectionCategory,
    SectionRoute,
    route_sections,
)

__all__ = [
    "SectionCategory",
    "SectionRoute",
    "route_sections",
]
