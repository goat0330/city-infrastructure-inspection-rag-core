from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.llm import ModelCallResult, OpenAIModelClient


def _client(mock_client, **kwargs):
    return OpenAIModelClient(
        client=mock_client,
        chat_model="chat-default",
        embed_model="embed-default",
        rerank_model="rerank-default",
        retry_delay=0,
        **kwargs,
    )


def test_constructor_uses_iaic_base_url_and_timeout(monkeypatch):
    key = "sk-" + "unit-secret"
    monkeypatch.setenv("IAIC_API_KEY", key)
    monkeypatch.setenv("IAIC_API_BASE", "https://llm.example.test/v1")
    fake = MagicMock()
    with patch("src.llm.client.OpenAI", return_value=fake) as openai:
        OpenAIModelClient(timeout=3.5)
    openai.assert_called_once_with(
        api_key=key,
        base_url="https://llm.example.test/v1",
        timeout=3.5,
        max_retries=0,
    )


@pytest.mark.parametrize("content", ['{"ok": true}', '```json\n{"ok": true}\n```'])
def test_chat_json_parses_json_and_code_fence(content):
    fake = MagicMock()
    fake.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3, total_tokens=7),
    )
    result = _client(fake).chat_json([{"role": "user", "content": "return JSON"}], max_tokens=20)
    assert isinstance(result, ModelCallResult)
    assert result.value == {"ok": True}
    assert result.model == "chat-default"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (4, 3, 7)
    assert result.attempts == 1
    fake.chat.completions.create.assert_called_once_with(
        model="chat-default",
        messages=[{"role": "user", "content": "return JSON"}],
        temperature=0,
        max_tokens=20,
    )


def test_embed_texts_returns_two_dimensional_numpy_array():
    fake = MagicMock()
    fake.embeddings.create.return_value = {
        "data": [
            {"index": 1, "embedding": [3, 4]},
            {"index": 0, "embedding": [1, 2]},
        ],
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }
    result = _client(fake).embed_texts(["second", "first"])
    assert isinstance(result.value, np.ndarray)
    assert result.value.shape == (2, 2)
    np.testing.assert_allclose(result.value, [[1, 2], [3, 4]])
    assert result.completion_tokens is None
    fake.embeddings.create.assert_called_once_with(model="embed-default", input=["second", "first"])


def test_rerank_posts_expected_body_and_sorts_results():
    fake = MagicMock()
    fake.post.return_value = {
        "data": [
            {"index": 0, "score": 0.2},
            {"index": 2, "score": 0.95, "text": "third"},
            {"index": 1, "score": 0.7},
        ]
    }
    documents = ["first", "second", "third"]
    result = _client(fake).rerank("query", documents, top_k=2)
    assert result.value == [
        {"index": 2, "score": 0.95, "text": "third"},
        {"index": 1, "score": 0.7, "text": "second"},
    ]
    fake.post.assert_called_once_with(
        "/rerank",
        cast_to=dict,
        body={"query": "query", "documents": documents, "top_n": 2, "model": "rerank-default"},
    )


def test_retry_once_and_redacts_key_from_final_error():
    key = "sk-" + "failure-secret"
    fake = MagicMock()
    fake.embeddings.create.side_effect = RuntimeError(f"upstream leaked {key}")
    client = _client(fake, api_key=key)
    with pytest.raises(RuntimeError) as raised:
        client.embed_texts(["text"])
    assert fake.embeddings.create.call_count == 2
    assert key not in str(raised.value)
    assert "[REDACTED]" in str(raised.value)
