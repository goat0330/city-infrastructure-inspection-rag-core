from __future__ import annotations

import json

import numpy as np

from scripts.build_round2_indexes import (
    build_indexes,
    load_base_entries,
    source_accounting,
    validate_index_dir,
)


class FakeEmbeddingClient:
    def embed_texts(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            [[float(len(text)), float(index + 1), 1.0, 0.5] for index, text in enumerate(texts)],
            dtype=np.float32,
        )


def test_load_base_entries_keeps_fit_gold_and_knowledge_only(tmp_path) -> None:
    path = tmp_path / "metadata.jsonl"
    rows = [
        {"kind": "gold_label", "sample_id": "fit-a", "split": "fit", "text": "a"},
        {"kind": "gold_label", "sample_id": "validation-a", "split": "validation", "text": "b"},
        {"kind": "gold_label", "sample_id": "target", "split": "fit", "text": "target gold"},
        {"kind": "knowledge_card", "sample_id": "knowledge-1", "text": "knowledge"},
        {"kind": "report_evidence", "sample_id": "other", "text": "other report"},
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    entries = load_base_entries(path, {"target"})

    assert [row["sample_id"] for row in entries] == ["fit-a", "knowledge-1"]
    assert source_accounting(entries, [{"kind": "report_evidence"}])["total"] == 3


def test_build_indexes_writes_row_aligned_vectors_and_target_evidence(tmp_path) -> None:
    base = [
        {"kind": "gold_label", "sample_id": "fit-a", "split": "fit", "text": "fit example"},
        {"kind": "knowledge_card", "sample_id": "knowledge-1", "text": "domain card"},
    ]
    target = "2013年-12-027杨公桥立交DA-ED匝道桥"
    target_data = {
        target: {
            "split": "validation",
            "source_docx": "2013年/target.docx",
            "report_fact_count": 2,
            "selected_report_evidence_count": 1,
            "selected_sections": ["inspection_conclusion"],
            "entries": [
                {
                    "kind": "report_evidence",
                    "sample_id": target,
                    "id": "report:e1",
                    "text": "target report evidence",
                }
            ],
        }
    }

    built = build_indexes(base, target_data, FakeEmbeddingClient(), tmp_path / "indexes")
    index_dir = tmp_path / "indexes" / target

    assert built[target]["metadata_rows"] == 3
    assert built[target]["vector_shape"] == [3, 4]
    assert validate_index_dir(index_dir) == built[target]
    metadata = [json.loads(line) for line in (index_dir / "metadata.jsonl").read_text(encoding="utf-8").splitlines()]
    assert metadata[-1]["sample_id"] == target
    assert np.isfinite(np.load(index_dir / "vectors.npy", allow_pickle=False)).all()


def test_build_indexes_reuses_matching_base_vectors_without_embedding_base(tmp_path) -> None:
    base = [
        {"id": "gold:a", "kind": "gold_label", "sample_id": "fit-a", "split": "fit", "text": "fit example"},
        {"id": "knowledge:a", "kind": "knowledge_card", "sample_id": "knowledge-1", "text": "domain card"},
    ]
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "metadata.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in base), encoding="utf-8"
    )
    np.save(frozen / "vectors.npy", np.asarray([[1, 2], [3, 4]], dtype=np.float32), allow_pickle=False)

    class TargetOnlyClient:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def embed_texts(self, texts: list[str]) -> np.ndarray:
            self.calls.append(texts)
            return np.asarray([[5.0, 6.0] for _ in texts], dtype=np.float32)

    target = "target"
    client = TargetOnlyClient()
    build_indexes(
        base,
        {
            target: {
                "split": "fit",
                "source_docx": "target.docx",
                "entries": [{"id": "report:e1", "kind": "report_evidence", "sample_id": target, "text": "new evidence"}],
            }
        },
        client,
        tmp_path / "indexes",
        base_index_dir=frozen,
    )

    assert client.calls == [["new evidence"]]
    vectors = np.load(tmp_path / "indexes" / target / "vectors.npy", allow_pickle=False)
    assert vectors.tolist() == [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
