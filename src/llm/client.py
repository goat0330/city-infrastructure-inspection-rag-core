"""Small client wrapper for the project's chat, embedding, and rerank calls."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from openai import OpenAI


_MISSING = object()
_DEFAULT_CHAT_MAX_TOKENS = 2048


@dataclass(frozen=True)
class ModelCallResult:
    """A model value together with the measurements needed by experiments."""

    value: Any
    model: str
    duration_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    attempts: int = 1


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _token_usage(response: Any) -> tuple[int | None, int | None, int | None]:
    usage = _field(response, "usage", None)
    if usage is None:
        return None, None, None
    return (
        _field(usage, "prompt_tokens", None),
        _field(usage, "completion_tokens", None),
        _field(usage, "total_tokens", None),
    )


def _redact(text: str, secrets: Sequence[str | None]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:500]


def _response_payload(response: Any) -> Any:
    if isinstance(response, Mapping):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            return json_method()
        except Exception:
            pass
    return response


class OpenAIModelClient:
    """Thin official-SDK client for the project's three model operations."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = 60.0,
        chat_model: str | None = None,
        embed_model: str | None = None,
        rerank_model: str | None = None,
        retry_delay: float = 0.1,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("IAIC_API_KEY")
        self._base_url = base_url if base_url is not None else os.getenv("IAIC_API_BASE")
        self._chat_model = chat_model if chat_model is not None else os.getenv("IAIC_CHAT_MODEL")
        self._embed_model = embed_model if embed_model is not None else os.getenv("IAIC_EMBED_MODEL")
        self._rerank_model = rerank_model if rerank_model is not None else os.getenv("IAIC_RERANK_MODEL")
        self._retry_delay = max(0.0, retry_delay)

        if client is not None:
            self._client = client
            return

        if not self._api_key:
            raise ValueError("IAIC_API_KEY is required")

        options: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": timeout,
            "max_retries": 0,
        }
        if self._base_url:
            options["base_url"] = self._base_url
        try:
            self._client = OpenAI(**options)
        except Exception as exc:
            message = _redact(str(exc) or type(exc).__name__, [self._api_key])
            raise RuntimeError(f"OpenAI client initialization failed: {message}") from None

    @staticmethod
    def _model(value: str | None, configured: str | None, env_name: str) -> str:
        model = value if value is not None else configured
        if not model:
            raise ValueError(f"{env_name} is required")
        return model

    def _run(
        self,
        *,
        operation: str,
        model: str,
        request: Callable[[], Any],
        parse: Callable[[Any], Any],
    ) -> ModelCallResult:
        started = time.perf_counter()
        last_error: Exception | None = None

        for attempt in (1, 2):
            try:
                response = request()
                value = parse(response)
                prompt_tokens, completion_tokens, total_tokens = _token_usage(response)
                duration_ms = (time.perf_counter() - started) * 1000
                return ModelCallResult(
                    value=value,
                    model=model,
                    duration_ms=round(duration_ms, 3),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    attempts=attempt,
                )
            except Exception as exc:
                last_error = exc
                if attempt == 1 and self._retry_delay:
                    time.sleep(self._retry_delay)

        message = _redact(str(last_error) or type(last_error).__name__, [self._api_key])
        raise RuntimeError(f"{operation} failed after 2 attempts: {message}") from None

    @staticmethod
    def _parse_chat(response: Any) -> dict[str, Any]:
        choices = _field(response, "choices", _MISSING)
        if not choices:
            raise ValueError("chat response has no choices")
        choice = choices[0]
        message = _field(choice, "message", _MISSING)
        content = _field(message, "content", _MISSING)
        if content is _MISSING or content is None or (isinstance(content, str) and not content.strip()):
            finish_reason = _field(choice, "finish_reason", _MISSING)
            usage = _field(response, "usage", None)
            reasoning_tokens = _field(usage, "reasoning_tokens", _MISSING)
            if reasoning_tokens is _MISSING:
                usage_details = _field(usage, "completion_tokens_details", None)
                reasoning_tokens = _field(usage_details, "reasoning_tokens", _MISSING)
            details = []
            if finish_reason is not _MISSING and finish_reason is not None:
                details.append(f"finish_reason={finish_reason}")
            if reasoning_tokens is not _MISSING and reasoning_tokens is not None:
                details.append(f"reasoning_tokens={reasoning_tokens}")
            suffix = f" ({'; '.join(details)})" if details else ""
            raise ValueError(
                "chat response has empty message content"
                f"{suffix}; increase max_tokens or disable thinking"
            )
        if isinstance(content, list):
            content = "".join(
                str(part_text)
                for part in content
                if (part_text := _field(part, "text", _MISSING)) is not _MISSING
            )
        if not isinstance(content, str):
            raise ValueError("chat response content is not text")

        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("chat response is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("chat response JSON must be an object")
        return value

    @staticmethod
    def _parse_embeddings(response: Any, expected_count: int) -> np.ndarray:
        items = _field(response, "data", _MISSING)
        if items is _MISSING:
            raise ValueError("embedding response has no data")
        items = list(items)
        if len(items) != expected_count:
            raise ValueError("embedding response count does not match input")
        indexed_items = list(enumerate(items))
        indexed_items.sort(key=lambda pair: _field(pair[1], "index", pair[0]))
        vectors = []
        for _, item in indexed_items:
            embedding = _field(item, "embedding", _MISSING)
            if embedding is _MISSING:
                raise ValueError("embedding response item has no vector")
            vectors.append(embedding)
        if not vectors:
            return np.empty((0, 0), dtype=np.float32)
        value = np.asarray(vectors, dtype=np.float32)
        if value.ndim != 2:
            raise ValueError("embedding response must be a two-dimensional array")
        return value

    @staticmethod
    def _parse_rerank(response: Any, documents: Sequence[str]) -> list[dict[str, Any]]:
        payload = _response_payload(response)
        items = _field(payload, "results", _MISSING)
        if items is _MISSING:
            items = _field(payload, "data", _MISSING)
        if items is _MISSING:
            items = _field(payload, "items", _MISSING)
        if items is _MISSING and isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            items = payload
        if items is _MISSING or items is None:
            raise ValueError("rerank response has no results")
        if isinstance(items, Mapping):
            nested = _field(items, "items", _MISSING)
            if nested is not _MISSING:
                items = nested

        ranked: list[dict[str, Any]] = []
        for item in items:
            index = _field(item, "index", _MISSING)
            score = _field(item, "score", _MISSING)
            if score is _MISSING:
                score = _field(item, "relevance_score", _MISSING)
            if index is _MISSING or score is _MISSING:
                raise ValueError("rerank result needs index and score")
            index = int(index)
            text = _field(item, "text", _MISSING)
            if text is _MISSING or text is None:
                if not 0 <= index < len(documents):
                    raise ValueError("rerank result index is out of range")
                text = documents[index]
            ranked.append({"index": index, "score": float(score), "text": text})
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked

    def chat_json(
        self,
        messages: Sequence[Mapping[str, Any]],
        model: str | None = None,
        temperature: float = 0,
        max_tokens: int | None = None,
    ) -> ModelCallResult:
        model_name = self._model(model, self._chat_model, "IAIC_CHAT_MODEL")
        options: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": _DEFAULT_CHAT_MAX_TOKENS if max_tokens is None else max_tokens,
        }
        if "qwen" in model_name.casefold():
            options["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        return self._run(
            operation="chat_json",
            model=model_name,
            request=lambda: self._client.chat.completions.create(**options),
            parse=self._parse_chat,
        )

    def embed_texts(self, texts: Sequence[str], model: str | None = None) -> ModelCallResult:
        model_name = self._model(model, self._embed_model, "IAIC_EMBED_MODEL")
        inputs = [texts] if isinstance(texts, str) else list(texts)
        options = {"model": model_name, "input": inputs}
        return self._run(
            operation="embed_texts",
            model=model_name,
            request=lambda: self._client.embeddings.create(**options),
            parse=lambda response: self._parse_embeddings(response, len(inputs)),
        )

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        model: str | None = None,
        top_k: int = 8,
    ) -> ModelCallResult:
        if top_k < 0:
            raise ValueError("top_k must be non-negative")
        model_name = self._model(model, self._rerank_model, "IAIC_RERANK_MODEL")
        input_documents = list(documents)
        body = {
            "query": query,
            "documents": input_documents,
            "top_n": top_k,
            "model": model_name,
        }
        return self._run(
            operation="rerank",
            model=model_name,
            request=lambda: self._client.post("/rerank", cast_to=dict, body=body),
            parse=lambda response: self._parse_rerank(response, input_documents)[:top_k],
        )
