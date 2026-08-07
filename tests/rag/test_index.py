from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.rag import LightRagIndex, build_index


class FakeResult:
    def __init__(self, value: object) -> None:
        self.value = value


class FakeClient:
    def __init__(self, *, with_rerank: bool = True) -> None:
        self.embed_calls: list[list[str]] = []
        self.rerank_calls: list[tuple[str, list[str], int]] = []
        if not with_rerank:
            self.rerank = None

    def embed_texts(self, texts: list[str]) -> FakeResult:
        self.embed_calls.append(list(texts))
        vectors = []
        for text in texts:
            if text == "query":
                vectors.append([1.0, 0.0])
            else:
                try:
                    number = int(text.rsplit("-", 1)[-1]) if "-" in text else 0
                except ValueError:
                    number = 0
                vectors.append([float(number + 1), 1.0])
        return FakeResult(np.asarray(vectors, dtype=np.float32))

    def rerank(self, query: str, documents: list[str], *, top_k: int = 8) -> FakeResult:
        self.rerank_calls.append((query, list(documents), top_k))
        ranked = sorted(
            (
                {"index": index, "score": float(len(documents) - index), "text": text}
                for index, text in enumerate(documents)
            ),
            key=lambda item: item["score"],
            reverse=True,
        )
        return FakeResult(ranked[:top_k])


def _entries(count: int = 3) -> list[dict[str, object]]:
    return [
        {
            "id": f"doc-{index}",
            "text": f"doc-{index}",
            "kind": "evidence",
            "sample_id": f"sample-{index}",
            "split": "fit",
            "metadata": {"position": index},
        }
        for index in range(count)
    ]


def test_build_persists_metadata_and_vectors_and_excludes_current_gold(tmp_path: Path) -> None:
    client = FakeClient()
    entries = _entries() + [{"id": "blank", "text": "  ", "kind": "evidence"}]
    entries.append({"id": "same", "text": "same", "kind": "gold", "sample_id": "sample-1"})

    index = build_index(entries, tmp_path, client, exclude_sample_id="sample-1")
    metadata_path = tmp_path / "metadata.jsonl"
    vectors_path = tmp_path / "vectors.npy"

    assert metadata_path.is_file()
    assert vectors_path.is_file()
    metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines()]
    vectors = np.load(vectors_path, allow_pickle=False)
    assert [record["id"] for record in metadata] == ["doc-0", "doc-1", "doc-2"]
    assert vectors.shape == (3, 2)
    assert np.array_equal(vectors, index.vectors)

    loaded = LightRagIndex.load(tmp_path, client=client)
    assert loaded.metadata == metadata
    assert np.array_equal(loaded.vectors, vectors)


def test_fit_only_keeps_fit_labels_but_preserves_non_label_entries(tmp_path: Path) -> None:
    entries = [
        {"id": "fit-label", "text": "fit label", "kind": "label", "split": "fit"},
        {"id": "holdout-label", "text": "holdout label", "kind": "label", "split": "holdout"},
        {"id": "holdout-evidence", "text": "holdout evidence", "kind": "evidence", "split": "holdout"},
    ]
    index = build_index(entries, tmp_path, FakeClient(), fit_only=True)
    assert [record["id"] for record in index.metadata] == ["fit-label", "holdout-evidence"]


def test_build_infers_facility_metadata_from_sample_identity(tmp_path: Path) -> None:
    entries = [
        {
            "id": "underpass-report",
            "text": "顶板和侧墙存在病害。",
            "kind": "evidence",
            "sample_id": "2012年-杨公桥A叉口人行通道",
            "split": "fit",
        }
    ]

    index = build_index(entries, tmp_path, FakeClient())

    assert index.metadata[0]["facility_type"] == "pedestrian_underpass"
    assert index.metadata[0]["facility_noun"] == "人行通道"
    assert index.metadata[0]["component_group"] == "deck_or_slab"


def test_retrieve_uses_top30_top8_then_top6_and_excludes_holdout_gold(tmp_path: Path) -> None:
    entries = _entries(40)
    for index in range(6):
        entries[index]["sample_id"] = "query-1"
    entries.extend(
        [
            {"id": "fit-label", "text": "fit-label", "kind": "label", "sample_id": "fit-1", "split": "fit"},
            {
                "id": "holdout-label",
                "text": "holdout-label",
                "kind": "label",
                "sample_id": "holdout-1",
                "split": "holdout",
            },
            {
                "id": "query-gold",
                "text": "query-gold",
                "kind": "gold",
                "sample_id": "query-1",
                "split": "holdout",
            },
        ]
    )
    client = FakeClient()
    build_index(entries, tmp_path, client)

    result = LightRagIndex.load(tmp_path, client=client).retrieve(
        "query",
        sample_id="query-1",
        split="holdout",
        top_embedding=30,
        top_rerank=8,
        top_k=6,
    )

    assert len(client.rerank_calls) == 1
    _, documents, rerank_top_k = client.rerank_calls[0]
    assert 0 < len(documents) <= 30
    assert rerank_top_k == 8
    assert len(result) == 6
    assert all(item["retrieval_mode"] == "embedding_rerank" for item in result)
    assert "holdout-label" not in {item["id"] for item in result}
    assert "query-gold" not in {item["id"] for item in result}
    assert all(item["sample_id"] == "query-1" for item in result)


def test_missing_reranker_is_explicit_offline_fallback(tmp_path: Path) -> None:
    client = FakeClient(with_rerank=False)
    build_index(_entries(), tmp_path, client)
    result = LightRagIndex.load(tmp_path, client=client).retrieve("query", top_k=2)

    assert len(result) == 2
    assert {item["retrieval_mode"] for item in result} == {"embedding_offline_fallback"}
    assert all(item["rerank_score"] is None for item in result)


def test_retrieve_returns_empty_before_embedding_when_metadata_filter_removes_all(tmp_path: Path) -> None:
    client = FakeClient()
    entries = [{"id": "gold", "text": "gold", "kind": "gold", "sample_id": "query", "split": "holdout"}]
    build_index(entries, tmp_path, client)
    embed_count = len(client.embed_calls)

    result = LightRagIndex.load(tmp_path, client=client).retrieve(
        "query",
        sample_id="query",
        split="holdout",
    )

    assert result == []
    assert len(client.embed_calls) == embed_count


def test_retrieve_source_quota_covers_aliases_and_excludes_current_gold(tmp_path: Path) -> None:
    entries: list[dict[str, object]] = []
    entries.extend(
        {
            "id": f"report-{index}",
            "text": f"report-{index}",
            "kind": "report_evidence",
            "sample_id": "current" if index < 3 else "other",
            "split": "fit",
        }
        for index in range(8)
    )
    entries.extend(
        {
            "id": f"knowledge-{index}",
            "text": f"knowledge-{index}",
            "kind": "domain_knowledge" if index else "knowledge_card",
            "split": "fit",
        }
        for index in range(4)
    )
    entries.extend(
        {"id": f"label-{index}", "text": f"label-{index}", "kind": "label_example", "split": "fit"}
        for index in range(2)
    )
    entries.append(
        {
            "id": "current-gold",
            "text": "current-gold",
            "kind": "gold_label",
            "sample_id": "current",
            "split": "fit",
        }
    )

    client = FakeClient()
    build_index(entries, tmp_path, client)
    result = LightRagIndex.load(tmp_path, client=client).retrieve(
        "query",
        sample_id="current",
        split="fit",
        source_quota={"report_evidence": 3, "knowledge_card": 2, "gold_label": 1},
    )

    kinds = [item["kind"] for item in result]
    assert len(result) == 6
    assert sum(kind == "report_evidence" for kind in kinds) == 3
    assert sum(kind in {"knowledge_card", "domain_knowledge"} for kind in kinds) == 2
    assert sum(kind in {"gold_label", "label_example"} for kind in kinds) == 1
    assert "current-gold" not in {item["id"] for item in result}
    assert not any(item["id"].startswith("report-") and item["sample_id"] != "current" for item in result)
    assert client.rerank_calls[0][2] == 8


def test_retrieve_source_quota_degrades_when_a_source_is_missing(tmp_path: Path) -> None:
    entries = [
        {"id": f"report-{index}", "text": f"report-{index}", "kind": "report_evidence", "split": "fit"}
        for index in range(5)
    ]
    entries.extend(
        {"id": f"knowledge-{index}", "text": f"knowledge-{index}", "kind": "domain_knowledge", "split": "fit"}
        for index in range(2)
    )

    client = FakeClient()
    build_index(entries, tmp_path, client)
    result = LightRagIndex.load(tmp_path, client=client).retrieve("query", source_quota=True)

    kinds = [item["kind"] for item in result]
    assert len(result) == 5
    assert sum(kind == "report_evidence" for kind in kinds) == 3
    assert sum(kind == "domain_knowledge" for kind in kinds) == 2
    assert all(kind not in {"gold_label", "label_example"} for kind in kinds)


def test_retrieve_source_quota_fills_sources_outside_embedding_window(tmp_path: Path) -> None:
    entries = [
        {"id": f"report-{index}", "text": f"report-{index}", "kind": "report_evidence", "split": "fit"}
        for index in range(8)
    ]
    entries.extend(
        [
            {"id": "knowledge-0", "text": "knowledge-0", "kind": "domain_knowledge", "split": "fit"},
            {"id": "knowledge-1", "text": "knowledge-1", "kind": "domain_knowledge", "split": "fit"},
            {"id": "label-0", "text": "label-0", "kind": "label_example", "split": "fit"},
        ]
    )

    client = FakeClient()
    build_index(entries, tmp_path, client)
    result = LightRagIndex.load(tmp_path, client=client).retrieve(
        "query",
        top_embedding=3,
        top_k=6,
        source_quota={"report_evidence": 3, "domain_knowledge": 2, "label_example": 1},
    )

    kinds = [item["kind"] for item in result]
    assert len(result) == 6
    assert sum(kind == "report_evidence" for kind in kinds) == 3
    assert sum(kind == "domain_knowledge" for kind in kinds) == 2
    assert sum(kind == "label_example" for kind in kinds) == 1


def test_source_quota_finds_low_global_rank_sources(tmp_path: Path) -> None:
    entries = [
        {"id": f"report-{index}", "text": f"report-{index}", "kind": "report_evidence", "split": "fit"}
        for index in range(40)
    ]
    entries.extend(
        [
            {"id": "knowledge-0", "text": "knowledge-0", "kind": "knowledge_card", "split": "fit"},
            {"id": "label-0", "text": "label-0", "kind": "gold_label", "split": "fit"},
        ]
    )

    client = FakeClient()
    build_index(entries, tmp_path, client)
    result = LightRagIndex.load(tmp_path, client=client).retrieve("query", source_quota=True)

    ids = {item["id"] for item in result}
    assert "knowledge-0" in ids
    assert "label-0" in ids
    assert sum(item["kind"] == "report_evidence" for item in result) == 3
