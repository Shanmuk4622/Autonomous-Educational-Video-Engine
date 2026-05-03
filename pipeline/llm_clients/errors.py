"""
AEVE 2.0 — LLM client exception hierarchy.

Preserves the rich provider/model/role context the legacy pipeline used so
callers can build precise error messages and retry policies.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LLMErrorContext:
    role: str | None = None
    provider: str | None = None
    model: str | None = None
    attempt: int | None = None

    def __str__(self) -> str:
        parts = [f"{k}={v}" for k, v in self.__dict__.items() if v is not None]
        return "[" + " ".join(parts) + "]" if parts else ""


class LLMError(Exception):
    """Base class for all LLM-client errors."""

    def __init__(self, message: str, *, context: LLMErrorContext | None = None) -> None:
        self.context = context or LLMErrorContext()
        ctx_str = str(self.context)
        super().__init__(f"{message} {ctx_str}".strip())


class ProviderError(LLMError):
    """Provider call returned a non-retryable HTTP error or empty body."""


class RateLimitError(LLMError):
    """HTTP 429 from a provider — caller should rotate keys / fall back."""


class OutputValidationError(LLMError):
    """Provider returned content that failed schema/format validation."""

    def __init__(
        self,
        message: str,
        *,
        raw_output: str = "",
        context: LLMErrorContext | None = None,
    ) -> None:
        super().__init__(message, context=context)
        self.raw_output = raw_output
