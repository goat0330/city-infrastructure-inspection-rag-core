from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import pytest

import src.agent.narrative as narrative


@pytest.fixture(autouse=True)
def shared_client_types_are_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sibling llm-client worktree is merged by the parent worker."""

    monkeypatch.setattr(narrative, "_load_llm_types", lambda: (object, object))


@dataclass
class FakeResult:
    value: Any
    duration_ms: float = 12.5
    prompt_tokens: int = 7
    completion_tokens: int = 11
    total_tokens: int = 18
    model: str = "fake-model"


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def chat_json(self, messages: list[dict[str, str]]) -> FakeResult:
        assert [message["role"] for message in messages] == ["system", "user"]
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResult(response)


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def retrieve(self, query: str, *, sample_id: str, split: str, top_k: int) -> list[dict[str, str]]:
        self.calls.append({"query": query, "sample_id": sample_id, "split": split, "top_k": top_k})
        return [{"evidence_id": "rag-1", "text": "资料支持常规巡检。"}]


def baseline() -> dict[str, Any]:
    return {
        "sample_id": "bridge-1",
        "source_file": "2024/bridge.docx",
        "schema_version": "prediction-v1",
        "summary": {
            "bridge_name": "示例桥",
            "report_date": "2024年5月1日",
            "overall_score": "86.5",
            "overall_grade": "B级",
            "previous_overall_score": "84.0",
            "previous_overall_grade": "B级",
        },
        "defects": [{"index": "1", "location": "桥面", "description": "出现裂缝"}],
        "recommendations": [{"index": "1", "content": "封闭裂缝", "location": "桥面"}],
        "history": {"previous": "病害稳定"},
        "detailed_conclusion": ["确定性结论"],
        "causes": ["确定性原因"],
        "treatments": ["确定性处置"],
        "safety_impact": ["确定性影响"],
    }


def valid_sections() -> dict[str, Any]:
    return {
        "detailed_conclusion": ["报告事实表明桥梁当前病害需要关注。"],
        "causes": [{"text": "病害与既有构件状态有关。", "evidence_ids": ["fact-1"]}],
        "treatments": [
            {
                "recommendation_index": "1",
                "text": "按原建议完成处置。",
                "evidence_ids": ["fact-1", "rag-1"],
            }
        ],
        "safety_impact": [{"text": "病害可能影响通行安全。", "evidence_ids": ["fact-1"]}],
    }


def run(client: FakeClient, *, retriever: Any = None) -> dict[str, Any]:
    return narrative.run_narrative_enhancement(
        baseline(),
        sample_id="bridge-1",
        source_file="2024/bridge.docx",
        report_facts=[{"evidence_id": "fact-1", "text": "桥面存在裂缝。"}],
        client=client,
        retriever=retriever,
        split="fit",
    )


def test_success_retrieves_context_and_enhances_only_four_fields() -> None:
    client = FakeClient([valid_sections()])
    retriever = FakeRetriever()
    result = run(client, retriever=retriever)

    assert result["used_fallback"] is False
    assert result["retry_count"] == 0
    assert result["validation_errors"] == []
    assert result["call_metrics"]["call_count"] == 1
    assert result["call_metrics"]["prompt_tokens"] == 7
    assert result["call_metrics"]["completion_tokens"] == 11
    assert result["call_metrics"]["total_tokens"] == 18
    assert result["call_metrics"]["latency_ms"] == 12.5
    assert result["call_metrics"]["duration_ms"] == 12.5
    assert result["call_metrics"]["model"] == "fake-model"
    assert result["retrieval_results"][0]["evidence_id"] == "rag-1"
    assert retriever.calls[0]["sample_id"] == "bridge-1"
    assert retriever.calls[0]["split"] == "fit"
    assert "桥面存在裂缝" in client.prompts[0]
    assert "资料支持常规巡检" in client.prompts[0]

    enhanced = result["enhanced_prediction"]
    for key, value in baseline().items():
        if key not in narrative.ENHANCED_FIELDS:
            assert enhanced[key] == value
    assert enhanced["causes"][0]["evidence_ids"] == ["fact-1"]


def test_one_validation_failure_is_sent_to_retry_and_then_succeeds() -> None:
    invalid = copy.deepcopy(valid_sections())
    invalid["causes"][0]["evidence_ids"] = ["missing-id"]
    client = FakeClient([invalid, valid_sections()])

    result = run(client, retriever=FakeRetriever())

    assert result["used_fallback"] is False
    assert result["retry_count"] == 1
    assert result["call_metrics"]["call_count"] == 2
    assert result["validation_errors"] == []
    assert "unknown evidence_id" in client.prompts[1]


def test_second_failure_returns_exact_baseline_fallback() -> None:
    invalid = copy.deepcopy(valid_sections())
    invalid["safety_impact"][0]["evidence_ids"] = ["missing-id"]
    client = FakeClient([invalid, invalid])
    expected = baseline()

    result = run(client)

    assert result["used_fallback"] is True
    assert result["retry_count"] == 1
    assert result["call_metrics"]["call_count"] == 2
    assert result["enhanced_prediction"] == expected
    assert result["validation_errors"]


def test_explicit_locked_field_change_is_rejected_and_never_merged() -> None:
    invalid = valid_sections()
    invalid["summary"] = {"bridge_name": "被篡改的桥"}
    client = FakeClient([invalid, invalid])

    result = run(client)

    assert result["used_fallback"] is True
    assert result["enhanced_prediction"]["summary"] == baseline()["summary"]
    assert any("locked" in error for error in result["validation_errors"])


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        (
            "evidence",
            lambda sections: sections["causes"][0].update({"evidence_ids": ["not-in-facts"]}),
        ),
        (
            "treatment-count",
            lambda sections: sections["treatments"].append(
                {"recommendation_index": "2", "text": "再次处置。", "evidence_ids": ["fact-1"]}
            ),
        ),
        (
            "paragraph-count",
            lambda sections: sections["detailed_conclusion"].extend(["二", "三", "四", "五"]),
        ),
    ],
)
def test_contract_violations_are_rejected(field: str, mutate: Any) -> None:
    invalid = valid_sections()
    mutate(invalid)
    client = FakeClient([invalid, invalid])

    result = run(client)

    assert result["used_fallback"] is True, field
    assert result["enhanced_prediction"] == baseline()
    assert result["validation_errors"]


def test_graph_exposes_the_required_five_nodes() -> None:
    graph = narrative.build_narrative_graph(FakeClient([valid_sections()]))
    node_names = set(graph.get_graph().nodes)
    assert {"prepare_context", "retrieve_knowledge", "generate_narrative", "validate_output", "finalize"} <= node_names
