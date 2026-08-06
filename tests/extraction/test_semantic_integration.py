from __future__ import annotations

from copy import deepcopy

from src.contracts.semantic_extraction import ExtractionCandidate
from src.extraction.semantic_candidates import build_semantic_candidates
from src.extraction.semantic_merge import merge_semantic_predictions


def _baseline() -> dict[str, object]:
    return {
        "sample_id": "sample-1",
        "source_file": "sample-1.docx",
        "schema_version": "prediction-v1",
        "summary": {
            "bridge_name": "示例人行通道",
            "overall_conclusion": "原结论",
            "risk_points": "原风险",
        },
        "defects": [{"description": "侧墙裂缝", "location": "侧墙", "defect_type": "裂缝"}],
        "recommendations": [{"content": "修复裂缝", "category": "尽快维修"}],
        "detailed_conclusion": ["原详细结论"],
        "causes": ["原成因"],
        "treatments": ["原处置"],
        "safety_impact": ["原安全影响"],
    }


def test_candidate_builder_only_selects_diagnostic_candidates() -> None:
    baseline = _baseline()
    candidates = build_semantic_candidates(
        baseline,
        {"quality_flags": [{"code": "recommendation_category_unresolved", "index": 0}]},
        [{"evidence_id": "docx:1", "text": "修复裂缝"}],
    )
    assert len(candidates) == 1
    assert candidates[0].task_type == "recommendation_category"
    assert candidates[0].context["recommendation_index"] == 0

    code_candidates = build_semantic_candidates(
        baseline,
        {"quality_flag_codes": ["recommendation_category_unresolved"]},
    )
    assert code_candidates
    assert all(item.task_type == "recommendation_category" for item in code_candidates)


def test_candidate_builder_skips_status_prose_as_recommendation() -> None:
    baseline = _baseline()
    baseline["recommendations"] = [
        {"content": "人行通道长69m、宽3.8m，结构尺寸与竣工图基本相符", "category": "尽快维修"}
    ]
    candidates = build_semantic_candidates(
        baseline,
        {"quality_flags": [{"code": "recommendation_category_unresolved", "index": 0}]},
    )
    assert candidates == []


def test_disabled_merge_is_exact_baseline() -> None:
    baseline = _baseline()
    original = deepcopy(baseline)
    merged, trace = merge_semantic_predictions(
        baseline,
        [],
        semantic_enabled=False,
    )
    assert merged == original
    assert trace["semantic_enabled"] is False


def test_invalid_decision_falls_back_and_locked_fields_stay_equal() -> None:
    baseline = _baseline()
    candidates = build_semantic_candidates(
        baseline,
        {"quality_flags": [{"code": "recommendation_category_unresolved", "index": 0}]},
    )
    decisions = [
        {
            "candidate_id": candidates[0].candidate_id,
            "task_type": "recommendation_category",
            "decision": "resolved",
            "evidence_ids": ["unknown"],
            "confidence": 0.9,
            "selection_reason": "bad source",
            "result": {"category": "预防性养护"},
        }
    ]
    merged, trace = merge_semantic_predictions(
        baseline,
        [item.to_dict() for item in candidates],
        decisions,
        semantic_enabled=True,
    )
    assert merged["defects"] == baseline["defects"]
    assert merged["detailed_conclusion"] == baseline["detailed_conclusion"]
    assert merged["recommendations"] == baseline["recommendations"]
    assert "recommendations" in trace["fallback_fields"]
    assert trace["validation_errors"]


def test_valid_recommendation_decision_only_changes_category() -> None:
    baseline = _baseline()
    candidates = build_semantic_candidates(
        baseline,
        {"quality_flags": [{"code": "recommendation_category_unresolved", "index": 0}]},
    )
    evidence = candidates[0].evidence_ids[0]
    decisions = [
        {
            "candidate_id": candidates[0].candidate_id,
            "task_type": "recommendation_category",
            "decision": "resolved",
            "evidence_ids": [evidence],
            "confidence": 0.9,
            "selection_reason": "原文建议",
            "result": {"category": "预防性养护"},
        }
    ]
    merged, trace = merge_semantic_predictions(
        baseline,
        [item.to_dict() for item in candidates],
        decisions,
        semantic_enabled=True,
    )
    assert merged["recommendations"][0]["category"] == "预防性养护"
    assert merged["recommendations"][0]["content"] == baseline["recommendations"][0]["content"]
    assert merged["defects"] == baseline["defects"]
    assert trace["completed_candidate_ids"] == [candidates[0].candidate_id]


def test_semantic_off_does_not_touch_runtime_adapters() -> None:
    baseline = _baseline()
    candidate = build_semantic_candidates(
        baseline,
        {"quality_flags": ["recommendation_category_unresolved"]},
    )

    def fail(*_args, **_kwargs):
        raise AssertionError("semantic adapter must not run while disabled")

    merged, trace = merge_semantic_predictions(
        baseline,
        [item.to_dict() for item in candidate],
        semantic_enabled=False,
        retriever=fail,
        decider=fail,
        client=fail,
        index=fail,
    )

    assert merged == baseline
    assert trace["semantic_enabled"] is False
    assert trace["used_fallback"] is False


def test_injected_retriever_and_decider_run_candidate_to_merge() -> None:
    baseline = _baseline()
    candidate = ExtractionCandidate(
        candidate_id="sample-1:recommendation:0",
        sample_id="sample-1",
        task_type="recommendation_category",
        source_text="修复裂缝",
        evidence_ids=("report:recommendation",),
        facility_context={"facility_type": "bridge"},
        context={"recommendation_index": 0},
    )
    calls: list[str] = []

    def retrieve(value,):
        calls.append(f"retrieve:{value.candidate_id}")
        return [{"evidence_id": "rag:1", "facility_type": "bridge", "text": "证据"}]

    def decide(value, evidence):
        calls.append(f"decide:{value.candidate_id}:{evidence[0]['evidence_id']}")
        return {
            "candidate_id": value.candidate_id,
            "task_type": value.task_type,
            "decision": "resolved",
            "evidence_ids": ["rag:1"],
            "confidence": 0.9,
            "selection_reason": "报告证据支持",
            "result": {"category": "预防性养护"},
        }

    merged, trace = merge_semantic_predictions(
        baseline,
        [candidate.to_dict()],
        semantic_enabled=True,
        retriever=retrieve,
        decider=decide,
    )

    assert calls == [
        "retrieve:sample-1:recommendation:0",
        "decide:sample-1:recommendation:0:rag:1",
    ]
    assert merged["recommendations"][0]["category"] == "预防性养护"
    assert trace["completed_candidate_ids"] == [candidate.candidate_id]
    assert trace["fallback_fields"] == []


def test_live_adapter_reuses_index_quota_and_qwen_compatible_client() -> None:
    baseline = _baseline()
    candidate = ExtractionCandidate(
        candidate_id="sample-1:recommendation:0",
        sample_id="sample-1",
        task_type="recommendation_category",
        source_text="修复裂缝",
        evidence_ids=("report:recommendation",),
        facility_context={"facility_type": "bridge"},
        context={"recommendation_index": 0},
    )

    class FakeIndex:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def retrieve(self, query, **kwargs):
            self.calls.append({"query": query, **kwargs})
            return [{"evidence_id": "rag:1", "facility_type": "bridge", "text": "证据"}]

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, _messages, *, max_tokens):
            self.calls += 1
            return {
                "candidate_id": candidate.candidate_id,
                "task_type": candidate.task_type,
                "decision": "resolved",
                "evidence_ids": ["rag:1"],
                "confidence": 0.9,
                "selection_reason": "报告证据支持",
                "result": {"category": "预防性养护"},
            }

    index = FakeIndex()
    client = FakeClient()
    merged, trace = merge_semantic_predictions(
        baseline,
        [candidate.to_dict()],
        semantic_enabled=True,
        index=index,
        client=client,
        split="fit",
    )

    assert client.calls == 1
    assert index.calls[0]["source_quota"] is True
    assert index.calls[0]["facility_type"] == "bridge"
    assert index.calls[0]["split"] == "fit"
    assert merged["recommendations"][0]["category"] == "预防性养护"
    assert trace["fallback_fields"] == []


def test_unresolved_live_decision_and_facility_mismatch_fall_back() -> None:
    baseline = _baseline()
    candidate = ExtractionCandidate(
        candidate_id="sample-1:recommendation:0",
        sample_id="sample-1",
        task_type="recommendation_category",
        source_text="修复裂缝",
        evidence_ids=("report:recommendation",),
        facility_context={"facility_type": "bridge"},
        context={"recommendation_index": 0},
    )

    def mismatched_retriever(_candidate):
        return [{"evidence_id": "rag:wrong", "facility_type": "tunnel", "text": "错误设施"}]

    def resolved_decider(value, _evidence):
        return {
            "candidate_id": value.candidate_id,
            "task_type": value.task_type,
            "decision": "resolved",
            "evidence_ids": ["rag:wrong"],
            "confidence": 0.9,
            "selection_reason": "不应合并",
            "result": {"category": "预防性养护"},
        }

    merged, trace = merge_semantic_predictions(
        baseline,
        [candidate.to_dict()],
        semantic_enabled=True,
        retriever=mismatched_retriever,
        decider=resolved_decider,
    )

    assert merged == baseline
    assert trace["used_fallback"] is True
    assert set(trace["fallback_fields"]) == {
        "recommendations",
        "summary.recommendations_summary",
    }
    assert any(
        "facility type mismatch" in item["reason"]
        for item in trace["fallback_reasons"]
    )


def test_live_client_failure_falls_back_without_leaking_exception_text() -> None:
    baseline = _baseline()
    candidate = ExtractionCandidate(
        candidate_id="sample-1:recommendation:0",
        sample_id="sample-1",
        task_type="recommendation_category",
        source_text="修复裂缝",
        evidence_ids=("report:recommendation",),
        facility_context={"facility_type": "bridge"},
        context={"recommendation_index": 0},
    )

    class FakeIndex:
        def retrieve(self, _query, **_kwargs):
            return [{"evidence_id": "rag:1", "facility_type": "bridge", "text": "证据"}]

    class FailingClient:
        def chat_json(self, _messages, *, max_tokens):
            raise TimeoutError("provider timeout")

    merged, trace = merge_semantic_predictions(
        baseline,
        [candidate.to_dict()],
        semantic_enabled=True,
        index=FakeIndex(),
        client=FailingClient(),
    )

    assert merged == baseline
    assert trace["used_fallback"] is True
    assert any("TimeoutError" in item["reason"] for item in trace["fallback_reasons"])
    assert all("provider timeout" not in item["reason"] for item in trace["fallback_reasons"])


def test_semantic_projection_preserves_all_protected_prediction_fields() -> None:
    baseline = _baseline()
    candidate = ExtractionCandidate(
        candidate_id="sample-1:recommendation:0",
        sample_id="sample-1",
        task_type="recommendation_category",
        source_text="修复裂缝",
        evidence_ids=("report:recommendation",),
        context={"recommendation_index": 0},
    )

    def decide(value, _evidence):
        return {
            "candidate_id": value.candidate_id,
            "task_type": value.task_type,
            "decision": "resolved",
            "evidence_ids": ["report:recommendation"],
            "confidence": 1.0,
            "selection_reason": "只改类别",
            "result": {
                "category": "预防性养护",
                "content": "模型不应覆盖建议原文",
                "location": "模型不应覆盖部位",
            },
            "source_file": "模型不应覆盖文件名",
        }

    merged, _trace = merge_semantic_predictions(
        baseline,
        [candidate.to_dict()],
        semantic_enabled=True,
        decider=decide,
    )

    assert merged["sample_id"] == baseline["sample_id"]
    assert merged["source_file"] == baseline["source_file"]
    assert merged["summary"] == baseline["summary"]
    assert merged["defects"] == baseline["defects"]
    assert merged["recommendations"][0]["category"] == "预防性养护"
    assert merged["recommendations"][0]["content"] == baseline["recommendations"][0]["content"]
    assert "location" not in merged["recommendations"][0]
