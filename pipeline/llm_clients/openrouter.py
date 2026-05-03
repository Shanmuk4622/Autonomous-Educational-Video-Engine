"""
AEVE 2.0 — OpenRouter async client.

OpenRouter is OpenAI-compatible. We talk to /chat/completions and /models
directly via httpx. Model-slug resolution is dynamic: at startup the registry
calls list_models() and remaps each ModelSpec to the latest variant of its
family (e.g. nvidia/nemotron-3-coder → nvidia/nemotron-3.1-coder if available).
"""

from __future__ import annotations

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

OPENROUTER_BASE_URL = getattr(
    config, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
OPENROUTER_API_KEY: str = getattr(config, "OPENROUTER_API_KEY", "") or ""
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
HTTP_REFERER = "https://github.com/aeve"  # OpenRouter requires this header
APP_TITLE = "AEVE"


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = OPENROUTER_BASE_URL,
    ) -> None:
        self._api_key = api_key or OPENROUTER_API_KEY
        if not self._api_key:
            logger.warning("OpenRouterClient initialized without an API key")
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, http2=True)
        self._model_cache: list[str] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": HTTP_REFERER,
            "X-Title": APP_TITLE,
        }

    async def list_models(self, *, force_refresh: bool = False) -> list[str]:
        """Return list of available model slugs. Cached after first call."""
        if self._model_cache is not None and not force_refresh:
            return self._model_cache
        if not self._api_key:
            self._model_cache = []
            return []
        try:
            resp = await self._client.get(
                f"{self._base_url}/models",
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
            ids = [m.get("id", "") for m in data.get("data", [])]
            self._model_cache = [m for m in ids if m]
            return self._model_cache
        except httpx.HTTPError as exc:
            logger.warning("OpenRouter /models probe failed: %s", exc)
            self._model_cache = []
            return []

    async def resolve_slug(self, intended: str) -> str:
        """Map an intended slug to the latest available variant of its family.

        e.g. 'nvidia/nemotron-3-coder' → 'nvidia/nemotron-3.1-coder' if 3-coder
        is gone but 3.1-coder is live. Returns the original slug as a passthrough
        if the model list is unavailable or contains an exact match.
        """
        models = await self.list_models()
        if not models:
            return intended
        if intended in models:
            return intended

        prefix = intended.split("/", 1)[0]
        family_token = intended.split("/", 1)[-1].split("-")[0]
        candidates = [
            m for m in models if m.startswith(f"{prefix}/") and family_token in m
        ]
        if candidates:
            best = sorted(candidates)[-1]
            logger.info("OpenRouter slug %s → %s (closest match)", intended, best)
            return best

        logger.warning("OpenRouter slug %s not found and no family match; keeping intent", intended)
        return intended

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        role: str | None = None,
    ) -> str:
        ctx = LLMErrorContext(role=role, provider="openrouter", model=model)
        if not self._api_key:
            raise ProviderError("OpenRouter API key not configured", context=ctx)

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
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter HTTP error: {exc}", context=ctx) from exc

        if resp.status_code == 429:
            raise RateLimitError("OpenRouter rate limit (429)", context=ctx)
        if resp.status_code >= 500:
            raise ProviderError(
                f"OpenRouter server error {resp.status_code}: {resp.text[:300]}",
                context=ctx,
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"OpenRouter client error {resp.status_code}: {resp.text[:300]}",
                context=ctx,
            )

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(
                f"OpenRouter malformed response: {resp.text[:300]}",
                context=ctx,
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise ProviderError("OpenRouter returned empty content", context=ctx)
        return content


_singleton: OpenRouterClient | None = None


def get_client() -> OpenRouterClient:
    global _singleton
    if _singleton is None:
        _singleton = OpenRouterClient()
    return _singleton


__all__ = ["OpenRouterClient", "get_client"]
