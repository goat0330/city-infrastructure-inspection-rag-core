from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .evidence import SourceAnchor


@dataclass(frozen=True)
class BridgeSummary:
    bridge_name: str = ""
    bridge_id: str = ""
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
    trend: str = ""
    overall_conclusion: str = ""
    risk_points: str = ""
    recommendations_summary: str = ""


@dataclass(frozen=True)
class Recommendation:
    index: str = ""
    category: str = ""
    content: str = ""
    location: str = ""
    evidence: tuple[SourceAnchor, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DefectObservation:
    index: str = ""
    location: str = ""
    defect_type: str = ""
    description: str = ""
    is_new: str = ""
    previous_status: str = ""
    development: str = ""
    evidence: tuple[SourceAnchor, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InspectionPrediction:
    sample_id: str = ""
    source_file: str = ""
    schema_version: str = "prediction-v1"
    summary: BridgeSummary = field(default_factory=BridgeSummary)
    detailed_conclusion: tuple[str, ...] = field(default_factory=tuple)
    recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
    defects: tuple[DefectObservation, ...] = field(default_factory=tuple)
    causes: tuple[str, ...] = field(default_factory=tuple)
    treatments: tuple[str, ...] = field(default_factory=tuple)
    safety_impact: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
