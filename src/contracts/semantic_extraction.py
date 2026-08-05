"""Frozen contracts for optional LLM-assisted semantic extraction.

The deterministic Word-first pipeline remains the authority for explicit
fields, numbers, dates, scores, grades, defect descriptions, and recommendation
text.  This module only defines the narrow hand-off between that pipeline and
an optional semantic decision layer.

The contract is intentionally dependency-free: it uses standard-library
``dataclass`` and ``TypedDict`` types so both the structured worktree and the
RAG/LangGraph worktree can import it without pulling in model or graph runtime
packages.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping, TypeAlias, TypedDict


SemanticTaskType = Literal[
    "defect_row_validation",
    "recommendation_category",
    "conclusion_evidence_selection",
    "risk_evidence_selection",
]

SEMANTIC_TASK_TYPES: tuple[SemanticTaskType, ...] = (
    "defect_row_validation",
    "recommendation_category",
    "conclusion_evidence_selection",
    "risk_evidence_selection",
)

DecisionStatus = Literal["resolved", "unresolved"]
RecommendationCategory = Literal["立即处置", "尽快维修", "预防性养护"]

RECOMMENDATION_CATEGORIES: tuple[RecommendationCategory, ...] = (
    "立即处置",
    "尽快维修",
    "预防性养护",
)

# A failed or unresolved semantic decision must fall back only to these
# deterministic prediction fields.  The mapping is part of the frozen
# worktree integration contract.
FALLBACK_FIELDS_BY_TASK: dict[SemanticTaskType, tuple[str, ...]] = {
    "defect_row_validation": ("defects",),
    "recommendation_category": (
        "recommendations",
        "summary.recommendations_summary",
    ),
    "conclusion_evidence_selection": ("summary.overall_conclusion",),
    "risk_evidence_selection": ("summary.risk_points",),
}

OUTPUT_FIELDS_BY_TASK: dict[SemanticTaskType, tuple[str, ...]] = {
    "defect_row_validation": (
        "is_defect",
        "normalized_location",
        "normalized_defect_type",
        "duplicate_of_candidate_id",
    ),
    "recommendation_category": ("category",),
    "conclusion_evidence_selection": (
        "selected_evidence_ids",
        "selected_text",
    ),
    "risk_evidence_selection": (
        "selected_evidence_ids",
        "selected_text",
    ),
}


def _require_non_empty(value: str, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalise_string_tuple(values: object, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field_name} must be a sequence of strings")
    try:
        items = tuple(str(value).strip() for value in values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{field_name} must be a sequence of strings") from exc
    if any(not item for item in items):
        raise ValueError(f"{field_name} must not contain empty identifiers")
    return tuple(dict.fromkeys(items))


def _normalise_mapping(value: object, field_name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _normalise_task_type(value: object) -> SemanticTaskType:
    task_type = str(value)
    if task_type not in SEMANTIC_TASK_TYPES:
        raise ValueError(
            f"task_type must be one of {', '.join(SEMANTIC_TASK_TYPES)}"
        )
    return task_type  # type: ignore[return-value]


def fallback_fields_for(task_type: SemanticTaskType | str) -> tuple[str, ...]:
    """Return the deterministic fields used when a task is unresolved."""

    return FALLBACK_FIELDS_BY_TASK[_normalise_task_type(task_type)]


@dataclass(frozen=True)
class ExtractionCandidate:
    """One deterministic ambiguity submitted to the semantic decision layer.

    ``source_text`` is always the original report text and must never be
    rewritten by the semantic layer.  Layout-specific details such as table
    headers, row indices, section names, and neighbouring records belong in
    ``context``.  ``rule_output`` contains the deterministic extractor's
    current interpretation, not a second copy of the source document.
    """

    candidate_id: str
    sample_id: str
    task_type: SemanticTaskType
    source_text: str
    evidence_ids: tuple[str, ...]
    context_before: str = ""
    context_after: str = ""
    rule_output: Mapping[str, object] = field(default_factory=dict)
    facility_context: Mapping[str, object] = field(default_factory=dict)
    context: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _require_non_empty(self.candidate_id, "candidate_id")
        )
        object.__setattr__(
            self, "sample_id", _require_non_empty(self.sample_id, "sample_id")
        )
        object.__setattr__(self, "task_type", _normalise_task_type(self.task_type))
        object.__setattr__(self, "source_text", str(self.source_text))
        object.__setattr__(
            self,
            "evidence_ids",
            _normalise_string_tuple(self.evidence_ids, "evidence_ids"),
        )
        if not self.evidence_ids:
            raise ValueError("evidence_ids must contain at least one source identifier")
        object.__setattr__(self, "context_before", str(self.context_before))
        object.__setattr__(self, "context_after", str(self.context_after))
        object.__setattr__(
            self, "rule_output", _normalise_mapping(self.rule_output, "rule_output")
        )
        object.__setattr__(
            self,
            "facility_context",
            _normalise_mapping(self.facility_context, "facility_context"),
        )
        object.__setattr__(self, "context", _normalise_mapping(self.context, "context"))

    @property
    def fallback_fields(self) -> tuple[str, ...]:
        return fallback_fields_for(self.task_type)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "sample_id": self.sample_id,
            "task_type": self.task_type,
            "source_text": self.source_text,
            "evidence_ids": list(self.evidence_ids),
            "context_before": self.context_before,
            "context_after": self.context_after,
            "rule_output": dict(self.rule_output),
            "facility_context": dict(self.facility_context),
            "context": dict(self.context),
            "fallback_fields": list(self.fallback_fields),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ExtractionCandidate":
        return cls(
            candidate_id=str(value.get("candidate_id", "")),
            sample_id=str(value.get("sample_id", "")),
            task_type=_normalise_task_type(value.get("task_type", "")),
            source_text=str(value.get("source_text", "")),
            evidence_ids=_normalise_string_tuple(
                value.get("evidence_ids", ()), "evidence_ids"
            ),
            context_before=str(value.get("context_before", "")),
            context_after=str(value.get("context_after", "")),
            rule_output=_normalise_mapping(
                value.get("rule_output", {}), "rule_output"
            ),
            facility_context=_normalise_mapping(
                value.get("facility_context", {}), "facility_context"
            ),
            context=_normalise_mapping(value.get("context", {}), "context"),
        )


@dataclass(frozen=True)
class DefectRowValidationResult:
    """Allowed output for ``defect_row_validation``.

    The source description, quantities, dimensions, figure references, and
    dates are deliberately absent: the semantic layer is not allowed to alter
    them.  ``duplicate_of_candidate_id`` may identify another candidate row;
    final record merging remains the structured pipeline's responsibility.
    """

    is_defect: bool
    normalized_location: str = ""
    normalized_defect_type: str = ""
    duplicate_of_candidate_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.is_defect, bool):
            raise TypeError("is_defect must be a bool")
        object.__setattr__(self, "normalized_location", str(self.normalized_location).strip())
        object.__setattr__(
            self, "normalized_defect_type", str(self.normalized_defect_type).strip()
        )
        duplicate = self.duplicate_of_candidate_id
        if duplicate is not None:
            duplicate = _require_non_empty(duplicate, "duplicate_of_candidate_id")
        object.__setattr__(self, "duplicate_of_candidate_id", duplicate)
        if not self.is_defect and (
            self.normalized_location
            or self.normalized_defect_type
            or self.duplicate_of_candidate_id is not None
        ):
            raise ValueError(
                "non-defect decisions must not emit normalized defect fields"
            )


@dataclass(frozen=True)
class RecommendationCategoryResult:
    """Allowed output for ``recommendation_category``."""

    category: RecommendationCategory

    def __post_init__(self) -> None:
        if self.category not in RECOMMENDATION_CATEGORIES:
            raise ValueError(
                f"category must be one of {', '.join(RECOMMENDATION_CATEGORIES)}"
            )


@dataclass(frozen=True)
class EvidenceSelectionResult:
    """Allowed output for conclusion and risk evidence-selection tasks."""

    selected_evidence_ids: tuple[str, ...]
    selected_text: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "selected_evidence_ids",
            _normalise_string_tuple(
                self.selected_evidence_ids, "selected_evidence_ids"
            ),
        )
        if not self.selected_evidence_ids:
            raise ValueError("selected_evidence_ids must contain at least one identifier")
        object.__setattr__(
            self, "selected_text", _require_non_empty(self.selected_text, "selected_text")
        )


SemanticDecisionResult: TypeAlias = (
    DefectRowValidationResult
    | RecommendationCategoryResult
    | EvidenceSelectionResult
)


@dataclass(frozen=True)
class SemanticDecision:
    """Validated semantic output for exactly one :class:`ExtractionCandidate`.

    ``decision='resolved'`` requires a task-specific ``result``.  An
    ``unresolved`` decision must not contain a result and causes only the
    fields returned by :func:`fallback_fields_for` to retain their
    deterministic baseline values.
    """

    candidate_id: str
    task_type: SemanticTaskType
    decision: DecisionStatus
    evidence_ids: tuple[str, ...]
    confidence: float
    selection_reason: str
    result: SemanticDecisionResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _require_non_empty(self.candidate_id, "candidate_id")
        )
        object.__setattr__(self, "task_type", _normalise_task_type(self.task_type))
        if self.decision not in ("resolved", "unresolved"):
            raise ValueError("decision must be 'resolved' or 'unresolved'")
        object.__setattr__(
            self,
            "evidence_ids",
            _normalise_string_tuple(self.evidence_ids, "evidence_ids"),
        )
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise TypeError("confidence must be a number between 0 and 1")
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "selection_reason", str(self.selection_reason).strip())

        if self.decision == "unresolved":
            if self.result is not None:
                raise ValueError("unresolved decisions must not contain a result")
            return

        if self.result is None:
            raise ValueError("resolved decisions must contain a result")
        if not self.evidence_ids:
            raise ValueError("resolved decisions must contain evidence_ids")
        if not self.selection_reason:
            raise ValueError("resolved decisions must contain selection_reason")
        self._validate_result_type()
        if isinstance(self.result, EvidenceSelectionResult):
            missing = set(self.result.selected_evidence_ids) - set(self.evidence_ids)
            if missing:
                raise ValueError(
                    "selected_evidence_ids must be included in decision evidence_ids"
                )

    def _validate_result_type(self) -> None:
        expected: type[SemanticDecisionResult]
        if self.task_type == "defect_row_validation":
            expected = DefectRowValidationResult
        elif self.task_type == "recommendation_category":
            expected = RecommendationCategoryResult
        else:
            expected = EvidenceSelectionResult
        if not isinstance(self.result, expected):
            raise TypeError(
                f"{self.task_type} requires result type {expected.__name__}"
            )

    @property
    def fallback_fields(self) -> tuple[str, ...]:
        return fallback_fields_for(self.task_type)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] | None
        if self.result is None:
            result = None
        else:
            result = asdict(self.result)
            if "selected_evidence_ids" in result:
                result["selected_evidence_ids"] = list(
                    result["selected_evidence_ids"]  # type: ignore[arg-type]
                )
        return {
            "candidate_id": self.candidate_id,
            "task_type": self.task_type,
            "decision": self.decision,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "selection_reason": self.selection_reason,
            "result": result,
            "fallback_fields": list(self.fallback_fields),
        }

    @classmethod
    def unresolved(
        cls,
        *,
        candidate_id: str,
        task_type: SemanticTaskType,
        selection_reason: str = "",
        confidence: float = 0.0,
        evidence_ids: tuple[str, ...] = (),
    ) -> "SemanticDecision":
        return cls(
            candidate_id=candidate_id,
            task_type=task_type,
            decision="unresolved",
            evidence_ids=evidence_ids,
            confidence=confidence,
            selection_reason=selection_reason,
            result=None,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SemanticDecision":
        task_type = _normalise_task_type(value.get("task_type", ""))
        decision = str(value.get("decision", ""))
        raw_result = value.get("result")
        result: SemanticDecisionResult | None = None
        if raw_result is not None:
            if not isinstance(raw_result, Mapping):
                raise TypeError("result must be a mapping or null")
            if task_type == "defect_row_validation":
                is_defect = raw_result.get("is_defect")
                if not isinstance(is_defect, bool):
                    raise TypeError("result.is_defect must be a bool")
                duplicate = raw_result.get("duplicate_of_candidate_id")
                result = DefectRowValidationResult(
                    is_defect=is_defect,
                    normalized_location=str(
                        raw_result.get("normalized_location", "")
                    ),
                    normalized_defect_type=str(
                        raw_result.get("normalized_defect_type", "")
                    ),
                    duplicate_of_candidate_id=(
                        None if duplicate is None else str(duplicate)
                    ),
                )
            elif task_type == "recommendation_category":
                result = RecommendationCategoryResult(
                    category=str(raw_result.get("category", ""))  # type: ignore[arg-type]
                )
            else:
                result = EvidenceSelectionResult(
                    selected_evidence_ids=_normalise_string_tuple(
                        raw_result.get("selected_evidence_ids", ()),
                        "result.selected_evidence_ids",
                    ),
                    selected_text=str(raw_result.get("selected_text", "")),
                )
        return cls(
            candidate_id=str(value.get("candidate_id", "")),
            task_type=task_type,
            decision=decision,  # type: ignore[arg-type]
            evidence_ids=_normalise_string_tuple(
                value.get("evidence_ids", ()), "evidence_ids"
            ),
            confidence=value.get("confidence", 0.0),  # type: ignore[arg-type]
            selection_reason=str(value.get("selection_reason", "")),
            result=result,
        )


def validate_decision_for_candidate(
    candidate: ExtractionCandidate,
    decision: SemanticDecision,
    *,
    available_evidence_ids: object = (),
) -> None:
    """Validate the cross-worktree join before applying a decision.

    ``available_evidence_ids`` contains additional report, knowledge-card, or
    label-example identifiers returned by retrieval.  The candidate's own
    source evidence is always allowed.  This helper validates only the shared
    contract; task-specific factual checks remain in the semantic graph and
    deterministic merge adapter.
    """

    if decision.candidate_id != candidate.candidate_id:
        raise ValueError("decision candidate_id does not match candidate")
    if decision.task_type != candidate.task_type:
        raise ValueError("decision task_type does not match candidate")
    allowed = set(candidate.evidence_ids)
    allowed.update(
        _normalise_string_tuple(available_evidence_ids, "available_evidence_ids")
    )
    unknown = set(decision.evidence_ids) - allowed
    if unknown:
        raise ValueError("decision contains evidence_ids outside available evidence")


class SemanticExtractionGraphState(TypedDict, total=False):
    """Serializable state contract for the minimal semantic LangGraph.

    Runtime nodes should exchange JSON-compatible dictionaries produced by
    ``to_dict`` rather than relying on graph-runtime serialization of
    dataclass instances.
    """

    sample_id: str
    baseline_prediction: dict[str, object]
    facility_context: dict[str, object]
    field_states: dict[str, object]
    candidates: list[dict[str, object]]
    retrieval_by_candidate: dict[str, list[dict[str, object]]]
    decisions: list[dict[str, object]]
    validation_errors: list[dict[str, object]]
    fallback_fields: list[str]
    merged_prediction: dict[str, object]
    completed_candidate_ids: list[str]


__all__ = [
    "DecisionStatus",
    "DefectRowValidationResult",
    "EvidenceSelectionResult",
    "ExtractionCandidate",
    "FALLBACK_FIELDS_BY_TASK",
    "OUTPUT_FIELDS_BY_TASK",
    "RECOMMENDATION_CATEGORIES",
    "RecommendationCategory",
    "RecommendationCategoryResult",
    "SEMANTIC_TASK_TYPES",
    "SemanticDecision",
    "SemanticDecisionResult",
    "SemanticExtractionGraphState",
    "SemanticTaskType",
    "fallback_fields_for",
    "validate_decision_for_candidate",
]
