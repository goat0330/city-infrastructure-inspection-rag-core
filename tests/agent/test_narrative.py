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


class QuotaRetriever:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def retrieve(
        self,
        query: str,
        *,
        sample_id: str,
        split: str,
        top_k: int,
        source_quota: dict[str, int],
    ) -> list[dict[str, str]]:
        self.calls.append(
            {
                "query": query,
                "sample_id": sample_id,
                "split": split,
                "top_k": top_k,
                "source_quota": dict(source_quota),
            }
        )
        task = next(task for task in narrative.RETRIEVAL_TASK_FIELDS if f"task={task};" in query)
        return [
            {"evidence_id": f"{task}-report-{index}", "kind": "report_evidence", "text": "报告证据"}
            for index in range(4)
        ] + [
            {"evidence_id": f"{task}-knowledge-{index}", "kind": "domain_knowledge", "text": "专业解释"}
            for index in range(3)
        ] + [
            {"evidence_id": f"{task}-label-{index}", "kind": "label_example", "text": "写法示例"}
            for index in range(2)
        ]


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
        "detailed_conclusion": [
            "经综合评定，报告事实表明桥梁当前技术状况需要关注。",
            "本次报告未提供往年检测评分及病害对比数据，无法开展跨期变化比较。",
            "目前桥面存在裂缝，相关构件状态应结合报告证据进行关注。",
            "综上，桥梁当前状态需要关注，建议按既有建议完成后续处置。",
        ],
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


def test_task_specific_retrieval_evidence_is_valid_without_public_merge() -> None:
    sections = valid_sections()
    sections["causes"][0]["evidence_ids"] = ["causes-knowledge-0"]
    sections["treatments"][0]["evidence_ids"] = ["treatments-knowledge-0"]
    client = FakeClient([sections])
    retriever = QuotaRetriever()

    result = run(client, retriever=retriever)

    assert result["used_fallback"] is False
    assert result["enhanced_prediction"]["causes"][0]["evidence_ids"] == ["causes-knowledge-0"]
    assert len(retriever.calls) == len(narrative.MODEL_GENERATED_FIELDS)
    causes = result["retrieval_by_task"]["causes"]
    assert causes["source_counts"] == {
        "report_evidence": 3,
        "domain_knowledge": 2,
        "label_example": 1,
    }


def test_official_four_paragraphs_require_first_detection_evidence() -> None:
    invalid = valid_sections()
    invalid["detailed_conclusion"][1] = "本次报告为示例桥首次定期检测。"

    result = run(FakeClient([invalid, invalid]))

    assert result["field_results"]["detailed_conclusion"] == "fallback"
    assert any("first-detection" in error for error in result["validation_errors"])


def test_official_narrative_rejects_internal_statistics_and_references() -> None:
    invalid = valid_sections()
    invalid["detailed_conclusion"][2] = "目前表3列示记录3条病害。"

    result = run(FakeClient([invalid, invalid]))

    assert result["field_results"]["detailed_conclusion"] == "fallback"
    assert any("forbidden internal extraction language" in error for error in result["validation_errors"])


def test_model_absence_phrase_is_normalized_to_official_none() -> None:
    sections = valid_sections()
    sections["detailed_conclusion"][1] = "本次报告往年评分未提取到，无法开展跨期变化比较。"

    result = run(FakeClient([sections]), retriever=FakeRetriever())

    assert result["field_results"]["detailed_conclusion"] == "enhanced"
    assert "未提取到" not in result["enhanced_prediction"]["detailed_conclusion"][1]
    assert "无" in result["enhanced_prediction"]["detailed_conclusion"][1]


def test_model_treatment_output_is_ignored_and_baseline_is_preserved() -> None:
    invalid = valid_sections()
    invalid["treatments"][0]["recommendation_index"] = "2"

    result = run(FakeClient([invalid]))

    assert result["field_results"]["treatments"] == "baseline"
    assert result["enhanced_prediction"]["treatments"] == baseline()["treatments"]


def test_deterministic_treatments_are_not_required_in_model_output() -> None:
    sections = valid_sections()
    sections.pop("treatments")

    result = run(FakeClient([sections]), retriever=FakeRetriever())

    assert "treatments must be an array" not in result["validation_errors"]
    assert result["field_results"]["causes"] == "enhanced"
    assert result["field_results"]["safety_impact"] == "enhanced"
    assert result["enhanced_prediction"]["treatments"] == baseline()["treatments"]


def test_validation_failure_falls_back_without_second_full_prompt() -> None:
    invalid = copy.deepcopy(valid_sections())
    invalid["causes"][0]["evidence_ids"] = ["missing-id"]
    client = FakeClient([invalid])

    result = run(client, retriever=FakeRetriever())

    assert result["used_fallback"] is True
    assert result["retry_count"] == 0
    assert result["call_metrics"]["call_count"] == 1
    assert result["field_results"]["causes"] == "fallback"
    assert result["enhanced_prediction"]["causes"] == baseline()["causes"]


def test_invalid_safety_falls_back_only_the_invalid_model_field() -> None:
    invalid = copy.deepcopy(valid_sections())
    invalid["safety_impact"][0]["evidence_ids"] = ["missing-id"]
    client = FakeClient([invalid])
    expected = baseline()

    result = run(client, retriever=FakeRetriever())

    assert result["used_fallback"] is True
    assert result["retry_count"] == 0
    assert result["call_metrics"]["call_count"] == 1
    assert result["enhanced_prediction"]["safety_impact"] == expected["safety_impact"]
    assert result["field_results"]["safety_impact"] == "fallback"
    assert result["field_results"]["causes"] == "enhanced"
    assert result["field_results"]["treatments"] == "baseline"
    assert result["field_fallbacks"] == ["safety_impact"]


def test_explicit_locked_field_change_is_rejected_and_never_merged() -> None:
    invalid = valid_sections()
    invalid["summary"] = {"bridge_name": "被篡改的桥"}
    client = FakeClient([invalid, invalid])

    result = run(client)

    assert result["used_fallback"] is True
    assert result["enhanced_prediction"]["summary"] == baseline()["summary"]
    assert any("locked" in error for error in result["validation_errors"])


@pytest.mark.parametrize(
    ("field", "mutate", "failed_field"),
    [
        (
            "evidence",
            lambda sections: sections["causes"][0].update({"evidence_ids": ["not-in-facts"]}),
            "causes",
        ),
        (
            "paragraph-count",
            lambda sections: sections["detailed_conclusion"].extend(["二", "三", "四", "五"]),
            "detailed_conclusion",
        ),
    ],
)
def test_model_contract_violations_fall_back_by_field(
    field: str, mutate: Any, failed_field: str
) -> None:
    invalid = valid_sections()
    mutate(invalid)
    result = run(FakeClient([invalid]), retriever=FakeRetriever())

    assert result["used_fallback"] is True, field
    assert result["field_results"][failed_field] == "fallback"
    assert result["enhanced_prediction"][failed_field] == baseline()[failed_field]
    assert result["enhanced_prediction"]["treatments"] == baseline()["treatments"]


def test_graph_exposes_the_required_five_nodes() -> None:
    graph = narrative.build_narrative_graph(FakeClient([valid_sections()]))
    node_names = set(graph.get_graph().nodes)
    assert {"prepare_context", "retrieve_knowledge", "generate_narrative", "validate_output", "finalize"} <= node_names


def test_prompt_baseline_is_compact_but_keeps_summary_recommendations_and_full_fallback() -> None:
    expanded = baseline()
    expanded["summary"] = {
        **expanded["summary"],
        "overall_conclusion": "summary-anchor-保留",
        "risk_points": "risk-anchor-保留",
    }
    expanded["defects"] = [
        {
            "index": str(index),
            "location": "桥面",
            "defect_type": "裂缝",
            "description": f"代表病害描述-{index}-" + ("很长" * 30),
            "extra_detail": "不应进入生成 Prompt 的完整病害表字段",
        }
        for index in range(1, 12)
    ]
    expanded["detailed_conclusion"] = ["full-detailed-conclusion-" + ("x" * 500)]
    expanded["causes"] = [{"text": "full-causes-" + ("x" * 500), "evidence_ids": ["fact-1"]}]
    expanded["treatments"] = [{"text": "full-treatments-" + ("x" * 500), "evidence_ids": ["fact-1"]}]
    expanded["safety_impact"] = [{"text": "full-safety-impact-" + ("x" * 500), "evidence_ids": ["fact-1"]}]

    prepared = narrative._prepare_context(
        {
            "baseline_prediction": expanded,
            "sample_id": "bridge-1",
            "source_file": "2024/bridge.docx",
            "report_facts": [],
        },
        max_retries=1,
    )
    prompt_baseline = prepared["prompt_baseline"]
    prompt = narrative._render_prompt(prepared)

    assert prepared["baseline_prediction"] == expanded
    assert prompt_baseline["summary"] == expanded["summary"]
    assert prompt_baseline["recommendations"] == expanded["recommendations"]
    assert len(prompt_baseline["defects"]) == 1
    assert len(prompt_baseline["defects"][0]["representative_descriptions"]) == 1
    assert len(narrative._json_dump(prompt_baseline)) < len(narrative._json_dump(expanded)) * 0.5
    assert "summary-anchor-保留" in prompt
    assert "封闭裂缝" in prompt
    for omitted in (
        "full-detailed-conclusion-",
        "full-causes-",
        "full-treatments-",
        "full-safety-impact-",
        "不应进入生成 Prompt 的完整病害表字段",
    ):
        assert omitted not in prompt


def test_render_prompt_compacts_long_report_and_retrieval_facts() -> None:
    long_text = (
        "prompt-head-marker-"
        + ("裂缝与渗水；" * 600)
        + "-prompt-middle-marker-"
        + ("裂缝与渗水；" * 600)
        + "-prompt-tail-marker"
    )
    rendered = narrative._render_prompt(
        {
            "baseline_prediction": {"summary": {}, "defects": [], "recommendations": []},
            "sample_id": "sample-1",
            "source_file": "sample.docx",
            "report_facts": [{"evidence_id": "fact-1", "section": "defect_table", "text": long_text}],
            "retrieval_results": [{"id": "hit-1", "kind": "domain_knowledge", "text": long_text}],
            "validation_errors": [],
        }
    )

    assert "prompt-middle-marker" not in rendered
    assert len(rendered) < len(long_text)


def test_render_prompt_caps_many_defect_groups() -> None:
    expanded = baseline()
    expanded["defects"] = [
        {
            "location": f"部位-{index}",
            "defect_type": "裂缝",
            "description": f"代表描述-{index}",
        }
        for index in range(80)
    ]
    prepared = narrative._prepare_context(
        {
            "baseline_prediction": expanded,
            "sample_id": "long-sample",
            "source_file": "long.docx",
            "report_facts": [],
        },
        max_retries=1,
    )
    prompt_baseline = prepared["prompt_baseline"]
    assert len(prompt_baseline["defects"]) == narrative._MAX_PROMPT_DEFECT_GROUPS
    assert all(len(item["representative_descriptions"]) <= 2 for item in prompt_baseline["defects"])


def test_render_prompt_exposes_safety_priority_ids() -> None:
    rendered = narrative._render_prompt(
        {
            "baseline_prediction": {"summary": {}, "defects": [], "recommendations": []},
            "report_facts": [
                {
                    "evidence_id": "fact-safety",
                    "section": "safety_assessment",
                    "text": "安全评估：当前影响较小。",
                }
            ],
            "retrieval_results": [],
        }
    )
    assert "fact-safety" in rendered


@dataclass
class FakeFacilityContext:
    facility_name: str
    facility_type: str = "pedestrian_underpass"
    facility_noun: str = "人行通道"

    def to_dict(self) -> dict[str, str]:
        return {
            "facility_name": self.facility_name,
            "facility_type": self.facility_type,
            "facility_noun": self.facility_noun,
        }


@pytest.mark.parametrize(
    ("facility_name", "facility_context"),
    [
        ("杨公桥A叉口人行通道", None),
        ("杨公桥EC匝道人行通道", FakeFacilityContext("杨公桥EC匝道人行通道")),
    ],
)
def test_pedestrian_underpass_fixture_keeps_facility_terms_and_safety_priority(
    facility_name: str, facility_context: Any
) -> None:
    baseline_prediction = {
        "sample_id": "underpass-1",
        "source_file": "underpass.docx",
        "summary": {
            "bridge_name": facility_name,
            "report_date": "2013年2月",
            "overall_score": "86",
            "overall_grade": "一类",
        },
        "defects": [{"index": "1", "location": "侧墙", "description": "侧墙局部破损"}],
        "recommendations": [{"index": "1", "content": "修复侧墙并完善排水设施", "location": "侧墙"}],
        "detailed_conclusion": ["旧结论"],
        "causes": ["旧原因"],
        "treatments": ["旧处置"],
        "safety_impact": ["旧影响"],
    }
    facts = [
        {"evidence_id": "fact-defect", "section": "defect_table", "text": "顶板、侧墙、翼墙、洞口、沉降缝、止水带、排水设施和附属设施存在局部病害。"},
        {"evidence_id": "fact-safety", "section": "safety_assessment", "text": "当前安全评估：病害对通行安全影响较小。"},
        {"evidence_id": "fact-treatment", "section": "treatment_recommendations", "text": "建议修复侧墙并完善排水设施。"},
    ]
    sections = {
        "detailed_conclusion": [
            f"经综合评定，{facility_name}当前技术状况需要关注。",
            "本次报告未提供往年检测评分及病害对比数据，无法开展跨期变化比较。",
            f"目前{facility_name}顶板、侧墙及排水设施存在报告所述病害。",
            f"综上，{facility_name}当前状态需要关注，建议按既有建议完成处置。",
        ],
        "causes": [{"text": "侧墙局部破损与构件状态有关。", "evidence_ids": ["fact-defect"]}],
        "treatments": [{"recommendation_index": "1", "text": "按建议修复侧墙并完善排水设施。", "evidence_ids": ["fact-treatment", "fact-defect"]}],
        "safety_impact": [{"text": "当前病害对通行安全影响较小。", "evidence_ids": ["fact-safety"]}],
    }
    retriever = FakeRetriever()
    result = narrative.run_narrative_enhancement(
        baseline_prediction,
        "underpass-1",
        "underpass.docx",
        facts,
        FakeClient([sections]),
        retriever=retriever,
        facility_context=facility_context,
        field_states={"report_date": "present"},
        locked_facts={"facility_name": facility_name, "recommendation_count": 1},
    )

    assert result["used_fallback"] is False
    enhanced = result["enhanced_prediction"]
    generated_text = " ".join(narrative._candidate_texts(result["generated_sections"]))
    assert "该桥" not in generated_text
    assert "侧墙" in generated_text and "排水设施" in generated_text
    assert [item["evidence_ids"] for item in enhanced["causes"]] == [["fact-defect"]]
    assert enhanced["treatments"] == baseline_prediction["treatments"]
    assert result["field_results"]["treatments"] == "baseline"
    assert len(enhanced["recommendations"]) == len(baseline_prediction["recommendations"])
    assert enhanced["summary"] == baseline_prediction["summary"]
    assert enhanced["defects"] == baseline_prediction["defects"]
    assert all("pedestrian_underpass" in call["query"] for call in retriever.calls)
    assert all("侧墙" in call["query"] and "排水设施" in call["query"] for call in retriever.calls)


def test_normative_tunnel_standard_title_is_not_a_foreign_facility() -> None:
    state = {
        "baseline_prediction": {
            "summary": {"bridge_name": "杨公桥A叉口人行通道"},
            "defects": [],
            "facility_context": {
                "facility_name": "杨公桥A叉口人行通道",
                "facility_type": "pedestrian_underpass",
                "facility_noun": "人行通道",
            },
        },
        "facility_context": {
            "facility_name": "杨公桥A叉口人行通道",
            "facility_type": "pedestrian_underpass",
            "facility_noun": "人行通道",
        },
        "report_facts": [
            {
                "evidence_id": "fact-standard",
                "section": "treatment_recommendations",
                "text": "参照《公路隧道养护技术规范》（JTG H12-2003）开展养护。",
            }
        ],
        "retrieval_results": [],
    }
    candidate = {
        "safety_impact": [
            {
                "text": "参照《公路隧道养护技术规范》（JTG H12-2003）评估当前影响。",
                "evidence_ids": ["fact-standard"],
            }
        ]
    }

    errors = narrative._facility_semantic_errors_by_field(state, candidate)

    assert not any("another facility noun" in message for message in errors["safety_impact"])

    candidate["safety_impact"][0]["text"] = "该隧道主体存在重大安全隐患。"
    errors = narrative._facility_semantic_errors_by_field(state, candidate)
    assert any("another facility noun" in message for message in errors["safety_impact"])


def test_detailed_conclusion_preserves_locked_summary_scores_and_grades() -> None:
    state = {
        "baseline_prediction": {
            "summary": {
                "bridge_name": "桂花新村大桥",
                "overall_score": "86.07",
                "overall_grade": "B级",
                "superstructure_score": "80.00",
                "superstructure_grade": "B级",
                "substructure_score": "95.31",
                "substructure_grade": "A级",
                "deck_score": "74.56",
                "deck_grade": "C级",
            },
            "defects": [],
            "recommendations": [],
            "detailed_conclusion": [
                "经综合评定，总体技术状况评分86.07分、等级为B级；上部结构80.00分（B级），下部结构95.31分（A级），桥面系74.56分（C级）。"
            ],
        },
        "facility_context": {
            "facility_name": "桂花新村大桥",
            "facility_type": "bridge",
            "facility_noun": "桥梁",
        },
        "report_facts": [],
        "retrieval_results": [],
        "generated_sections": {
            "detailed_conclusion": [
                "经综合评定，桂花新村大桥总体技术状况评分86.07分，等级为B级。",
                "本次报告未提供往年检测评分及病害对比数据，无法开展跨期变化比较。",
                "目前，桥梁整体状态良好。",
                "综上，桥梁总体状态良好。",
            ],
            "causes": [],
            "treatments": [],
            "safety_impact": [],
        },
    }

    validation = narrative._validate_output(state)

    assert "detailed_conclusion omits a locked summary score or grade" in validation["validation_errors"]


def test_bridge_baseline_four_paragraphs_are_projected_to_official_prefixes() -> None:
    paragraphs = narrative._official_baseline_detailed_conclusion(
        [
            "经综合评定，桥梁总体状态良好。",
            "检测病害主要表现为局部破损。",
            "该设施承载能力满足设计要求。",
            "综上，建议及时维修。",
        ]
    )

    assert paragraphs == [
        "经综合评定，桥梁总体状态良好。",
        "本次报告检测病害主要表现为局部破损。",
        "目前，该设施承载能力满足设计要求。",
        "综上，建议及时维修。",
    ]


def test_large_bridge_prompt_stays_within_calibrated_budget() -> None:
    expanded = baseline()
    expanded["defects"] = [
        {
            "index": str(index),
            "location": f"第{index}跨梁底",
            "defect_type": "裂缝",
            "description": "梁底存在纵向裂缝并伴有局部渗水泛碱" * 8,
        }
        for index in range(125)
    ]
    expanded["recommendations"] = [
        {
            "index": str(index),
            "category": "尽快维修",
            "location": "梁底",
            "content": "对裂缝进行封闭并处理渗水部位" * 6,
        }
        for index in range(1, 10)
    ]
    facts = [
        {
            "evidence_id": f"fact-{index}",
            "section": "safety_assessment" if index < 2 else "defect_table",
            "text": "当前病害对结构安全影响较小，但需关注耐久性。" * 20,
        }
        for index in range(20)
    ]
    prepared = narrative._prepare_context(
        {
            "baseline_prediction": expanded,
            "sample_id": "large-bridge",
            "source_file": "large.docx",
            "report_facts": facts,
        },
        max_retries=0,
    )
    prompt = narrative._render_prompt(prepared)

    assert len(prompt) < 20000
    assert prompt.count("representative_descriptions") <= narrative._MAX_PROMPT_DEFECT_GROUPS


def test_prompt_exposes_only_components_observed_in_current_report() -> None:
    state = narrative._prepare_context(
        {
            "baseline_prediction": {
                **baseline(),
                "summary": {**baseline()["summary"], "bridge_name": "示例人行通道"},
                "facility_context": {
                    "facility_name": "示例人行通道",
                    "facility_type": "pedestrian_underpass",
                    "facility_noun": "人行通道",
                },
                "defects": [{"location": "顶板", "defect_type": "刮痕", "description": "顶板存在车辆刮痕"}],
            },
            "sample_id": "underpass",
            "source_file": "underpass.docx",
            "report_facts": [{"evidence_id": "f1", "section": "defect_table", "text": "顶板存在车辆刮痕。"}],
        },
        max_retries=0,
    )
    prompt = narrative._render_prompt(state)

    facility_json = prompt.split("设施上下文：", 1)[1].split("\n设施称谓：", 1)[0]
    assert "顶板" in facility_json
    assert "止水带" not in facility_json
    assert "防水层" not in facility_json


def test_safety_items_must_use_current_report_evidence_not_only_knowledge() -> None:
    state = {
        "baseline_prediction": baseline(),
        "facility_context": {"facility_name": "示例桥", "facility_type": "bridge", "facility_noun": "桥梁"},
        "report_facts": [
            {"evidence_id": "safety-1", "section": "safety_assessment", "text": "当前病害对安全影响较小。"},
            {"evidence_id": "defect-1", "section": "defect_table", "text": "桥面存在裂缝。"},
        ],
        "retrieval_results": [{"evidence_id": "knowledge-1", "kind": "knowledge_card", "text": "裂缝可能影响耐久性。"}],
    }
    candidate = {
        "safety_impact": [
            {"text": "桥面裂缝可能影响耐久性。", "evidence_ids": ["knowledge-1"]}
        ]
    }

    errors = narrative._facility_semantic_errors_by_field(state, candidate)

    assert any("current report" in message for message in errors["safety_impact"])


def test_low_impact_report_rejects_unsupported_severe_safety_claim() -> None:
    state = {
        "baseline_prediction": baseline(),
        "facility_context": {"facility_name": "示例桥", "facility_type": "bridge", "facility_noun": "桥梁"},
        "report_facts": [
            {"evidence_id": "safety-1", "section": "safety_assessment", "text": "当前病害对结构安全影响较小。"},
            {"evidence_id": "defect-1", "section": "defect_table", "text": "桥面存在裂缝。"},
        ],
        "retrieval_results": [],
    }
    candidate = {
        "safety_impact": [
            {"text": "桥面裂缝形成重大安全隐患。", "evidence_ids": ["safety-1", "defect-1"]}
        ]
    }

    errors = narrative._facility_semantic_errors_by_field(state, candidate)

    assert any("exaggerates" in message for message in errors["safety_impact"])


def test_good_bridge_baseline_is_kept_without_counting_as_validation_fallback() -> None:
    strong = baseline()
    strong["detailed_conclusion"] = [
        "经综合评定，该桥总体技术状况良好。",
        "本次报告未提供往年检测评分及病害对比数据。",
        "目前，该桥桥面存在局部裂缝。",
        "综上，该桥可正常使用并应按既有建议维修。",
    ]
    strong["causes"] = ["桥面裂缝主要是由于车辆荷载长期作用所致。"]
    strong["safety_impact"] = ["桥面裂缝持续发展可能影响耐久性。"]
    candidate = valid_sections()
    result = narrative.run_narrative_enhancement(
        strong,
        sample_id="bridge-strong",
        source_file="bridge.docx",
        report_facts=[{"evidence_id": "fact-1", "section": "defect_table", "text": "桥面存在裂缝。"}],
        client=FakeClient([candidate]),
        facility_context={"facility_name": "示例桥", "facility_type": "bridge", "facility_noun": "桥梁"},
    )

    assert result["used_fallback"] is False
    assert result["field_results"]["detailed_conclusion"] == "baseline"
    assert result["enhanced_prediction"]["detailed_conclusion"] == strong["detailed_conclusion"]
    assert result["field_results"]["treatments"] == "baseline"
