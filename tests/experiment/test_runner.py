from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from scripts import run_narrative_enhancement as runner
import src.agent.narrative as narrative
from tests.fixtures.word.ooxml_factory import paragraph, write_docx


def test_offline_runner_writes_a_b_c_d_artifacts(tmp_path: Path) -> None:
    source = write_docx(tmp_path / "实验桥.docx", paragraph("检测结论：桥面存在裂缝。"))
    output = tmp_path / "run"
    summary = runner.run_experiment(source, output, sample_id="sample-1", offline=True)
    assert summary["status"] == "offline"
    assert summary["offline"] is True
    for name in ("baseline_prediction.json", "enhanced_prediction.json", "retrieval_trace.json", "ab_results.json", "experiment_summary.json"):
        assert (output / name).is_file()
    ab = json.loads((output / "ab_results.json").read_text(encoding="utf-8"))
    assert set(ab["groups"]) == {"A", "B", "C", "D"}
    assert "evidence_id_validity" in ab["groups"]["D"]


def test_missing_real_configuration_does_not_write_enhanced(tmp_path: Path, monkeypatch) -> None:
    source = write_docx(tmp_path / "实验桥.docx", paragraph("检测结论：桥面存在裂缝。"))
    for name in tuple(name for name in __import__("os").environ if name.startswith("IAIC_")):
        monkeypatch.delenv(name, raising=False)
    output = tmp_path / "missing"
    summary = runner.run_experiment(source, output)
    assert summary["status"] == "configuration_error"
    assert not (output / "enhanced_prediction.json").exists()


def test_text_values_recurses_through_all_values_of_each_target_field() -> None:
    fields = {
        field: {"outer": {"arbitrary_key": f"new-{field}"}}
        for field in runner.TARGET_FIELDS
    }
    baseline = {
        field: {"outer": {"arbitrary_key": f"old-{field}"}}
        for field in runner.TARGET_FIELDS
    }

    record = runner._group_record(
        "B",
        "test",
        fields,
        baseline,
        [],
        [],
        {"calls": 0},
    )

    assert record["has_new_facts"] is True
    assert set(record["new_facts"]) == {f"new-{field}" for field in runner.TARGET_FIELDS}


def test_narrative_prompt_uses_the_compact_baseline() -> None:
    baseline = {
        "sample_id": "sample-1",
        "source_file": "sample.docx",
        "summary": {"bridge_name": "示例桥", "overall_conclusion": "总体结论"},
        "defects": [
            {"location": "桥面", "defect_type": "裂缝", "description": "裂缝描述"},
        ],
        "recommendations": [{"index": "1", "content": "封闭裂缝", "location": "桥面"}],
    }
    baseline.update(
        {
            field: [{"text": f"baseline-{field}"}]
            for field in runner.TARGET_FIELDS
        }
    )

    compact = narrative._prompt_baseline(baseline)

    assert all(field not in compact for field in runner.TARGET_FIELDS)
    assert compact["summary"] == baseline["summary"]
    assert compact["recommendations"] == baseline["recommendations"]
    assert compact["defects"][0]["representative_descriptions"] == ["裂缝描述"]

    rendered = narrative._render_prompt(
        {
            "baseline_prediction": baseline,
            "sample_id": "sample-1",
            "source_file": "sample.docx",
            "report_facts": [],
            "retrieval_results": [],
            "validation_errors": [],
        }
    )
    assert "baseline-detailed_conclusion" not in rendered
    assert "封闭裂缝" in rendered


def test_b_prompt_keeps_its_ablation_baseline_contract() -> None:
    baseline = {
        field: [{"text": f"baseline-{field}"}]
        for field in runner.TARGET_FIELDS
    }

    payload = json.loads(runner._prompt("B", baseline, [])[1]["content"])

    assert set(payload["baseline_prediction"]) == set(runner.TARGET_FIELDS)
    assert payload["report_facts"] == []


def test_task_queries_are_independent_and_retrieval_hits_use_global_source_quotas() -> None:
    baseline = {
        "sample_id": "sample-1",
        "summary": {
            "bridge_name": "示例桥",
            "overall_conclusion": "总体结论",
            "risk_points": "安全风险",
        },
        "defects": [
            {"location": "桥面", "defect_type": "裂缝", "description": "裂缝描述"}
        ],
        "recommendations": [{"index": "1", "content": "封闭裂缝", "location": "桥面"}],
    }
    queries = runner._task_queries(baseline)

    assert tuple(queries) == runner.TARGET_FIELDS
    assert len(set(queries.values())) == len(runner.TARGET_FIELDS)
    assert "封闭裂缝" in queries["treatments"]
    assert "裂缝描述" in queries["causes"]

    task_hits = {
        "detailed_conclusion": [
            {"id": "r1", "kind": "report_evidence", "text": "r1"},
            {"id": "r2", "kind": "report_evidence", "text": "r2"},
            {"id": "r3", "kind": "report_evidence", "text": "r3"},
            {"id": "r4", "kind": "report_evidence", "text": "r4"},
        ],
        "causes": [
            {"id": "r1", "kind": "report_evidence", "text": "duplicate"},
            {"id": "k1", "kind": "knowledge_card", "text": "k1"},
            {"id": "d1", "kind": "domain_knowledge", "text": "d1"},
            {"id": "d2", "kind": "domain_knowledge", "text": "d2"},
        ],
        "treatments": [
            {"id": "g1", "kind": "gold_label", "text": "g1"},
            {"id": "l1", "kind": "label_example", "text": "l1"},
        ],
        "safety_impact": [],
    }

    hits = runner._merge_retrieval_hits(task_hits)
    ids = [hit["id"] for hit in hits]
    assert len(ids) == len(set(ids))
    assert sum(runner._retrieval_source_bucket(hit) == "report_evidence" for hit in hits) <= 3
    assert sum(runner._retrieval_source_bucket(hit) == "knowledge_card" for hit in hits) <= 2
    assert sum(runner._retrieval_source_bucket(hit) == "gold_label" for hit in hits) <= 1
    assert "r4" not in ids
    assert "l1" not in ids


def test_real_runner_records_task_queries_and_final_d_hits(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "实验桥.docx"
    source.write_bytes(b"placeholder")
    output = tmp_path / "run"
    baseline = {
        "sample_id": "sample-1",
        "source_file": "实验桥.docx",
        "summary": {"bridge_name": "示例桥", "overall_conclusion": "总体结论"},
        "defects": [{"location": "桥面", "defect_type": "裂缝", "description": "裂缝"}],
        "recommendations": [{"index": "1", "content": "封闭裂缝", "location": "桥面"}],
        "detailed_conclusion": ["旧结论"],
        "causes": ["旧原因"],
        "treatments": ["旧处置"],
        "safety_impact": ["旧影响"],
    }
    facts = [{"evidence_id": "fact-1", "text": "桥面存在裂缝。", "section": "defect_table"}]

    class FakeModel:
        def chat_json(self, *_args, **_kwargs):
            return runner.ModelCallResult(
                value={
                    "detailed_conclusion": ["事实表明需要关注。"],
                    "causes": [{"text": "与构件状态有关。", "evidence_ids": ["fact-1"]}],
                    "treatments": [],
                    "safety_impact": [{"text": "可能影响通行安全。", "evidence_ids": ["fact-1"]}],
                },
                model="fake-model",
                duration_ms=0.1,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
            )

    class FakeIndex:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def retrieve(self, query: str, **kwargs):
            self.calls.append({"query": query, **kwargs})
            return [
                {"id": "r1", "kind": "report_evidence", "text": "r1"},
                {"id": "r2", "kind": "report_evidence", "text": "r2"},
                {"id": "r3", "kind": "report_evidence", "text": "r3"},
                {"id": "r4", "kind": "report_evidence", "text": "r4"},
                {"id": "k1", "kind": "knowledge_card", "text": "k1"},
                {"id": "d1", "kind": "domain_knowledge", "text": "d1"},
                {"id": "g1", "kind": "gold_label", "text": "g1"},
                {"id": "l1", "kind": "label_example", "text": "l1"},
            ]

    fake_index = FakeIndex()
    monkeypatch.setattr(runner, "_load_baseline", lambda *_args: deepcopy(baseline))
    monkeypatch.setattr(runner, "_report_facts", lambda *_args: deepcopy(facts))
    monkeypatch.setattr(runner, "_load_real_client", lambda: FakeModel())
    monkeypatch.setattr(
        runner.LightRagIndex,
        "load",
        classmethod(lambda _cls, *_args, **_kwargs: fake_index),
    )

    summary = runner.run_experiment(source, output, index_dir=tmp_path / "index")

    assert summary["status"] == "succeeded"
    trace = json.loads((output / "retrieval_trace.json").read_text(encoding="utf-8"))
    assert set(trace["task_queries"]) == set(runner.TARGET_FIELDS)
    assert [call["query"] for call in fake_index.calls] == [
        trace["task_queries"][field] for field in runner.TARGET_FIELDS
    ]
    assert all(call["source_quota"] == runner.RETRIEVAL_SOURCE_QUOTA for call in fake_index.calls)
    assert trace["hits"] == trace["retrieval_hits"]
    assert len(trace["hits"]) == 6

    ab = json.loads((output / "ab_results.json").read_text(encoding="utf-8"))
    assert ab["groups"]["D"]["retrieval_trace"]["task_queries"] == trace["task_queries"]
    assert ab["groups"]["D"]["retrieval_trace"]["hits"] == trace["hits"]


def test_group_record_checks_strings_in_all_top_level_target_fields() -> None:
    baseline = {field: [f"baseline-{field}"] for field in runner.TARGET_FIELDS}
    fields = {field: f"new-{field}" for field in runner.TARGET_FIELDS}

    record = runner._group_record(
        "B",
        "test",
        fields,
        baseline,
        [],
        [],
        {},
    )

    assert runner._text_values(fields) == list(fields.values())
    assert record["has_new_facts"] is True
    assert set(record["new_facts"]) == set(fields.values())

    baseline_record = runner._group_record(
        "A",
        "baseline",
        baseline,
        baseline,
        [],
        [],
        {},
    )
    assert baseline_record["has_new_facts"] is False


def test_group_record_accepts_paraphrase_with_valid_item_evidence() -> None:
    baseline = {field: [] for field in runner.TARGET_FIELDS}
    facts = [{"evidence_id": "fact-1", "text": "报告记录桥面裂缝。"}]
    fields = {
        "detailed_conclusion": ["桥面状态需要关注。"],
        "causes": [{"text": "裂缝与构件状态有关。", "evidence_ids": ["fact-1"]}],
        "treatments": [],
        "safety_impact": [],
    }

    record = runner._group_record("C", "evidence", fields, baseline, facts, [], {})

    assert record["evidence_id_valid"] is True
    assert record["has_new_facts"] is True
    assert "裂缝与构件状态有关。" not in record["new_facts"]
