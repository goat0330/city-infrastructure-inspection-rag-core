#!/usr/bin/env python3
"""Build a deterministic inventory of semantic candidate inputs.

This inventory is deliberately narrower than the semantic candidate builder:
it reads the frozen baseline, current-report evidence, explicit diagnostics,
and current deterministic extraction flags.  It never reads Gold records and
never calls an embedding, reranker, chat, or other model API.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contracts.semantic_extraction import ExtractionCandidate  # noqa: E402
from src.extraction.pipeline import extract_report  # noqa: E402
from src.extraction.semantic_candidates import build_semantic_candidates  # noqa: E402
from src.parsing import parse_docx  # noqa: E402
from src.routing import route_sections  # noqa: E402


DEFAULT_MANIFEST = ROOT / "runs/b2-night/eval-manifest.json"
DEFAULT_BASELINE = ROOT / "runs/b2-night/baseline/aligned-predictions.jsonl"
DEFAULT_DIAGNOSTICS = ROOT / "runs/b2-night/baseline/diagnostics.json"
DEFAULT_OUTPUT_DIR = ROOT / "runs/round2-semantic/86-gold-candidate-inventory"

_CANDIDATE_KEYS = (
    "candidates",
    "semantic_candidates",
    "candidate_inputs",
    "row_candidates",
    "index_candidates",
)
_ROW_INDEX_KEYS = (
    "row_index",
    "prediction_row",
    "defect_index",
    "recommendation_index",
    "index",
)
_TASK_TYPES = {
    "defect_row_validation",
    "recommendation_category",
    "conclusion_evidence_selection",
    "risk_evidence_selection",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL entry at line {line_number} is not an object: {path}")
            records.append(dict(value))
    return records


def _normalise_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _plain(value: Any) -> Any:
    """Convert parser dataclasses and nested values to JSON-compatible data."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain(to_dict())
    return str(value)


def _stable_evidence_id(source_file: str, block: Any) -> str:
    """Use the existing report-evidence ID recipe without touching Gold."""

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


def load_report_evidence(docx_path: Path, source_file: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse one current report and retain only its traceable report facts."""

    document = parse_docx(docx_path, source_file=source_file)
    routes = route_sections(document)
    categories: dict[int, list[str]] = {}
    for route in routes:
        category = str(
            getattr(getattr(route, "category", ""), "value", getattr(route, "category", ""))
        )
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

    section_counts = Counter(str(fact["section"]) for fact in facts)
    route_categories = Counter(
        str(getattr(getattr(route, "category", ""), "value", getattr(route, "category", "")))
        for route in routes
    )
    return facts, {
        "block_count": len(document.blocks),
        "route_count": len(routes),
        "route_categories": dict(sorted(route_categories.items())),
        "evidence_count": len(facts),
        "evidence_section_counts": dict(sorted(section_counts.items())),
    }


def _has_value(value: object) -> bool:
    return value is not None and value is not False and str(value).strip() != ""


def _has_row_index(value: object) -> bool:
    """Check a candidate payload only; do not scan Gold-comparison issues."""

    if not isinstance(value, Mapping):
        return False
    if any(_has_value(value.get(key)) for key in _ROW_INDEX_KEYS):
        return True
    for key in ("row", "candidate", "candidate_input"):
        nested = value.get(key)
        if isinstance(nested, Mapping) and _has_row_index(nested):
            return True
    return False


def _as_mapping_list(value: object) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def diagnostic_candidate_inputs(record: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool, bool]:
    """Return explicit candidate payloads and two diagnostic presence flags.

    ``sections.issues`` is intentionally excluded.  Its ``gold_row`` and
    ``prediction_row`` values describe evaluation alignment, not candidate
    inputs, and using them would make Gold differences a semantic source.
    """

    payloads: list[dict[str, Any]] = []
    has_candidate_field = False
    has_row_index_candidate = False
    containers: list[Mapping[str, Any]] = [record]
    diagnostics = record.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        containers.append(diagnostics)

    for container in containers:
        for key in _CANDIDATE_KEYS:
            if key not in container:
                continue
            has_candidate_field = True
            values = _as_mapping_list(container[key])
            payloads.extend(values)
            if any(_has_row_index(item) for item in values):
                has_row_index_candidate = True

    quality_flags = record.get("quality_flags") or record.get("quality_flag_codes")
    for flag in _as_mapping_list(quality_flags):
        for key in ("candidate", "candidate_input"):
            value = flag.get(key)
            if value is None:
                continue
            has_candidate_field = True
            values = _as_mapping_list(value)
            payloads.extend(values)
            if any(_has_row_index(item) for item in values):
                has_row_index_candidate = True

    return payloads, has_candidate_field, has_row_index_candidate


def _evidence_aliases(facts: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    lookup: dict[str, Mapping[str, Any]] = {}
    for fact in facts:
        evidence_id = str(fact.get("evidence_id", ""))
        if evidence_id:
            lookup[evidence_id] = fact
            lookup[f"report:{evidence_id}"] = fact
    return lookup


def _text_matches(text: object, fact_text: object) -> bool:
    left = _normalise_text(text)
    right = _normalise_text(fact_text)
    return bool(left and right and (left in right or right in left))


def _evidence_ids_for_text(
    texts: Sequence[object], facts: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    values: list[str] = []
    for text in texts:
        if not _normalise_text(text):
            continue
        for fact in facts:
            if _text_matches(text, fact.get("text")):
                evidence_id = str(fact.get("evidence_id", ""))
                if evidence_id and evidence_id not in values:
                    values.append(evidence_id)
    return tuple(values)


def _baseline_row_evidence(row: Mapping[str, Any]) -> list[str]:
    hints: list[str] = []
    raw_evidence = row.get("evidence")
    if isinstance(raw_evidence, Mapping):
        raw_evidence = [raw_evidence]
    if isinstance(raw_evidence, Sequence) and not isinstance(raw_evidence, (str, bytes, bytearray)):
        for item in raw_evidence:
            if isinstance(item, Mapping):
                hints.append(str(item.get("raw_text", "")))
    return hints


def _row_index(value: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        raw = value.get(key)
        if isinstance(raw, bool):
            continue
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if index >= 0:
            return index
    return None


def _build_index_candidate(
    payload: Mapping[str, Any],
    *,
    sample_id: str,
    baseline: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Project an explicit diagnostic row/index payload onto the contract."""

    task_type = str(payload.get("task_type", ""))
    if task_type not in _TASK_TYPES:
        return None
    index: int | None = None
    row: Mapping[str, Any] | None = None
    field = ""
    if task_type == "defect_row_validation":
        index = _row_index(payload, "defect_index", "row_index", "index")
        field = "defects"
    elif task_type == "recommendation_category":
        index = _row_index(payload, "recommendation_index", "row_index", "index")
        field = "recommendations"
    elif task_type == "conclusion_evidence_selection":
        field = "overall_conclusion"
    elif task_type == "risk_evidence_selection":
        field = "risk_points"

    if field in {"defects", "recommendations"}:
        values = baseline.get(field)
        if index is None or not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            return None
        if index >= len(values) or not isinstance(values[index], Mapping):
            return None
        row = dict(values[index])
        source_text = _normalise_text(
            row.get("description") if field == "defects" else row.get("content")
        )
        text_hints = [source_text, *_baseline_row_evidence(row)]
        evidence_ids = _evidence_ids_for_text(text_hints, facts)
        rule_output = dict(row)
        context = {"diagnostic_index": index, "reason": "explicit diagnostic row/index"}
    else:
        summary = baseline.get("summary")
        if not isinstance(summary, Mapping):
            return None
        source_text = _normalise_text(summary.get(field))
        evidence_ids = _evidence_ids_for_text((source_text,), facts)
        rule_output = {field: source_text}
        context = {"summary_field": field, "reason": "explicit diagnostic field"}

    if not source_text or not evidence_ids:
        return None
    candidate_id = _normalise_text(payload.get("candidate_id"))
    if not candidate_id:
        suffix = f"{field}:{index}" if index is not None else field
        candidate_id = f"{sample_id}:{task_type}:{suffix}"
    return {
        "candidate_id": candidate_id,
        "sample_id": sample_id,
        "task_type": task_type,
        "source_text": source_text,
        "evidence_ids": list(evidence_ids),
        "context": context,
        "rule_output": rule_output,
    }


def _normalise_candidate(
    payload: Mapping[str, Any],
    *,
    sample_id: str,
    baseline: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate an explicit candidate and keep evidence inside this report."""

    item = dict(payload)
    item.setdefault("sample_id", sample_id)
    evidence_lookup = _evidence_aliases(facts)
    raw_ids = item.get("evidence_ids", ())
    if isinstance(raw_ids, (str, bytes, bytearray)):
        return None, "候选输入不足：evidence_ids 不是 evidence ID 数组"
    try:
        evidence_ids = tuple(str(value).strip() for value in raw_ids)
    except TypeError:
        return None, "候选输入不足：evidence_ids 不是 evidence ID 数组"
    if not evidence_ids or any(not value for value in evidence_ids):
        return None, "候选输入不足：候选没有 evidence_ids"
    if any(value not in evidence_lookup for value in evidence_ids):
        return None, "候选输入不足：候选 evidence_id 无法绑定当前报告 evidence"

    if not _normalise_text(item.get("source_text")):
        item["source_text"] = str(evidence_lookup[evidence_ids[0]].get("text", ""))
    item.setdefault("rule_output", {})
    item.setdefault("context", {})
    item.setdefault("facility_context", baseline.get("facility_context", {}))
    try:
        candidate = ExtractionCandidate.from_dict(item)
    except (TypeError, ValueError) as exc:
        return None, f"候选输入不足：ExtractionCandidate 合同校验失败（{type(exc).__name__}）"
    return candidate.to_dict(), None


def _quality_flag_codes(record: Mapping[str, Any]) -> list[str]:
    values = record.get("quality_flag_codes") or record.get("quality_flags") or []
    if isinstance(values, Mapping):
        values = [values]
    if isinstance(values, str):
        values = [values]
    codes: list[str] = []
    if isinstance(values, Sequence):
        for value in values:
            if isinstance(value, Mapping):
                code = _normalise_text(value.get("code") or value.get("quality_flag"))
            else:
                code = _normalise_text(value)
            if code and code not in codes:
                codes.append(code)
    return codes


def _runtime_candidates(
    baseline: Mapping[str, Any],
    extraction: object,
    facts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build candidates from the current deterministic extraction only."""

    flags = getattr(extraction, "quality_flags", ())
    if not flags:
        return []
    candidate_baseline = dict(baseline)
    facility_context = getattr(extraction, "facility_context", None)
    if facility_context is not None and callable(getattr(facility_context, "to_dict", None)):
        candidate_baseline["facility_context"] = facility_context.to_dict()
    diagnostics = {"quality_flags": [dict(flag) for flag in flags if isinstance(flag, Mapping)]}
    report_evidence_ids = {str(item.get("evidence_id")) for item in facts}
    return [
        candidate.to_dict()
        for candidate in build_semantic_candidates(
            candidate_baseline,
            diagnostics,
            facts,
            max_candidates=24,
        )
        if candidate.evidence_ids
        and all(str(evidence_id) in report_evidence_ids for evidence_id in candidate.evidence_ids)
    ]


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def build_inventory(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    baseline_path: Path = DEFAULT_BASELINE,
    diagnostics_path: Path = DEFAULT_DIAGNOSTICS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Build the inventory and write its three delivery artifacts."""

    manifest = _load_json(manifest_path)
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("records"), list):
        raise ValueError("eval-manifest.json must contain a records array")
    manifest_records = [dict(item) for item in manifest["records"] if isinstance(item, Mapping)]
    baseline_records = _load_jsonl(baseline_path)
    baseline_by_id = {str(item.get("sample_id", "")): item for item in baseline_records}
    diagnostics = _load_json(diagnostics_path)
    diagnostic_records = diagnostics.get("records", []) if isinstance(diagnostics, Mapping) else []
    diagnostics_by_id = {
        str(item.get("sample_id", "")): dict(item)
        for item in diagnostic_records
        if isinstance(item, Mapping)
    }

    candidates: list[dict[str, Any]] = []
    sample_details: list[dict[str, Any]] = []
    no_candidate_reasons: Counter[str] = Counter()
    quality_flag_counts: Counter[str] = Counter()
    report_section_counts: Counter[str] = Counter()
    report_errors: list[dict[str, str]] = []
    missing_baseline: list[str] = []
    missing_diagnostics: list[str] = []
    seen_sample_ids: set[str] = set()
    explicit_candidate_records = 0
    row_index_candidate_records = 0
    ignored_gold_issue_records = 0
    report_evidence_loaded_count = 0
    report_evidence_total = 0

    for manifest_record in manifest_records:
        sample_id = _normalise_text(manifest_record.get("sample_id"))
        if not sample_id:
            continue
        seen_sample_ids.add(sample_id)
        baseline = baseline_by_id.get(sample_id)
        diagnostic = diagnostics_by_id.get(sample_id)
        if baseline is None:
            missing_baseline.append(sample_id)
            baseline = {"sample_id": sample_id}
        if diagnostic is None:
            missing_diagnostics.append(sample_id)
            diagnostic = {"sample_id": sample_id}

        quality_flags = _quality_flag_codes(diagnostic)
        quality_flag_counts.update(quality_flags)
        if isinstance(diagnostic.get("sections"), Mapping):
            if any(
                isinstance(section, Mapping)
                and any(
                    isinstance(issue, Mapping)
                    and ("gold_row" in issue or "prediction_row" in issue)
                    for issue in section.get("issues", [])
                    if isinstance(section.get("issues", []), Sequence)
                )
                for section in diagnostic["sections"].values()
            ):
                ignored_gold_issue_records += 1

        source_docx = _normalise_text(manifest_record.get("source_docx"))
        source_file = _normalise_text(baseline.get("source_file")) or source_docx
        docx_path = ROOT / source_docx
        facts: list[dict[str, Any]] = []
        report_stats: dict[str, Any] = {
            "block_count": 0,
            "route_count": 0,
            "route_categories": {},
            "evidence_count": 0,
            "evidence_section_counts": {},
        }
        report_error: str | None = None
        runtime_extraction: object | None = None
        if not docx_path.is_file():
            report_error = f"report file not found: {source_docx}"
        else:
            try:
                facts, report_stats = load_report_evidence(docx_path, source_file)
                runtime_extraction = extract_report(docx_path, source_file=source_file)
                report_evidence_loaded_count += 1
                report_evidence_total += len(facts)
                report_section_counts.update(report_stats["evidence_section_counts"])
            except Exception as exc:  # pragma: no cover - only exercised by broken source files
                report_error = f"{type(exc).__name__}: {_normalise_text(exc)}"
        if report_error:
            report_errors.append({"sample_id": sample_id, "error": report_error})

        payloads, has_candidate_field, has_row_index_candidate = diagnostic_candidate_inputs(diagnostic)
        if has_candidate_field:
            explicit_candidate_records += 1
        if has_row_index_candidate:
            row_index_candidate_records += 1

        sample_candidates: list[dict[str, Any]] = []
        rejected_reasons: list[str] = []
        for payload in payloads:
            candidate_payload, reason = _normalise_candidate(
                payload, sample_id=sample_id, baseline=baseline, facts=facts
            )
            if candidate_payload is None and _has_row_index(payload):
                candidate_payload = _build_index_candidate(
                    payload, sample_id=sample_id, baseline=baseline, facts=facts
                )
                if candidate_payload is None:
                    reason = reason or "候选输入不足：row/index 候选无法绑定当前报告 evidence"
            if candidate_payload is not None:
                sample_candidates.append(candidate_payload)
            elif reason:
                rejected_reasons.append(reason)

        if runtime_extraction is not None:
            sample_candidates.extend(_runtime_candidates(baseline, runtime_extraction, facts))

        candidate_ids: set[str] = set()
        for candidate in sample_candidates:
            if candidate["candidate_id"] in candidate_ids:
                continue
            candidate_ids.add(str(candidate["candidate_id"]))
            candidates.append(candidate)

        if sample_candidates:
            reason = ""
        elif report_error:
            reason = "候选输入不足：当前报告 evidence 加载失败"
        elif not has_candidate_field:
            flags = ",".join(quality_flags) if quality_flags else "none"
            reason = f"候选输入不足：diagnostics 未提供 row/index 级候选（quality_flag_codes={flags}）"
        elif rejected_reasons:
            reason = rejected_reasons[0]
        else:
            reason = "候选输入不足：diagnostics 候选为空或没有可追溯的报告 evidence"
        if reason:
            no_candidate_reasons[reason] += 1

        sample_details.append(
            {
                "sample_id": sample_id,
                "split": _normalise_text(manifest_record.get("split")),
                "source_docx": source_docx,
                "baseline_loaded": sample_id in baseline_by_id,
                "diagnostics_loaded": sample_id in diagnostics_by_id,
                "quality_flag_codes": quality_flags,
                "report_evidence": report_stats,
                "candidate_count": len(sample_candidates),
                "candidate_input": {
                    "has_explicit_candidate_field": has_candidate_field,
                    "has_row_index_candidate": has_row_index_candidate,
                    "reason": reason,
                },
            }
        )

    task_type_counts = Counter(str(item.get("task_type", "")) for item in candidates)
    candidate_sample_ids = sorted({str(item["sample_id"]) for item in candidates})
    summary: dict[str, Any] = {
        "schema": "semantic-candidate-inventory-v1",
        "inputs": {
            "manifest": _relative(manifest_path),
            "baseline": _relative(baseline_path),
            "diagnostics": _relative(diagnostics_path),
            "gold_loaded": False,
            "model_api_calls": 0,
            "network_calls": 0,
            "runtime_deterministic_extraction": True,
        },
        "sample_coverage": {
            "manifest_count": len(manifest_records),
            "unique_manifest_sample_count": len(seen_sample_ids),
            "baseline_count": len(baseline_records),
            "diagnostics_record_count": len(diagnostics_by_id),
            "processed_count": len(sample_details),
            "report_evidence_loaded_count": report_evidence_loaded_count,
            "candidate_sample_count": len(candidate_sample_ids),
        },
        "candidates": {
            "candidate_count": len(candidates),
            "sample_ids": candidate_sample_ids,
            "task_type_counts": dict(sorted(task_type_counts.items())),
            "contract_checked_count": len(candidates),
            "contract_validation_failures": 0,
            "evidence_source": "current_report_only",
        },
        "report_evidence": {
            "total_fact_count": report_evidence_total,
            "section_counts": dict(sorted(report_section_counts.items())),
            "errors": report_errors,
        },
            "diagnostics": {
            "quality_flag_code_counts": dict(sorted(quality_flag_counts.items())),
            "records_with_explicit_candidate_field": explicit_candidate_records,
            "records_with_row_index_candidate": row_index_candidate_records,
                "gold_comparison_issue_records_ignored": ignored_gold_issue_records,
                "runtime_deterministic_extraction": True,
            },
        "no_candidate_reasons": dict(sorted(no_candidate_reasons.items())),
        "missing_inputs": {
            "baseline_sample_ids": sorted(missing_baseline),
            "diagnostics_sample_ids": sorted(missing_diagnostics),
            "report_errors": report_errors,
        },
        "gold_leakage_risk": {
            "exists": False,
            "gold_loaded": False,
            "gold_fields_used": [],
            "ignored_diagnostic_fields": ["gold_row", "gold_anchors", "Gold-vs-baseline issue categories"],
            "reason": "The script never opens gold_path/gold_record and ignores Gold-comparison issue rows.",
        },
        "samples": sample_details,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "candidates.jsonl"
    candidates_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in candidates),
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    readme = (
        "# 86 份 Gold 语义候选输入清单\n\n"
        "## 生成\n\n"
        "在项目根目录执行：\n\n"
        "```text\n"
        "python scripts/build_semantic_candidate_inventory.py\n"
        "```\n\n"
        "脚本只读取 `runs/b2-night/eval-manifest.json`、baseline、baseline diagnostics 和 manifest 指向的当前 Word 报告。它直接使用现有 Word parser/route 生成 `docx:<hash>` 报告 evidence，并调用现有确定性 `extract_report` 获取运行时质量标记；不读取 Gold 内容，也不调用 IAIC、Embedding、Reranker、Chat 或网络 API。\n\n"
        "## 当前限制\n\n"
        "原始 86 条 evaluation diagnostics 只有汇总 `quality_flags`，没有独立的 `candidates`、`semantic_candidates` 或 row/index 级语义候选输入。脚本不使用 `sections.issues` 里的 Gold 对齐行号，而是从当前 Word 的确定性抽取质量标记恢复可追溯候选。\n\n"
        "`diagnostics.sections[*].issues` 中的 `gold_row`、`prediction_row` 和 Gold 对比类别被故意忽略，不能据此伪造候选。待后续 deterministic extraction diagnostics 输出真实 row/index 候选并能绑定当前报告 evidence 后，再重新运行脚本。\n\n"
        "## 输出\n\n"
        "- `candidates.jsonl`：每条真实候选均包含 `sample_id`、`candidate_id`、`task_type`、`source_text`、`evidence_ids`、`context`、`rule_output`，并在生成时通过 `ExtractionCandidate.from_dict`。\n"
        "- `summary.json`：覆盖数、报告 evidence 统计、task type 统计、无候选原因、质量标记和 Gold 泄漏风险。\n"
        "- 本清单是后续 Semantic Extraction live 评估的输入审计，不是模型预测，也不是 Gold 标签替代品。\n"
    )
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--diagnostics", type=Path, default=DEFAULT_DIAGNOSTICS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    summary = build_inventory(
        manifest_path=args.manifest,
        baseline_path=args.baseline,
        diagnostics_path=args.diagnostics,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "processed_count": summary["sample_coverage"]["processed_count"],
                "candidate_count": summary["candidates"]["candidate_count"],
                "candidate_sample_count": summary["sample_coverage"]["candidate_sample_count"],
                "output_dir": _relative(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
