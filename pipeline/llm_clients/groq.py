"""
AEVE 2.0 — Groq async client with key rotation.

Groq exposes an OpenAI-compatible /chat/completions endpoint. We talk to it
directly via httpx so we can:
  - rotate across multiple API keys on HTTP 429 within a single request
  - capture the precise (provider, model, key_index) context for errors
  - skip the openai SDK's connection pool which doesn't share well across
    providers in a multi-route setup
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

import config
from pipeline.llm_clients.errors import (
    LLMErrorContext,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger("AEVE")

GROQ_BASE_URL = getattr(config, "GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_API_KEYS: list[str] = list(getattr(config, "GROQ_API_KEYS", []))
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)


class GroqClient:
    """Single client instance shared across the process. Rotates keys on 429."""

    def __init__(
        self,
        api_keys: list[str] | None = None,
        base_url: str = GROQ_BASE_URL,
    ) -> None:
        keys = api_keys if api_keys is not None else GROQ_API_KEYS
        self._keys = [k for k in keys if k]
        if not self._keys:
            logger.warning("GroqClient initialized with zero API keys")
        self._base_url = base_url.rstrip("/")
        self._key_idx = 0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, http2=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.4,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        role: str | None = None,
    ) -> str:
        """Single chat completion call. Rotates keys across the entire key list on 429."""
        if not self._keys:
            raise ProviderError(
                "no Groq API keys configured",
                context=LLMErrorContext(role=role, provider="groq", model=model),
            )

        last_error: Exception | None = None
        for rotation in range(len(self._keys)):
            key_idx = (self._key_idx + rotation) % len(self._keys)
            key = self._keys[key_idx]
            try:
                content = await self._post_chat(
                    key=key,
                    key_idx=key_idx,
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    role=role,
                )
                async with self._lock:
                    self._key_idx = key_idx
                return content
            except RateLimitError as exc:
                logger.warning(
                    "Groq 429 on key %d/%d (%s); rotating",
                    key_idx + 1,
                    len(self._keys),
                    model,
                )
                last_error = exc
                continue

        raise last_error or ProviderError(
            "all Groq keys exhausted",
            context=LLMErrorContext(role=role, provider="groq", model=model),
        )

    async def _post_chat(
        self,
        *,
        key: str,
        key_idx: int,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: dict[str, Any] | None,
        role: str | None,
    ) -> str:
        ctx = LLMErrorContext(role=role, provider="groq", model=model, attempt=key_idx + 1)
        url = f"{self._base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            resp = await self._client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Groq HTTP error: {exc}", context=ctx) from exc

        if resp.status_code == 429:
            raise RateLimitError("Groq rate limit (429)", context=ctx)
        if resp.status_code >= 500:
            raise ProviderError(f"Groq server error {resp.status_code}: {resp.text[:300]}", context=ctx)
        if resp.status_code >= 400:
            raise ProviderError(
                f"Groq client error {resp.status_code}: {resp.text[:300]}",
                context=ctx,
            )

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(
                f"Groq malformed response: {resp.text[:300]}",
                context=ctx,
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Groq returned empty content", context=ctx)
        return content


_singleton: GroqClient | None = None


def get_client() -> GroqClient:
    global _singleton
    if _singleton is None:
        _singleton = GroqClient()
    return _singleton


__all__ = ["GroqClient", "get_client"]
