from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.rag import LightRagIndex, build_index


ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_PATH = ROOT / "assets" / "knowledge" / "pedestrian_underpass.jsonl"
REQUIRED_FIELDS = {
    "id",
    "kind",
    "split",
    "text",
    "possible_causes",
    "possible_impacts",
    "treatment_principles",
    "facility_type",
    "component",
}
EXPECTED_TOPICS = {
    "顶板车辆刮痕",
    "顶板裂缝",
    "侧墙竖向裂缝",
    "侧墙局部破损",
    "翼墙开裂",
    "沉降缝异常",
    "止水带老化或破损",
    "通道渗水",
    "洞口破损",
    "排水不畅",
    "内部积水",
    "栏杆或附属设施破损",
}


class FakeResult:
    def __init__(self, value: object) -> None:
        self.value = value


class AssetClient:
    """Deterministic client sufficient to exercise index persistence and retrieval."""

    def embed_texts(self, texts: list[str]) -> FakeResult:
        vectors: list[list[float]] = []
        for text in texts:
            if text == "人行通道 顶板 裂缝":
                vectors.append([1.0, 0.0, 0.0])
            elif "桥梁" in text:
                vectors.append([0.99, 0.01, 0.0])
            elif "人行通道" in text:
                vectors.append([0.95, 0.05, 0.0])
            else:
                vectors.append([0.0, 1.0, 0.0])
        return FakeResult(np.asarray(vectors, dtype=np.float32))

    def rerank(self, query: str, documents: list[str], *, top_k: int = 8) -> FakeResult:
        ranked = [
            {"index": index, "score": float(len(documents) - index)}
            for index in range(len(documents))
        ]
        return FakeResult(ranked[:top_k])


def _load_cards() -> list[dict[str, object]]:
    with KNOWLEDGE_PATH.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_pedestrian_cards_have_the_existing_metadata_contract_and_topics() -> None:
    cards = _load_cards()

    assert len(cards) == 12
    assert len({card["id"] for card in cards}) == len(cards)
    assert {card["facility_type"] for card in cards} == {"pedestrian_underpass"}

    combined = "\n".join(
        f"{card['component']} {card['text']}"
        for card in cards
    )
    for topic in EXPECTED_TOPICS:
        assert topic in combined

    for card in cards:
        assert set(card) == REQUIRED_FIELDS
        assert card["kind"] == "knowledge_card"
        assert card["split"] == "fit"
        assert isinstance(card["text"], str) and card["text"].strip()
        for field in ("possible_causes", "possible_impacts", "treatment_principles"):
            value = card[field]
            assert isinstance(value, list) and value
            assert all(isinstance(item, str) and item.strip() for item in value)


def test_pedestrian_cards_are_read_by_the_jsonl_numpy_index(tmp_path: Path) -> None:
    cards = _load_cards()
    client = AssetClient()

    built = build_index(cards, tmp_path, client)
    loaded = LightRagIndex.load(tmp_path, client=client)
    metadata = [
        json.loads(line)
        for line in (tmp_path / "metadata.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    vectors = np.load(tmp_path / "vectors.npy", allow_pickle=False)

    assert [record["id"] for record in loaded.metadata] == [record["id"] for record in cards]
    expected_metadata = [
        {
            **card,
            "facility_noun": "人行通道",
            "component_group": card["component"],
        }
        for card in cards
    ]
    assert loaded.metadata == metadata == expected_metadata
    assert vectors.shape == (len(cards), 3)
    assert np.array_equal(loaded.vectors, built.vectors)


def test_pedestrian_facility_quota_does_not_substitute_bridge_cards(tmp_path: Path) -> None:
    pedestrian_cards = _load_cards()[:2]
    bridge_cards = [
        {
            **card,
            "id": f"knowledge:bridge:decoy-{index}",
            "facility_type": "bridge",
            "component": "梁体",
            "text": "普通桥梁梁体裂缝知识卡，不适用于人行通道。",
        }
        for index, card in enumerate(pedestrian_cards, start=1)
    ]
    client = AssetClient()
    build_index(pedestrian_cards + bridge_cards, tmp_path, client)

    result = LightRagIndex.load(tmp_path, client=client).retrieve(
        "人行通道 顶板 裂缝",
        top_embedding=30,
        top_rerank=8,
        top_k=2,
        source_quota={"knowledge_card": 2},
        facility_type="pedestrian_underpass",
    )

    assert len(result) == 2
    assert {item["facility_type"] for item in result} == {"pedestrian_underpass"}
    assert not any(item["facility_type"] == "bridge" for item in result)


def test_pedestrian_cards_do_not_contain_sample_or_gold_label_identity() -> None:
    cards = _load_cards()

    for card in cards:
        assert "sample_id" not in card
        assert "field" not in card
        assert card["kind"] not in {"gold", "gold_label", "label", "label_example"}
        assert not str(card["id"]).startswith(("gold:", "label:"))
