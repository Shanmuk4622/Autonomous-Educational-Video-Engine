"""
AEVE 2.0 — multi-provider LLM client package.

Public surface:
    call_agent(...)               — primary async entrypoint with fallback chain
    LLMError, OutputValidationError, RateLimitError, ProviderError
    AgentRole, ModelSpec
"""

from pipeline.llm_clients.errors import (
    LLMError,
    OutputValidationError,
    ProviderError,
    RateLimitError,
)
from pipeline.llm_clients.registry import (
    AgentRole,
    ModelSpec,
    ROUTES,
    call_agent,
    call_agent_json,
    resolve_route,
)

__all__ = [
    "AgentRole",
    "LLMError",
    "ModelSpec",
    "OutputValidationError",
    "ProviderError",
    "ROUTES",
    "RateLimitError",
    "call_agent",
    "call_agent_json",
    "resolve_route",
]
