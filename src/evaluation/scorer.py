"""Small, deterministic scorer for structured inspection records.

The scorer uses the official 100-point section weights.  Summary values are
compared field by field after Unicode/whitespace/number normalization. Defect
and recommendation rows use deterministic one-to-one exact matching on their
semantic fields (the display-only ``index`` is intentionally not required).
Text-list sections use a lightweight character/word fact-token coverage so a
partially preserved key fact receives partial credit without a model.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


SECTION_ORDER = (
    "summary",
    "detailed_conclusion",
    "recommendations",
    "defects",
    "causes",
    "treatments",
    "safety_impact",
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "summary": 20.0,
    "detailed_conclusion": 15.0,
    "recommendations": 20.0,
    "defects": 30.0,
    "causes": 5.0,
    "treatments": 5.0,
    "safety_impact": 5.0,
}

RECOMMENDATION_FIELDS = ("category", "content", "location")
DEFECT_FIELDS = (
    "location",
    "defect_type",
    "description",
    "is_new",
    "previous_status",
    "development",
)

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*|[\u3400-\u9fff]+")


def normalize_text(value: Any) -> str:
    """Normalize a scalar for deterministic comparisons.

    Full-width characters are normalized with NFKC, case is folded, all
    whitespace is removed, and standalone decimal strings are canonicalized
    (for example, ``87.060`` becomes ``87.06``).
    """

    if value is None:
        return ""
    if isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(text.split())
    if _NUMBER_RE.fullmatch(text):
        try:
            number = Decimal(text)
        except InvalidOperation:
            return text
        normalized = format(number, "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
        return normalized if normalized not in {"", "-", "+"} else "0"
    return text


def _round(value: float) -> float:
    return round(float(value), 6)


def _metrics(true_positive: int, false_positive: int, false_negative: int) -> dict[str, float]:
    predicted = true_positive + false_positive
    expected = true_positive + false_negative
    precision = true_positive / predicted if predicted else 1.0
    recall = true_positive / expected if expected else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "precision": _round(precision),
        "recall": _round(recall),
        "f1": _round(f1),
    }


def _section_result(
    section: str,
    weight: float,
    true_positive: int,
    false_positive: int,
    false_negative: int,
    missing: Iterable[Any] = (),
    extra: Iterable[Any] = (),
) -> dict[str, Any]:
    metric = _metrics(true_positive, false_positive, false_negative)
    return {
        "section": section,
        "weight": _round(weight),
        "score": _round(weight * metric["f1"]),
        **metric,
        "counts": {
            "true_positive": int(true_positive),
            "false_positive": int(false_positive),
            "false_negative": int(false_negative),
        },
        "missing": list(missing),
        "extra": list(extra),
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _summary_result(gold: Any, prediction: Any, weight: float) -> dict[str, Any]:
    gold_summary = _mapping(gold)
    prediction_summary = _mapping(prediction)
    missing: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
    true_positive = 0
    false_positive = 0
    false_negative = 0

    for field in sorted(set(gold_summary) | set(prediction_summary), key=str):
        in_gold = field in gold_summary
        in_prediction = field in prediction_summary
        if in_gold and in_prediction:
            if normalize_text(gold_summary[field]) == normalize_text(prediction_summary[field]):
                true_positive += 1
            else:
                false_negative += 1
                false_positive += 1
                missing.append({"field": field, "value": gold_summary[field]})
                extra.append({"field": field, "value": prediction_summary[field]})
        elif in_gold:
            false_negative += 1
            missing.append({"field": field, "value": gold_summary[field]})
        else:
            false_positive += 1
            extra.append({"field": field, "value": prediction_summary[field]})

    return _section_result(
        "summary",
        weight,
        true_positive,
        false_positive,
        false_negative,
        missing,
        extra,
    )


def _row_signature(row: Any, fields: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(row, Mapping):
        return (normalize_text(row),)
    return tuple(normalize_text(row.get(field, "")) for field in fields)


def _row_result(
    section: str,
    gold: Any,
    prediction: Any,
    fields: Sequence[str],
    weight: float,
) -> dict[str, Any]:
    gold_rows = _list(gold)
    prediction_rows = _list(prediction)
    prediction_signatures = [_row_signature(row, fields) for row in prediction_rows]
    used_prediction: set[int] = set()
    matched = 0
    missing: list[Any] = []

    for gold_row in gold_rows:
        signature = _row_signature(gold_row, fields)
        match_index = next(
            (
                index
                for index, prediction_signature in enumerate(prediction_signatures)
                if index not in used_prediction and prediction_signature == signature
            ),
            None,
        )
        if match_index is None:
            missing.append(gold_row)
        else:
            used_prediction.add(match_index)
            matched += 1

    extra = [
        row for index, row in enumerate(prediction_rows) if index not in used_prediction
    ]
    return _section_result(
        section,
        weight,
        matched,
        len(extra),
        len(missing),
        missing,
        extra,
    )


def _fact_tokens(value: Any) -> Counter[str]:
    text = normalize_text(value)
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        chunk = match.group(0)
        if all("\u3400" <= character <= "\u9fff" for character in chunk):
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                tokens.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
        else:
            tokens.append(chunk)
    if not tokens and text:
        tokens.append(text)
    return Counter(tokens)


def _counter_total(counter: Counter[str]) -> int:
    return sum(counter.values())


def _counter_items(counter: Counter[str]) -> list[str]:
    return sorted(counter.elements())


def _fact_result(section: str, gold: Any, prediction: Any, weight: float) -> dict[str, Any]:
    gold_items = [item for item in _list(gold) if normalize_text(item)]
    prediction_items = [item for item in _list(prediction) if normalize_text(item)]
    gold_counters = [_fact_tokens(item) for item in gold_items]
    prediction_counters = [_fact_tokens(item) for item in prediction_items]

    candidates: list[tuple[float, int, int, int]] = []
    for gold_index, gold_counter in enumerate(gold_counters):
        gold_total = _counter_total(gold_counter)
        for prediction_index, prediction_counter in enumerate(prediction_counters):
            intersection = _counter_total(gold_counter & prediction_counter)
            if not intersection:
                continue
            prediction_total = _counter_total(prediction_counter)
            pair_f1 = (2 * intersection / (gold_total + prediction_total)) if gold_total + prediction_total else 0.0
            candidates.append((-pair_f1, -intersection, gold_index, prediction_index))
    candidates.sort()

    matches: dict[int, int] = {}
    used_predictions: set[int] = set()
    for _, _, gold_index, prediction_index in candidates:
        if gold_index in matches or prediction_index in used_predictions:
            continue
        matches[gold_index] = prediction_index
        used_predictions.add(prediction_index)

    true_positive = 0
    missing: list[dict[str, Any]] = []
    for gold_index, gold_counter in enumerate(gold_counters):
        prediction_counter = prediction_counters[matches[gold_index]] if gold_index in matches else Counter()
        intersection = _counter_total(gold_counter & prediction_counter)
        true_positive += intersection
        gold_total = _counter_total(gold_counter)
        coverage = intersection / gold_total if gold_total else 1.0
        missing_facts = _counter_items(gold_counter - prediction_counter)
        if missing_facts:
            missing.append(
                {
                    "value": gold_items[gold_index],
                    "coverage": _round(coverage),
                    "missing_facts": missing_facts,
                }
            )

    extra: list[dict[str, Any]] = []
    for prediction_index, prediction_counter in enumerate(prediction_counters):
        if prediction_index in used_predictions:
            gold_index = next(
                index for index, matched_prediction in matches.items() if matched_prediction == prediction_index
            )
            gold_counter = gold_counters[gold_index]
            intersection = _counter_total(gold_counter & prediction_counter)
            extra_facts = _counter_items(prediction_counter - gold_counter)
            prediction_total = _counter_total(prediction_counter)
            coverage = intersection / prediction_total if prediction_total else 1.0
        else:
            extra_facts = _counter_items(prediction_counter)
            coverage = 0.0
        if extra_facts:
            extra.append(
                {
                    "value": prediction_items[prediction_index],
                    "coverage": _round(coverage),
                    "extra_facts": extra_facts,
                }
            )

    false_negative = sum(_counter_total(counter) for counter in gold_counters) - true_positive
    false_positive = sum(_counter_total(counter) for counter in prediction_counters) - true_positive
    return _section_result(
        section,
        weight,
        true_positive,
        false_positive,
        false_negative,
        missing,
        extra,
    )


def _validated_weights(weights: Mapping[str, Any] | None) -> dict[str, float]:
    source = DEFAULT_WEIGHTS if weights is None else weights
    result: dict[str, float] = {}
    for section in SECTION_ORDER:
        if section not in source:
            raise ValueError(f"missing score weight for section: {section}")
        value = float(source[section])
        if value < 0:
            raise ValueError(f"score weight must be non-negative: {section}")
        result[section] = value
    if abs(sum(result.values()) - 100.0) > 1e-6:
        raise ValueError("score weights must sum to 100")
    return result


def load_weights(path: str | Path | None = None) -> dict[str, float]:
    """Load official weights from a JSON file or return the official defaults."""

    if path is None:
        return dict(DEFAULT_WEIGHTS)
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    raw_weights = payload.get("weights") if isinstance(payload, Mapping) else payload
    if isinstance(raw_weights, list):
        mapping = {
            str(item["section"]): item["points"]
            for item in raw_weights
            if isinstance(item, Mapping) and "section" in item and "points" in item
        }
    elif isinstance(raw_weights, Mapping):
        mapping = dict(raw_weights)
    else:
        raise ValueError("weights JSON must contain a weights list or object")
    return _validated_weights(mapping)


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Load either a JSON object/list or JSONL records from ``path``."""

    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        records: list[Any] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSONL at line {line_number}: {error}") from error
        payload = records

    if isinstance(payload, Mapping):
        if isinstance(payload.get("records"), list):
            payload = payload["records"]
        elif isinstance(payload.get("samples"), list):
            payload = payload["samples"]
        else:
            payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("records input must be a JSON object, JSON list, or JSONL")
    if not all(isinstance(record, Mapping) for record in payload):
        raise ValueError("every record must be a JSON object")
    return [dict(record) for record in payload]


def _record_id(record: Mapping[str, Any], index: int) -> str:
    value = record.get("sample_id")
    if value is None or not str(value).strip():
        return str(index)
    return str(value)


def _has_complete_ids(records: Sequence[Mapping[str, Any]]) -> bool:
    return all(record.get("sample_id") is not None and str(record.get("sample_id")).strip() for record in records)


def _align_records(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[str, Mapping[str, Any], Mapping[str, Any]]], list[str], list[str]]:
    use_ids = bool(gold_records or prediction_records) and _has_complete_ids(gold_records) and _has_complete_ids(prediction_records)
    if not use_ids:
        count = max(len(gold_records), len(prediction_records))
        aligned: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
        for index in range(count):
            gold = gold_records[index] if index < len(gold_records) else {}
            prediction = prediction_records[index] if index < len(prediction_records) else {}
            sample_id = _record_id(gold, index) if gold else _record_id(prediction, index)
            aligned.append((sample_id, gold, prediction))
        return aligned, [], []

    def indexed(records: Sequence[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for index, record in enumerate(records):
            sample_id = _record_id(record, index)
            if sample_id in result:
                raise ValueError(f"duplicate sample_id in {label}: {sample_id}")
            result[sample_id] = record
        return result

    gold_by_id = indexed(gold_records, "gold")
    prediction_by_id = indexed(prediction_records, "predictions")
    sample_ids = list(gold_by_id)
    sample_ids.extend(sorted(set(prediction_by_id) - set(gold_by_id)))
    aligned = [
        (sample_id, gold_by_id.get(sample_id, {}), prediction_by_id.get(sample_id, {}))
        for sample_id in sample_ids
    ]
    missing_sample_ids = sorted(set(gold_by_id) - set(prediction_by_id))
    extra_sample_ids = sorted(set(prediction_by_id) - set(gold_by_id))
    return aligned, missing_sample_ids, extra_sample_ids


def score_record(
    gold: Mapping[str, Any],
    prediction: Mapping[str, Any],
    weights: Mapping[str, Any] | None = None,
    sample_id: str | None = None,
) -> dict[str, Any]:
    """Score one pair of records and return per-section diagnostics."""

    validated = _validated_weights(weights)
    gold_record = _mapping(gold)
    prediction_record = _mapping(prediction)
    sections = {
        "summary": _summary_result(gold_record.get("summary"), prediction_record.get("summary"), validated["summary"]),
        "detailed_conclusion": _fact_result(
            "detailed_conclusion",
            gold_record.get("detailed_conclusion"),
            prediction_record.get("detailed_conclusion"),
            validated["detailed_conclusion"],
        ),
        "recommendations": _row_result(
            "recommendations",
            gold_record.get("recommendations"),
            prediction_record.get("recommendations"),
            RECOMMENDATION_FIELDS,
            validated["recommendations"],
        ),
        "defects": _row_result(
            "defects",
            gold_record.get("defects"),
            prediction_record.get("defects"),
            DEFECT_FIELDS,
            validated["defects"],
        ),
        "causes": _fact_result("causes", gold_record.get("causes"), prediction_record.get("causes"), validated["causes"]),
        "treatments": _fact_result(
            "treatments", gold_record.get("treatments"), prediction_record.get("treatments"), validated["treatments"]
        ),
        "safety_impact": _fact_result(
            "safety_impact",
            gold_record.get("safety_impact"),
            prediction_record.get("safety_impact"),
            validated["safety_impact"],
        ),
    }
    total_score = sum(sections[section]["score"] for section in SECTION_ORDER)
    result: dict[str, Any] = {
        "sample_id": sample_id if sample_id is not None else str(gold_record.get("sample_id", "")),
        "total_score": _round(total_score),
        "max_score": 100.0,
        "sections": sections,
    }
    return result


def _aggregate_section(
    section: str,
    results: Sequence[tuple[str, Mapping[str, Any]]],
    weight: float,
) -> dict[str, Any]:
    true_positive = sum(int(result["counts"]["true_positive"]) for _, result in results)
    false_positive = sum(int(result["counts"]["false_positive"]) for _, result in results)
    false_negative = sum(int(result["counts"]["false_negative"]) for _, result in results)
    missing: list[Any] = []
    extra: list[Any] = []
    for sample_id, result in results:
        for item in result["missing"]:
            missing.append({"sample_id": sample_id, **item} if isinstance(item, Mapping) else {"sample_id": sample_id, "value": item})
        for item in result["extra"]:
            extra.append({"sample_id": sample_id, **item} if isinstance(item, Mapping) else {"sample_id": sample_id, "value": item})
    aggregate = _section_result(section, weight, true_positive, false_positive, false_negative, missing, extra)
    sample_f1 = [float(result["f1"]) for _, result in results]
    macro_f1 = sum(sample_f1) / len(sample_f1) if sample_f1 else 1.0
    aggregate["macro_f1"] = _round(macro_f1)
    aggregate["macro_score"] = _round(weight * macro_f1)
    aggregate["sample_count"] = len(results)
    return aggregate


def score_dataset(
    gold_records: Sequence[Mapping[str, Any]],
    prediction_records: Sequence[Mapping[str, Any]],
    weights: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score aligned record collections and aggregate section metrics."""

    validated = _validated_weights(weights)
    aligned, missing_sample_ids, extra_sample_ids = _align_records(gold_records, prediction_records)
    record_results: list[dict[str, Any]] = []
    section_results: dict[str, list[tuple[str, Mapping[str, Any]]]] = {section: [] for section in SECTION_ORDER}

    for sample_id, gold, prediction in aligned:
        result = score_record(gold, prediction, validated, sample_id)
        record_results.append(result)
        for section in SECTION_ORDER:
            section_results[section].append((sample_id, result["sections"][section]))

    aggregate_sections = {
        section: _aggregate_section(section, section_results[section], validated[section])
        for section in SECTION_ORDER
    }
    total_score = sum(aggregate_sections[section]["score"] for section in SECTION_ORDER)
    macro_total_score = (
        sum(float(result["total_score"]) for result in record_results) / len(record_results)
        if record_results
        else 100.0
    )
    return {
        "total_score": _round(total_score),
        "micro_total_score": _round(total_score),
        "macro_total_score": _round(macro_total_score),
        "max_score": 100.0,
        "record_count": len(aligned),
        "weights": {section: _round(validated[section]) for section in SECTION_ORDER},
        "missing_sample_ids": missing_sample_ids,
        "extra_sample_ids": extra_sample_ids,
        "sections": aggregate_sections,
        "records": record_results,
    }
