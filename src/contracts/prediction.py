from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .evidence import SourceAnchor


@dataclass(frozen=True)
class BridgeSummary:
    bridge_name: str = ""
    report_date: str = ""
    overall_score: str = ""
    overall_grade: str = ""
    superstructure_score: str = ""
    superstructure_grade: str = ""
    substructure_score: str = ""
    substructure_grade: str = ""
    deck_score: str = ""
    deck_grade: str = ""
    previous_overall_score: str = ""
    previous_overall_grade: str = ""


@dataclass(frozen=True)
class Recommendation:
    category: str = ""
    content: str = ""
    location: str = ""
    evidence: tuple[SourceAnchor, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DefectObservation:
    location: str = ""
    defect_type: str = ""
    description: str = ""
    is_new: str = ""
    previous_status: str = ""
    development: str = ""
    evidence: tuple[SourceAnchor, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InspectionPrediction:
    summary: BridgeSummary = field(default_factory=BridgeSummary)
    detailed_conclusion: tuple[str, ...] = field(default_factory=tuple)
    recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
    defects: tuple[DefectObservation, ...] = field(default_factory=tuple)
    causes: tuple[str, ...] = field(default_factory=tuple)
    treatments: tuple[str, ...] = field(default_factory=tuple)
    safety_impact: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
