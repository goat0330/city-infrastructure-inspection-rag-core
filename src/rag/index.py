"""A lightweight embedding and reranker backed retrieval index.

The on-disk format is intentionally small: one JSON object per line in
``metadata.jsonl`` and a row-aligned NumPy array in ``vectors.npy``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import json
from pathlib import Path
from typing import Any

import numpy as np


METADATA_FILENAME = "metadata.jsonl"
VECTORS_FILENAME = "vectors.npy"
_LABEL_KINDS = {"gold", "gold_label", "label", "label_example"}
_HOLDOUT_SPLITS = {"holdout", "test", "val", "validation"}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _kind(record: Mapping[str, Any]) -> str:
    return _text(record.get("kind")).lower()


def _is_label(record: Mapping[str, Any]) -> bool:
    value = _kind(record)
    return value in _LABEL_KINDS or value.startswith("gold:") or value.startswith("label:")


def _same_sample(record: Mapping[str, Any], sample_id: object) -> bool:
    if sample_id is None or record.get("sample_id") is None:
        return False
    return _text(record.get("sample_id")) == _text(sample_id)


def _fit_only_records(
    entries: Iterable[Mapping[str, Any]],
    *,
    exclude_sample_id: object = None,
    fit_only: bool = False,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError("each RAG entry must be a JSON object")
        record = dict(entry)
        if not _text(record.get("text")):
            continue
        if "id" not in record or not _text(record.get("id")):
            raise ValueError("each RAG entry must have a non-empty id")
        if "kind" not in record or not _text(record.get("kind")):
            raise ValueError("each RAG entry must have a non-empty kind")
        if exclude_sample_id is not None and _same_sample(record, exclude_sample_id):
            continue
        if fit_only and _is_label(record) and _text(record.get("split")).lower() != "fit":
            continue
        record["text"] = _text(record["text"])
        records.append(record)
    return records


def _result_value(result: object) -> object:
    value = getattr(result, "value", result)
    if value is None:
        raise ValueError("model client returned no value")
    return value


def _embedding_matrix(value: object, count: int) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    if count == 0:
        return np.empty((0, 0), dtype=np.float32)
    if array.ndim == 1 and count == 1:
        array = array.reshape(1, -1)
    if array.ndim != 2 or array.shape[0] != count or array.shape[1] == 0:
        raise ValueError(f"embedding shape must be ({count}, dimension), got {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError("embedding vectors must contain only finite values")
    return np.ascontiguousarray(array, dtype=np.float32)


def _query_vector(value: object) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    if array.ndim == 2 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 1 or array.shape[0] == 0:
        raise ValueError(f"query embedding must have shape (dimension,), got {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError("query embedding must contain only finite values")
    return array


def _cosine_scores(vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
    if vectors.shape[1] != query.shape[0]:
        raise ValueError(
            "query embedding dimension does not match the index "
            f"({query.shape[0]} != {vectors.shape[1]})"
        )
    vector_norms = np.linalg.norm(vectors, axis=1)
    query_norm = float(np.linalg.norm(query))
    denominator = vector_norms * query_norm
    scores = np.zeros(vectors.shape[0], dtype=np.float32)
    np.divide(vectors @ query, denominator, out=scores, where=denominator != 0)
    return scores


def _write_metadata(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def build_index(
    entries: Iterable[Mapping[str, Any]],
    output_dir: str | Path,
    client: Any,
    exclude_sample_id: str | None = None,
    fit_only: bool = False,
) -> "LightRagIndex":
    """Build and persist a row-aligned JSONL/NumPy retrieval index."""

    records = _fit_only_records(
        entries,
        exclude_sample_id=exclude_sample_id,
        fit_only=fit_only,
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    if records:
        result = client.embed_texts([record["text"] for record in records])
        vectors = _embedding_matrix(_result_value(result), len(records))
    else:
        vectors = np.empty((0, 0), dtype=np.float32)

    _write_metadata(output / METADATA_FILENAME, records)
    np.save(output / VECTORS_FILENAME, vectors, allow_pickle=False)
    return LightRagIndex(output, records, vectors, client=client)


class LightRagIndex:
    """Loaded lightweight index with optional embedding/reranker client."""

    def __init__(
        self,
        path: str | Path,
        metadata: Sequence[Mapping[str, Any]],
        vectors: np.ndarray,
        *,
        client: Any = None,
    ) -> None:
        self.path = Path(path)
        self.metadata = [dict(record) for record in metadata]
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.client = client
        if self.vectors.ndim != 2 or self.vectors.shape[0] != len(self.metadata):
            raise ValueError("metadata and vectors must have the same row count")

    @classmethod
    def load(cls, path: str | Path, client: Any = None) -> "LightRagIndex":
        """Load an index directory produced by :func:`build_index`."""

        root = Path(path)
        metadata_path = root / METADATA_FILENAME
        vectors_path = root / VECTORS_FILENAME
        metadata: list[dict[str, Any]] = []
        with metadata_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid metadata JSON at line {line_number}") from exc
                if not isinstance(record, Mapping):
                    raise ValueError(f"metadata line {line_number} is not a JSON object")
                metadata.append(dict(record))
        vectors = np.load(vectors_path, allow_pickle=False)
        if vectors.ndim != 2 or vectors.shape[0] != len(metadata):
            raise ValueError("metadata and vectors must have the same row count")
        return cls(root, metadata, vectors, client=client)

    def _candidate_indices(self, sample_id: object, split: object) -> list[int]:
        query_split = _text(split).lower()
        candidates: list[int] = []
        for index, record in enumerate(self.metadata):
            if sample_id is not None and _same_sample(record, sample_id) and _is_label(record):
                continue
            if _is_label(record):
                record_split = _text(record.get("split")).lower()
                if query_split in _HOLDOUT_SPLITS and record_split != "fit":
                    continue
                if query_split == "fit" and record_split != "fit":
                    continue
            candidates.append(index)
        return candidates

    @staticmethod
    def _format_result(
        record: Mapping[str, Any],
        *,
        embedding_score: float,
        rerank_score: float | None,
        mode: str,
    ) -> dict[str, Any]:
        result = dict(record)
        result["score"] = float(rerank_score if rerank_score is not None else embedding_score)
        result["embedding_score"] = float(embedding_score)
        result["rerank_score"] = None if rerank_score is None else float(rerank_score)
        result["retrieval_mode"] = mode
        return result

    def retrieve(
        self,
        query: str,
        sample_id: str | None = None,
        split: str | None = None,
        top_embedding: int = 30,
        top_rerank: int = 8,
        top_k: int = 6,
    ) -> list[dict[str, Any]]:
        """Retrieve metadata using embedding ranking followed by reranking."""

        query_text = _text(query)
        if not query_text or top_embedding <= 0 or top_rerank <= 0 or top_k <= 0:
            return []
        candidate_indices = self._candidate_indices(sample_id, split)
        if not candidate_indices:
            return []
        if self.client is None or not callable(getattr(self.client, "embed_texts", None)):
            raise RuntimeError("retrieve requires a model client with embed_texts")

        query_result = self.client.embed_texts([query_text])
        query_vector = _query_vector(_result_value(query_result))
        candidate_vectors = self.vectors[candidate_indices]
        embedding_scores = _cosine_scores(candidate_vectors, query_vector)
        embedding_order = np.argsort(-embedding_scores, kind="stable")[: min(top_embedding, len(candidate_indices))]
        selected_indices = [candidate_indices[int(position)] for position in embedding_order]
        selected_scores = [float(embedding_scores[int(position)]) for position in embedding_order]

        rerank = getattr(self.client, "rerank", None)
        if not callable(rerank):
            return [
                self._format_result(
                    self.metadata[index],
                    embedding_score=score,
                    rerank_score=None,
                    mode="embedding_offline_fallback",
                )
                for index, score in list(zip(selected_indices, selected_scores))[:top_k]
            ]

        documents = [self.metadata[index]["text"] for index in selected_indices]
        rerank_result = rerank(query_text, documents, top_k=top_rerank)
        reranked = _result_value(rerank_result)
        if not isinstance(reranked, Sequence) or isinstance(reranked, (str, bytes)):
            raise ValueError("reranker result must be a sequence of dictionaries")

        scored: list[tuple[float, int, int]] = []
        for response_order, item in enumerate(reranked):
            if not isinstance(item, Mapping):
                continue
            try:
                relative_index = int(item["index"])
                score = float(item["score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("reranker items must contain index and score") from exc
            if relative_index < 0 or relative_index >= len(selected_indices):
                continue
            scored.append((score, response_order, relative_index))
        scored.sort(key=lambda item: (-item[0], item[1]))

        results: list[dict[str, Any]] = []
        seen: set[int] = set()
        for rerank_score, _, relative_index in scored:
            if relative_index in seen:
                continue
            seen.add(relative_index)
            absolute_index = selected_indices[relative_index]
            results.append(
                self._format_result(
                    self.metadata[absolute_index],
                    embedding_score=selected_scores[relative_index],
                    rerank_score=rerank_score,
                    mode="embedding_rerank",
                )
            )
            if len(results) >= top_k:
                break
        return results
