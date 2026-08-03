"""Stable contracts shared by the Word-first pipeline."""

from .document import DocumentBlock, DocumentModel, ParagraphBlock, TableBlock, TableCell, TableRow
from .evidence import SourceAnchor
from .prediction import BridgeSummary, DefectObservation, InspectionPrediction, Recommendation
from .status import RunStatus, StageStatus

__all__ = [
    "DocumentBlock",
    "DocumentModel",
    "ParagraphBlock",
    "TableBlock",
    "TableCell",
    "TableRow",
    "InspectionPrediction",
    "BridgeSummary",
    "DefectObservation",
    "Recommendation",
    "RunStatus",
    "SourceAnchor",
    "StageStatus",
]
