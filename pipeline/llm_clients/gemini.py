"""
AEVE 2.0 — Google Gemini async client (kept for optional fallback).

Not in any default route in AEVE 2.0; provided so callers can opt in. Uses the
google-genai SDK if installed, falling back to a no-op stub that always raises
ProviderError. This keeps the registry import-clean even when the SDK is absent.
"""

from __future__ import annotations

import asyncio
import logging

import config
from pipeline.llm_clients.errors import LLMErrorContext, ProviderError

logger = logging.getLogger("AEVE")

try:
    from google import genai  # type: ignore[import-not-found]

    _HAS_GENAI = True
except ImportError:
    genai = None  # type: ignore[assignment]
    _HAS_GENAI = False

GOOGLE_API_KEY: str = getattr(config, "GOOGLE_API_KEY", "") or ""


class GeminiClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or GOOGLE_API_KEY
        self._client = None
        if _HAS_GENAI and self._api_key:
            self._client = genai.Client(api_key=self._api_key)
        elif not _HAS_GENAI:
            logger.info("google-genai not installed; Gemini fallback disabled")

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        role: str | None = None,
        **_: object,
    ) -> str:
        ctx = LLMErrorContext(role=role, provider="gemini", model=model)
        if self._client is None:
            raise ProviderError("Gemini client unavailable", context=ctx)

        prompt = "\n\n".join(
            f"[{m.get('role', 'user').upper()}]\n{m.get('content', '')}" for m in messages
        )

        def _sync_call() -> str:
            response = self._client.models.generate_content(  # type: ignore[union-attr]
                model=model,
                contents=prompt,
                config={"temperature": temperature, "max_output_tokens": max_tokens},
            )
            text = getattr(response, "text", "") or ""
            return text

        try:
            content = await asyncio.to_thread(_sync_call)
        except Exception as exc:  # genai exceptions vary across versions
            raise ProviderError(f"Gemini call failed: {exc}", context=ctx) from exc

        if not content.strip():
            raise ProviderError("Gemini returned empty content", context=ctx)
        return content


_singleton: GeminiClient | None = None


def get_client() -> GeminiClient:
    global _singleton
    if _singleton is None:
        _singleton = GeminiClient()
    return _singleton


__all__ = ["GeminiClient", "get_client"]
