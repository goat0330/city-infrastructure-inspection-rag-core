#!/usr/bin/env python3
"""Run one inspection report through the narrative A/B/C/D experiment.

This is deliberately a single-sample runner.  The deterministic extractor is
the source of the baseline; the model may replace only three narrative fields; deterministic treatments
remain unchanged.  No credential is written to an artifact.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from time import perf_counter
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.narrative import (  # noqa: E402
    _prompt_baseline,
    _task_queries as _facility_task_queries,
    run_narrative_enhancement,
)
from src.extraction.pipeline import extract_report  # noqa: E402
from src.llm.client import ModelCallResult, OpenAIModelClient  # noqa: E402
from src.parsing import parse_docx  # noqa: E402
from src.rag import LightRagIndex  # noqa: E402
from src.routing import route_sections  # noqa: E402


TARGET_FIELDS = ("detailed_conclusion", "causes", "safety_impact")
MODEL_RETRIEVAL_FIELDS = ("detailed_conclusion", "causes", "safety_impact")
RETRIEVAL_SOURCE_QUOTA = {
    "report_evidence": 3,
    "domain_knowledge": 2,
    "label_example": 1,
}
_FINAL_RETRIEVAL_QUOTA = {
    "report_evidence": 3,
    "knowledge_card": 2,
    "gold_label": 1,
}
_SECRET_RE = re.compile(r"(?i)(api[_ -]?key|authorization|bearer|token|secret)\s*[:=]\s*[^,;\s]+")


class ExperimentConfigurationError(RuntimeError):
    """Raised when a real-model run cannot be configured safely."""


def _safe_error(error: BaseException) -> str:
    message = " ".join(str(error).split())
    message = _SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", message)
    return message[:300]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain(to_dict())
    return str(value)


def _prediction_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "prediction"):
        value = value.prediction
    if isinstance(value, Mapping):
        return deepcopy(_plain(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _prediction_dict(to_dict())
    raise TypeError("baseline prediction must be a mapping or prediction object")


def _load_baseline(input_docx: Path, baseline_json: Path | None) -> dict[str, Any]:
    if baseline_json is None:
        extraction = extract_report(input_docx)
        baseline = _prediction_dict(extraction.prediction)
        # ReportExtraction carries facility metadata outside the public
        # InspectionPrediction contract. Keep it in the experiment state so
        # facility-aware retrieval and validation do not have to infer it from
        # a legacy field such as summary.bridge_name.
        baseline["facility_context"] = _plain(extraction.facility_context)
        baseline["field_states"] = _plain(extraction.field_states)
        return baseline
    return _prediction_dict(json.loads(baseline_json.read_text(encoding="utf-8")))


def _stable_evidence_id(source_file: str, block: Any) -> str:
    anchor = _plain(getattr(block, "source", None))
    anchor = anchor if isinstance(anchor, Mapping) else {}
    payload = {
        "source_file": source_file,
        "block_index": getattr(block, "block_index", anchor.get("block_index")),
        "table_index": anchor.get("table_index"),
        "row_index": anchor.get("row_index"),
        "column_index": anchor.get("column_index"),
        "paragraph_index": anchor.get("paragraph_index"),
        "raw_text": str(getattr(block, "raw_text", anchor.get("raw_text", ""))),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "docx:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _report_facts(input_docx: Path, source_file: str) -> list[dict[str, Any]]:
    document = parse_docx(input_docx, source_file=source_file)
    routes = route_sections(document)
    categories: dict[int, list[str]] = {}
    for route in routes:
        category = str(getattr(getattr(route, "category", ""), "value", getattr(route, "category", "")))
        for block in getattr(route, "blocks", ()):
            categories.setdefault(int(block.block_index), [])
            if category not in categories[int(block.block_index)]:
                categories[int(block.block_index)].append(category)

    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in document.blocks:
        text = str(getattr(block, "raw_text", "")).strip()
        if not text:
            continue
        evidence_id = _stable_evidence_id(source_file, block)
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        anchor = _plain(getattr(block, "source", None))
        facts.append(
            {
                "evidence_id": evidence_id,
                "text": text,
                "section": categories.get(int(block.block_index), ["unrouted"])[0],
                "source": anchor if isinstance(anchor, Mapping) else {},
            }
        )
    return facts


def _text_values(value: Any, _active: set[int] | None = None) -> list[str]:
    """Collect nested strings without looping through self-referential data."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, (bytes, bytearray)):
        return []
    active = _active if _active is not None else set()
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in active:
            return []
        active.add(marker)
        result: list[str] = []
        try:
            for item in value.values():
                result.extend(_text_values(item, active))
        finally:
            active.remove(marker)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        marker = id(value)
        if marker in active:
            return []
        active.add(marker)
        result: list[str] = []
        try:
            for item in value:
                result.extend(_text_values(item, active))
        finally:
            active.remove(marker)
        return result
    return []


def _evidence_ids(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in {"evidence_id", "evidence_ids"}:
                for identifier in _text_values(item):
                    if identifier not in result:
                        result.append(identifier)
            else:
                result.extend(identifier for identifier in _evidence_ids(item) if identifier not in result)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            result.extend(identifier for identifier in _evidence_ids(item) if identifier not in result)
    return result


def _text_evidence_pairs(
    value: Any,
    inherited_ids: Sequence[str] = (),
    _active: set[int] | None = None,
) -> list[tuple[str, list[str]]]:
    """Return narrative text together with evidence IDs inherited from its item."""

    if isinstance(value, str):
        return [(value, list(inherited_ids))]
    if isinstance(value, (bytes, bytearray)):
        return []
    active = _active if _active is not None else set()
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in active:
            return []
        active.add(marker)
        evidence_ids = list(inherited_ids)
        pairs: list[tuple[str, list[str]]] = []
        try:
            for key in ("evidence_id", "evidence_ids"):
                evidence_ids.extend(_text_values(value.get(key)))
            for key, item in value.items():
                if str(key) in {"evidence_id", "evidence_ids"}:
                    continue
                pairs.extend(_text_evidence_pairs(item, evidence_ids, active))
        finally:
            active.remove(marker)
        return pairs
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        marker = id(value)
        if marker in active:
            return []
        active.add(marker)
        pairs: list[tuple[str, list[str]]] = []
        try:
            for item in value:
                pairs.extend(_text_evidence_pairs(item, inherited_ids, active))
        finally:
            active.remove(marker)
        return pairs
    return []


def _normalise_text(text: Any) -> str:
    return "".join(str(text).split()).casefold()


def _normalised_fields(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, Mapping) else {}
    return {field: deepcopy(value.get(field, [])) for field in TARGET_FIELDS}


def _locked_top_level_differences(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    missing = object()
    differences: list[str] = []
    for key in sorted(set(candidate) | set(baseline)):
        if key in TARGET_FIELDS:
            continue
        if candidate.get(key, missing) != baseline.get(key, missing):
            differences.append(str(key))
    return differences


def _call_metrics(result: ModelCallResult | None, *, model: str, duration_ms: float, calls: int = 1) -> dict[str, Any]:
    return {
        "model": getattr(result, "model", model) if result is not None else model,
        "duration_ms": round(float(getattr(result, "duration_ms", duration_ms)), 3),
        "prompt_tokens": int(getattr(result, "prompt_tokens", 0) or 0) if result is not None else 0,
        "completion_tokens": int(getattr(result, "completion_tokens", 0) or 0) if result is not None else 0,
        "total_tokens": int(getattr(result, "total_tokens", 0) or 0) if result is not None else 0,
        "calls": calls,
        "token_usage_known": result is not None and getattr(result, "total_tokens", None) is not None,
    }


class TrackingClient:
    """Small recording wrapper; it does not store request text or credentials."""

    def __init__(self, client: Any, *, default_chat_max_tokens: int = 2400) -> None:
        self.client = client
        self.default_chat_max_tokens = default_chat_max_tokens
        self.calls: list[dict[str, Any]] = []

    def _record(self, operation: str, result: ModelCallResult) -> ModelCallResult:
        self.calls.append(
            {
                "operation": operation,
                "model": result.model,
                "duration_ms": result.duration_ms,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
            }
        )
        return result

    def chat_json(self, *args: Any, **kwargs: Any) -> ModelCallResult:
        kwargs.setdefault("max_tokens", self.default_chat_max_tokens)
        return self._record("chat_json", self.client.chat_json(*args, **kwargs))

    def embed_texts(self, *args: Any, **kwargs: Any) -> ModelCallResult:
        return self._record("embed_texts", self.client.embed_texts(*args, **kwargs))

    def rerank(self, *args: Any, **kwargs: Any) -> ModelCallResult:
        return self._record("rerank", self.client.rerank(*args, **kwargs))


class StaticRetriever:
    """Replay precomputed retrieval without erasing task boundaries.

    The batch runners retrieve each narrative task independently before the
    LangGraph starts.  Earlier versions merged those hits and replayed the same
    evidence for every task, which weakened causes/safety grounding.  This
    adapter accepts either the legacy flat hit list or a ``task -> hits`` map.
    """

    _TASK_RE = re.compile(r"(?:^|;\s*)task=([^;]+)")

    def __init__(
        self,
        hits: Sequence[Mapping[str, Any]] | Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> None:
        if isinstance(hits, Mapping):
            self.hits_by_task = {
                str(task): [dict(hit) for hit in values if isinstance(hit, Mapping)]
                for task, values in hits.items()
                if isinstance(values, Sequence)
                and not isinstance(values, (str, bytes, bytearray))
            }
            self.hits: list[dict[str, Any]] = []
        else:
            self.hits_by_task = {}
            self.hits = [dict(hit) for hit in hits if isinstance(hit, Mapping)]

    def retrieve(self, query: str = "", **_kwargs: Any) -> list[dict[str, Any]]:
        if self.hits_by_task:
            match = self._TASK_RE.search(str(query or ""))
            task = match.group(1).strip() if match else ""
            return deepcopy(self.hits_by_task.get(task, []))
        return deepcopy(self.hits)


class OfflineClient:
    model = "offline-fake"

    def __init__(self, evidence_id: str = "") -> None:
        self.evidence_id = evidence_id

    def _result(self, group: str) -> ModelCallResult:
        evidence = [self.evidence_id] if self.evidence_id else []
        return ModelCallResult(
            value={
                "detailed_conclusion": [
                    f"经综合评定，offline {group} narrative。",
                    "本次报告未提供往年检测评分及病害对比数据。",
                    "目前，报告所述病害需要持续关注。",
                    "综上，应依据既有建议开展处置。",
                ],
                "causes": [{"text": f"offline {group} cause", "evidence_ids": evidence}],
                "treatments": [],
                "safety_impact": [{"text": f"offline {group} safety", "evidence_ids": evidence}],
            },
            model=self.model,
            duration_ms=0.1,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        )

    def chat_json(self, messages: Sequence[Mapping[str, Any]], **_kwargs: Any) -> ModelCallResult:
        text = str(messages[-1].get("content", "")) if messages else ""
        group = "LLM"
        for candidate in ("B", "C", "D"):
            if f'"group": "{candidate}"' in text:
                group = candidate
        return self._result(group)


_PROMPT_CONTEXT_MAX_ITEMS = 12
_PROMPT_CONTEXT_MAX_CHARS = 6000
_PROMPT_CONTEXT_ITEM_CHARS = 720
_PROMPT_CONTEXT_KEYS = (
    "evidence_id",
    "id",
    "kind",
    "source_bucket",
    "source_type",
    "section",
    "sample_id",
    "split",
    "score",
    "embedding_score",
    "rerank_score",
    "retrieval_mode",
    "title",
)
_PROMPT_SOURCE_KEYS = (
    "block_index",
    "table_index",
    "row_index",
    "column_index",
    "paragraph_index",
)


def _compact_prompt_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.7))
    tail = max(1, limit - head - 1)
    return text[:head] + "…" + text[-tail:]


def _compact_context_records(
    records: Sequence[Mapping[str, Any]],
    *,
    max_items: int = _PROMPT_CONTEXT_MAX_ITEMS,
    max_chars: int = _PROMPT_CONTEXT_MAX_CHARS,
    max_item_chars: int = _PROMPT_CONTEXT_ITEM_CHARS,
) -> list[dict[str, Any]]:
    """Keep evidence anchors while removing repeated long source text from prompts."""

    result: list[dict[str, Any]] = []
    used_chars = 0
    for record in records:
        if not isinstance(record, Mapping) or len(result) >= max_items:
            break
        raw_text = record.get("text", record.get("content", record.get("snippet", "")))
        remaining = max_chars - used_chars
        if remaining <= 0 or not str(raw_text or "").strip():
            continue
        item: dict[str, Any] = {
            key: deepcopy(record[key])
            for key in _PROMPT_CONTEXT_KEYS
            if key in record and record[key] is not None
        }
        source = record.get("source")
        if isinstance(source, Mapping):
            compact_source = {
                key: deepcopy(source[key])
                for key in _PROMPT_SOURCE_KEYS
                if key in source and source[key] is not None
            }
            if compact_source:
                item["source"] = compact_source
        item["text"] = _compact_prompt_text(raw_text, min(max_item_chars, remaining))
        result.append(item)
        used_chars += len(item["text"])
    return result


def _prompt(group: str, baseline: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    payload = {
        "group": group,
        "task": "Enhance only detailed_conclusion, causes and safety_impact; keep treatments from baseline.",
        "baseline_prediction": _normalised_fields(baseline),
        "report_facts": _compact_context_records(facts),
        "contract": {
            "detailed_conclusion": "array of at most four concise strings",
            "causes": "array of concise objects with text and evidence_ids",
            "treatments": "do not generate; deterministic baseline is retained",
            "safety_impact": "array of concise objects with text and evidence_ids",
            "brevity": "do not repeat the report; keep each item under 100 Chinese characters",
        },
    }
    return [
        {"role": "system", "content": "Return only valid JSON. Do not change locked baseline fields. Be concise: at most four conclusion paragraphs and short evidence-grounded items."},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _run_chat_group(group: str, client: TrackingClient, baseline: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ModelCallResult]:
    result = client.chat_json(_prompt(group, baseline, facts), max_tokens=4096)
    value = result.value if isinstance(result.value, Mapping) else {}
    return _normalised_fields(value), result


def _query(
    baseline: Mapping[str, Any],
    facility_context: Any = None,
    report_facts: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    return _task_queries(baseline, facility_context, report_facts)["detailed_conclusion"]


def _task_queries(
    baseline: Mapping[str, Any],
    facility_context: Any = None,
    report_facts: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    """Build separate facility-aware retrieval context for each narrative field."""

    return _facility_task_queries(baseline, facility_context, report_facts)


def _retrieval_source_bucket(hit: Mapping[str, Any]) -> str | None:
    values: list[str] = []
    for key in ("source_type", "source_kind", "kind", "source", "type"):
        value = hit.get(key)
        if isinstance(value, Mapping):
            values.extend(str(value.get(name, "")) for name in ("source_type", "source_kind", "kind", "type", "name"))
        elif value is not None:
            values.append(str(value))
    text = " ".join(values).casefold().replace("-", "_").replace(" ", "_")
    if any(alias in text for alias in ("gold_label", "label_example", "gold", "label")):
        return "gold_label"
    if any(alias in text for alias in ("knowledge_card", "domain_knowledge", "knowledge")):
        return "knowledge_card"
    if any(alias in text for alias in ("report_evidence", "report_fact", "current_report", "evidence")):
        return "report_evidence"
    return None


def _public_retrieval_source_bucket(hit: Mapping[str, Any]) -> str | None:
    """Expose the competition-facing names while preserving the raw ``kind``."""

    return {
        "report_evidence": "report_evidence",
        "knowledge_card": "domain_knowledge",
        "gold_label": "label_example",
    }.get(_retrieval_source_bucket(hit))


def _retrieval_hit_key(hit: Mapping[str, Any]) -> str:
    for key in ("evidence_id", "id"):
        value = hit.get(key)
        if value is not None and str(value):
            return f"{key}:{value}"
    return json.dumps(
        {key: hit.get(key) for key in ("kind", "source", "text")},
        ensure_ascii=False,
        sort_keys=True,
    )


def _merge_retrieval_hits(task_hits: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Deduplicate task results and enforce the final D-group source quotas."""

    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for task in MODEL_RETRIEVAL_FIELDS:
        for raw_hit in task_hits.get(task, []):
            if not isinstance(raw_hit, Mapping):
                continue
            key = _retrieval_hit_key(raw_hit)
            if key in positions:
                continue
            hit = deepcopy(dict(raw_hit))
            positions[key] = len(merged)
            merged.append(hit)

    selected: list[dict[str, Any]] = []
    counts = {bucket: 0 for bucket in _FINAL_RETRIEVAL_QUOTA}
    for hit in merged:
        bucket = _retrieval_source_bucket(hit)
        if bucket not in _FINAL_RETRIEVAL_QUOTA:
            continue
        if counts[bucket] >= _FINAL_RETRIEVAL_QUOTA[bucket]:
            continue
        counts[bucket] += 1
        public_bucket = _public_retrieval_source_bucket(hit)
        if public_bucket:
            hit["source_bucket"] = public_bucket
        selected.append(hit)
    return selected


def _retrieve_task(
    index: Any,
    query: str,
    *,
    sample_id: str,
    split: str,
    facility_type: str | None = None,
) -> Sequence[Mapping[str, Any]]:
    """Call either the quota-aware RAG API or the current pre-quota API."""

    kwargs = {
        "sample_id": sample_id,
        "split": split,
        "top_embedding": 30,
        "top_rerank": 8,
        "top_k": sum(_FINAL_RETRIEVAL_QUOTA.values()),
    }
    if facility_type:
        kwargs["facility_type"] = facility_type
    try:
        return index.retrieve(query, **kwargs, source_quota=dict(RETRIEVAL_SOURCE_QUOTA))
    except TypeError as error:
        if "source_quota" not in str(error):
            raise
        return index.retrieve(query, **kwargs)


def _retrieve_task_hits(
    index: Any,
    task_queries: Mapping[str, str],
    *,
    sample_id: str,
    split: str,
    facility_type: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    task_hits: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for task in MODEL_RETRIEVAL_FIELDS:
        try:
            results = _retrieve_task(
                index,
                task_queries[task],
                sample_id=sample_id,
                split=split,
                facility_type=facility_type,
            )
            task_hits[task] = [dict(item) for item in (results or []) if isinstance(item, Mapping)]
        except Exception as error:
            task_hits[task] = []
            errors[task] = _safe_error(error)
    return task_hits, errors


def _select_context_facts(
    facts: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    *,
    max_items: int = 12,
    max_chars: int = 5200,
) -> list[dict[str, Any]]:
    """Select a small evidence set with reserved safety and defect coverage."""

    query = _query(baseline)
    terms = [term for term in re.findall(r"[\u4e00-\u9fffA-Za-z0-9+#.-]{2,}", query) if term]
    preferred = {
        "safety_assessment": 8,
        "inspection_conclusion": 6,
        "defect_table": 5,
        "treatment_recommendations": 1,
    }
    ranked: list[tuple[int, int, Mapping[str, Any]]] = []
    for order, fact in enumerate(facts):
        text = str(fact.get("text", ""))
        section = str(fact.get("section", ""))
        score = preferred.get(section, 0) + min(5, sum(1 for term in terms if term in text))
        ranked.append((score, -order, fact))
    ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))

    chosen: list[Mapping[str, Any]] = []
    # Reserve current-report safety and defect evidence before general ranking.
    for section, quota in (("safety_assessment", 2), ("inspection_conclusion", 2), ("defect_table", 4)):
        matches = [item[2] for item in ranked if str(item[2].get("section", "")) == section]
        chosen.extend(matches[:quota])
    for _, _, fact in ranked:
        if fact not in chosen and len(chosen) < max_items:
            chosen.append(fact)
    chosen = sorted(chosen, key=lambda item: list(facts).index(item))

    result: list[dict[str, Any]] = []
    used_chars = 0
    for fact in chosen:
        text = " ".join(str(fact.get("text", "")).split())
        if not text:
            continue
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        compact = dict(fact)
        compact["text"] = _compact_prompt_text(text, min(520, remaining))
        result.append(compact)
        used_chars += len(compact["text"])
    return result


def _group_record(
    group: str,
    label: str,
    fields: Mapping[str, Any],
    baseline: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    retrieval: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    *,
    available: bool = True,
    used_fallback: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    available_ids = {
        str(item.get(key))
        for item in list(facts) + list(retrieval)
        for key in ("evidence_id", "id")
        if isinstance(item, Mapping) and item.get(key)
    }
    evidence_ids = _evidence_ids(fields)
    invalid = [identifier for identifier in evidence_ids if identifier not in available_ids]
    source_texts = {
        _normalise_text(text)
        for field in TARGET_FIELDS
        for text in _text_values(baseline.get(field))
    }
    source_texts.update(
        _normalise_text(text)
        for fact in list(facts) + list(retrieval)
        for text in _text_values(fact.get("text"))
    )
    source_texts.discard("")
    new_facts = []
    for text, item_evidence_ids in _text_evidence_pairs(fields):
        normalized = _normalise_text(text)
        lexical_match = normalized in source_texts or any(
            len(normalized) >= 8 and (normalized in source or source in normalized)
            for source in source_texts
        )
        evidence_match = bool(item_evidence_ids) and all(
            str(identifier) in available_ids for identifier in item_evidence_ids
        )
        if normalized and not lexical_match and not evidence_match and text not in new_facts:
            new_facts.append(text)
    record: dict[str, Any] = {
        "group": group,
        "label": label,
        "available": available,
        "fields": deepcopy(dict(fields)),
        "has_new_facts": bool(new_facts),
        "new_facts": new_facts,
        "evidence_id_valid": not invalid,
        "evidence_id_validity": {
            "valid": not invalid,
            "checked": len(evidence_ids),
            "evidence_ids": evidence_ids,
            "invalid_evidence_ids": invalid,
            "available_evidence_count": len(available_ids),
        },
        "call_metrics": dict(metrics),
        "used_fallback": used_fallback,
    }
    if error:
        record["error"] = _safe_error(RuntimeError(error))
    return record


def _aggregate(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "calls": len(calls),
        "duration_ms": round(sum(float(call.get("duration_ms", 0) or 0) for call in calls), 3),
        "prompt_tokens": sum(int(call.get("prompt_tokens", 0) or 0) for call in calls),
        "completion_tokens": sum(int(call.get("completion_tokens", 0) or 0) for call in calls),
        "total_tokens": sum(int(call.get("total_tokens", 0) or 0) for call in calls),
        "models": sorted({str(call.get("model")) for call in calls if call.get("model")}),
        "token_usage_known": all(call.get("total_tokens") is not None for call in calls),
    }


def _load_real_client() -> OpenAIModelClient:
    required = ("IAIC_API_BASE", "IAIC_API_KEY", "IAIC_CHAT_MODEL")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ExperimentConfigurationError("missing required IAIC_* configuration: " + ", ".join(missing))
    return OpenAIModelClient(timeout=120, retry_delay=0.2)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-docx", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-json", type=Path)
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument("--sample-id", default="")
    parser.add_argument("--split", default="fit")
    parser.add_argument("--offline", action="store_true")
    return parser


def run_experiment(
    input_docx: str | Path,
    output_dir: str | Path,
    *,
    baseline_json: str | Path | None = None,
    index_dir: str | Path | None = None,
    sample_id: str = "",
    split: str = "fit",
    offline: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    input_path = Path(input_docx)
    output = Path(output_dir)
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    baseline = _load_baseline(input_path, Path(baseline_json) if baseline_json else None)
    if sample_id:
        baseline["sample_id"] = sample_id
    sample_id = str(sample_id or baseline.get("sample_id") or input_path.stem)
    source_file = str(baseline.get("source_file") or input_path.name)
    facts = _report_facts(input_path, source_file)
    context_facts = _select_context_facts(facts, baseline)
    facility_context = baseline.get("facility_context")
    field_states = baseline.get("field_states")
    locked_facts = baseline.get("locked_facts")
    facility_type = None
    if isinstance(facility_context, Mapping):
        value = facility_context.get("facility_type")
        if value:
            facility_type = str(value)
    task_queries = _task_queries(baseline, facility_context, facts)
    output.mkdir(parents=True, exist_ok=True)
    baseline_path = output / "baseline_prediction.json"
    retrieval_path = output / "retrieval_trace.json"
    summary_path = output / "experiment_summary.json"
    ab_path = output / "ab_results.json"
    _write_json(baseline_path, baseline)

    if offline:
        client: Any = TrackingClient(OfflineClient(facts[0]["evidence_id"] if facts else ""))
    else:
        try:
            client = TrackingClient(_load_real_client())
        except ExperimentConfigurationError as error:
            unavailable = {
                group: {
                    "group": group,
                    "label": label,
                    "available": False,
                    "fields": {field: None for field in TARGET_FIELDS},
                    "used_fallback": False,
                    "error": _safe_error(error),
                }
                for group, label in (
                    ("B", "LLM without RAG"),
                    ("C", "LLM with current-report evidence"),
                    ("D", "LLM with evidence+RAG+similar labels"),
                )
            }
            groups = {"A": _group_record("A", "rule baseline", _normalised_fields(baseline), baseline, facts, [], {"model": "rule-baseline", "calls": 0})}
            groups.update(unavailable)
            retrieval = {
                "status": "not-run",
                "retrieval_available": False,
                "task_queries": task_queries,
                "task_hits": {},
                "hits": [],
                "retrieval_hits": [],
                "calls": [],
            }
            _write_json(retrieval_path, retrieval)
            _write_json(ab_path, {"schema_version": "narrative-ab-v1", "groups": groups, "results": list(groups.values())})
            summary = {
                "schema_version": "narrative-experiment-v1",
                "status": "configuration_error",
                "offline": False,
                "sample_id": sample_id,
                "model": "not-run",
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "configuration_error": _safe_error(error),
                "enhanced_prediction_written": False,
                "outputs": {"baseline_prediction": str(baseline_path), "retrieval_trace": str(retrieval_path), "ab_results": str(ab_path), "experiment_summary": str(summary_path), "enhanced_prediction": None},
            }
            _write_json(summary_path, summary)
            return summary

    groups: dict[str, dict[str, Any]] = {
        "A": _group_record("A", "rule baseline", _normalised_fields(baseline), baseline, facts, [], {"model": "rule-baseline", "calls": 0})
    }
    start_b = len(client.calls)
    try:
        fields_b, _ = _run_chat_group("B", client, baseline, [])
        groups["B"] = _group_record("B", "LLM without RAG", fields_b, baseline, facts, [], _aggregate(client.calls[start_b:]))
    except Exception as error:
        groups["B"] = _group_record(
            "B",
            "LLM without RAG",
            {field: None for field in TARGET_FIELDS},
            baseline,
            facts,
            [],
            _aggregate(client.calls[start_b:]),
            available=False,
            error=_safe_error(error),
        )
    start_c = len(client.calls)
    try:
        fields_c, _ = _run_chat_group("C", client, baseline, context_facts)
        groups["C"] = _group_record("C", "LLM with current-report evidence", fields_c, baseline, context_facts, [], _aggregate(client.calls[start_c:]))
    except Exception as error:
        groups["C"] = _group_record(
            "C",
            "LLM with current-report evidence",
            {field: None for field in TARGET_FIELDS},
            baseline,
            context_facts,
            [],
            _aggregate(client.calls[start_c:]),
            available=False,
            error=_safe_error(error),
        )

    retrieval_hits: list[dict[str, Any]] = []
    task_hits: dict[str, list[dict[str, Any]]] = {}
    task_errors: dict[str, str] = {}
    retrieval_status = "unavailable"
    retrieval_error: str | None = None
    if index_dir is not None:
        start_retrieval = len(client.calls)
        try:
            index = LightRagIndex.load(index_dir, client=client)
            task_hits, task_errors = _retrieve_task_hits(
                index,
                task_queries,
                sample_id=sample_id,
                split=split,
                facility_type=facility_type,
            )
            retrieval_hits = _merge_retrieval_hits(task_hits)
            if task_errors:
                retrieval_error = "; ".join(f"{task}: {message}" for task, message in task_errors.items())
        except Exception as error:
            retrieval_error = _safe_error(error)
        retrieval_calls = client.calls[start_retrieval:]
        retrieval_status = "error" if retrieval_error else ("retrieved" if retrieval_hits else "retrieved_empty")
    else:
        retrieval_calls = []
    retrieval = {
        "schema_version": "retrieval-trace-v1",
        "status": retrieval_status,
        "retrieval_available": index_dir is not None,
        "index_dir": str(index_dir) if index_dir is not None else None,
        "facility_type": facility_type,
        "query": _query(baseline, facility_context, facts),
        "task_queries": task_queries,
        "task_hits": task_hits,
        "hits": retrieval_hits,
        "retrieval_hits": retrieval_hits,
        "calls": retrieval_calls,
    }
    if retrieval_error:
        retrieval["error"] = retrieval_error
    _write_json(retrieval_path, retrieval)

    start_d = len(client.calls)
    d_error: str | None = None
    d_available = True
    try:
        narrative = run_narrative_enhancement(
            baseline,
            sample_id,
            source_file,
            facts,
            client,
            retriever=StaticRetriever(task_hits),
            split=split,
            facility_context=facility_context,
            field_states=field_states,
            locked_facts=locked_facts,
        )
    except Exception as error:
        d_available = False
        d_error = _safe_error(error)
        narrative = {
            "enhanced_prediction": baseline,
            "used_fallback": True,
            "validation_errors": [d_error],
        }
    enhanced = _prediction_dict(narrative.get("enhanced_prediction", baseline))
    fields_d = _normalised_fields(enhanced)
    groups["D"] = _group_record(
        "D",
        "LLM with evidence+RAG+similar labels",
        fields_d,
        baseline,
        facts,
        retrieval_hits,
        _aggregate(client.calls[start_d:]),
        available=d_available,
        used_fallback=bool(narrative.get("used_fallback")),
        error=d_error or "; ".join(str(item) for item in narrative.get("validation_errors", [])) or None,
    )
    groups["D"]["field_results"] = dict(
        narrative.get(
            "field_results",
            {
                field: ("fallback" if narrative.get("used_fallback") else "enhanced")
                for field in TARGET_FIELDS
            },
        )
    )
    locked_differences = _locked_top_level_differences(enhanced, baseline)
    groups["D"]["locked_top_level_differences"] = locked_differences
    groups["D"]["locked_fields_unchanged"] = not locked_differences
    groups["D"]["retrieval_status"] = retrieval_status
    groups["D"]["retrieval_trace"] = {
        "task_queries": deepcopy(task_queries),
        "hits": deepcopy(retrieval_hits),
    }
    enhanced_path = output / "enhanced_prediction.json"
    _write_json(enhanced_path, enhanced)
    _write_json(ab_path, {"schema_version": "narrative-ab-v1", "sample_id": sample_id, "split": split, "offline": offline, "target_fields": list(TARGET_FIELDS), "groups": groups, "results": list(groups.values())})
    has_unavailable_group = any(not groups[group].get("available", True) for group in ("B", "C", "D"))
    summary = {
        "schema_version": "narrative-experiment-v1",
        "status": "offline" if offline else ("partial" if has_unavailable_group or groups["D"]["used_fallback"] else "succeeded"),
        "offline": offline,
        "sample_id": sample_id,
        "split": split,
        "model": groups["D"]["call_metrics"].get("models", ["not-run"])[0] if groups["D"]["call_metrics"].get("models") else "not-run",
        "duration_ms": round((perf_counter() - started) * 1000, 3),
        "token_usage": _aggregate(client.calls),
        "retrieval": {"status": retrieval_status, "hit_count": len(retrieval_hits), "call_count": len(retrieval_calls)},
        "locked_fields_unchanged": not locked_differences,
        "groups": {group: groups[group]["call_metrics"] for group in ("A", "B", "C", "D")},
        "enhanced_prediction_written": True,
        "current_best_config": (
            "D"
            if groups["D"]["available"] and not groups["D"]["used_fallback"]
            else (
                "D-partial"
                if groups["D"].get("field_results")
                and "enhanced" in groups["D"]["field_results"].values()
                else "A"
            )
        ),
        "outputs": {"baseline_prediction": str(baseline_path), "enhanced_prediction": str(enhanced_path), "retrieval_trace": str(retrieval_path), "ab_results": str(ab_path), "experiment_summary": str(summary_path)},
        "unresolved": ["真实运行需提供 fit-only RAG index" ] if index_dir is None else [],
    }
    _write_json(summary_path, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_experiment(args.input_docx, args.output_dir, baseline_json=args.baseline_json, index_dir=args.index_dir, sample_id=args.sample_id, split=args.split, offline=args.offline)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(f"experiment error: {_safe_error(error)}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"offline", "succeeded", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
