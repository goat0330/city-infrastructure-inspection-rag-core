"""Deterministic routing of parsed Word blocks into report sections."""

from .section_router import (
    SectionCategory,
    SectionKind,
    SectionRoute,
    SectionRouter,
    SectionType,
    route_document,
    route_sections,
)

__all__ = [
    "SectionCategory",
    "SectionKind",
    "SectionRoute",
    "SectionRouter",
    "SectionType",
    "route_document",
    "route_sections",
]
