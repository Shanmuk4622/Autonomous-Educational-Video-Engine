"""
AEVE 2.0 — agent → model routing with hard fallback chains.

Every agent has a primary model spec plus 2 fallbacks. A spec is invoked in
order; on RateLimitError, ProviderError, or schema-validation failure, the
registry advances to the next spec. The original error is preserved so callers
can still distinguish "everything failed" from "first try failed."

Usage:

    from pipeline.llm_clients import call_agent, ROUTES

    text = await call_agent(
        role="solver",
        user_prompt="Prove the Pythagorean theorem.",
        system_prompt="You are a careful mathematician.",
        max_tokens=4096,
    )
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pipeline.llm_clients import gemini, groq, openrouter
from pipeline.llm_clients.errors import (
    LLMError,
    LLMErrorContext,
    OutputValidationError,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger("AEVE")

AgentRole = Literal["solver", "director", "narrator", "animator", "healer"]
Provider = Literal["groq", "openrouter", "gemini"]


@dataclass(frozen=True)
class ModelSpec:
    provider: Provider
    model: str
    temperature: float = 0.3
    max_tokens: int = 4096


# ---------------------------------------------------------------------------
# Routing table — primary + 2 fallbacks per agent.
# Order matters: specs[0] is tried first.
# Slugs are intent; OpenRouter slugs are resolved at runtime via
# openrouter.resolve_slug() so a deprecation never hard-stops the pipeline.
# ---------------------------------------------------------------------------

# Slug currency last verified by probe_keys.py on 2026-05-05.
# Three previously-listed slugs were retired here because the providers no
# longer serve them: `moonshotai/kimi-k2-instruct` (gone from Groq),
# `nvidia/nemotron-3-coder` (404 on OpenRouter), and `zai/glm-4.6` (404 on
# OpenRouter). When promoting a new slug, run `python probe_keys.py` first
# and only land slugs that returned `OK`.
ROUTES: dict[AgentRole, list[ModelSpec]] = {
    "solver": [
        # Solver outputs DeepSolution JSON — long-form math; 6k is comfortable.
        ModelSpec("groq", "llama-3.3-70b-versatile", temperature=0.2, max_tokens=6144),
        ModelSpec("openrouter", "deepseek/deepseek-chat-v3", temperature=0.2, max_tokens=6144),
        ModelSpec("groq", "llama-3.1-8b-instant", temperature=0.2, max_tokens=6144),
    ],
    "director": [
        ModelSpec("groq", "llama-3.3-70b-versatile", temperature=0.4, max_tokens=4096),
        ModelSpec("openrouter", "meta-llama/llama-3.3-70b-instruct", temperature=0.4, max_tokens=4096),
        ModelSpec("groq", "llama-3.1-8b-instant", temperature=0.4, max_tokens=4096),
    ],
    "narrator": [
        ModelSpec("groq", "llama-3.3-70b-versatile", temperature=0.5, max_tokens=2048),
        ModelSpec("openrouter", "meta-llama/llama-3.3-70b-instruct", temperature=0.5, max_tokens=2048),
        ModelSpec("groq", "llama-3.1-8b-instant", temperature=0.5, max_tokens=2048),
    ],
    # Manim scene files are ~1 KB / a few hundred tokens. 4096 leaves plenty of
    # headroom and avoids OpenRouter free-tier 402 "you can only afford N" errors
    # observed when 8192 was requested.
    "animator": [
        ModelSpec("openrouter", "qwen/qwen3-coder", temperature=0.2, max_tokens=4096),
        ModelSpec("openrouter", "deepseek/deepseek-chat-v3", temperature=0.2, max_tokens=4096),
        ModelSpec("openrouter", "deepseek/deepseek-r1", temperature=0.2, max_tokens=4096),
    ],
    "healer": [
        ModelSpec("openrouter", "deepseek/deepseek-r1", temperature=0.1, max_tokens=4096),
        ModelSpec("openrouter", "qwen/qwen3-coder", temperature=0.1, max_tokens=4096),
        ModelSpec("openrouter", "deepseek/deepseek-chat-v3", temperature=0.1, max_tokens=4096),
    ],
}


@dataclass
class _CallContext:
    role: AgentRole
    attempt: int
    spec: ModelSpec
    errors: list[Exception] = field(default_factory=list)


def resolve_route(role: AgentRole) -> list[ModelSpec]:
    """Return the ordered ModelSpec chain for an agent role."""
    if role not in ROUTES:
        raise ValueError(f"unknown agent role {role!r}; expected one of {list(ROUTES)}")
    return list(ROUTES[role])


async def _dispatch(
    spec: ModelSpec,
    *,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    response_format: dict[str, Any] | None,
    role: AgentRole,
) -> str:
    if spec.provider == "groq":
        return await groq.get_client().chat(
            model=spec.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            role=role,
        )
    if spec.provider == "openrouter":
        client = openrouter.get_client()
        resolved = await client.resolve_slug(spec.model)
        return await client.chat(
            model=resolved,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            role=role,
        )
    if spec.provider == "gemini":
        return await gemini.get_client().chat(
            model=spec.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            role=role,
        )
    raise ValueError(f"unknown provider {spec.provider!r}")


async def call_agent(
    *,
    role: AgentRole,
    user_prompt: str,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
    extra_messages: list[dict[str, str]] | None = None,
) -> str:
    """Call an agent with hard fallback across its model chain.

    Returns the raw assistant content string. JSON / Pydantic validation is
    the caller's responsibility — see `call_agent_json()` if you want gating.
    """
    chain = resolve_route(role)
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if extra_messages:
        messages.extend(extra_messages)
    messages.append({"role": "user", "content": user_prompt})

    errors: list[Exception] = []
    for attempt, spec in enumerate(chain, start=1):
        eff_temp = temperature if temperature is not None else spec.temperature
        eff_max = max_tokens if max_tokens is not None else spec.max_tokens
        try:
            content = await _dispatch(
                spec,
                messages=messages,
                temperature=eff_temp,
                max_tokens=eff_max,
                response_format=response_format,
                role=role,
            )
            if attempt > 1:
                logger.info(
                    "[%s] succeeded on fallback %d/%d (%s/%s)",
                    role,
                    attempt,
                    len(chain),
                    spec.provider,
                    spec.model,
                )
            return content
        except (RateLimitError, ProviderError) as exc:
            errors.append(exc)
            logger.warning(
                "[%s] attempt %d/%d failed (%s/%s): %s",
                role,
                attempt,
                len(chain),
                spec.provider,
                spec.model,
                exc,
            )
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)
            logger.exception(
                "[%s] unexpected error on %s/%s; trying fallback",
                role,
                spec.provider,
                spec.model,
            )
            continue

    summary = "; ".join(f"{type(e).__name__}: {e}" for e in errors[-3:])
    raise LLMError(
        f"all {len(chain)} model specs failed for role={role}: {summary}",
        context=LLMErrorContext(role=role),
    )


async def call_agent_json(
    *,
    role: AgentRole,
    user_prompt: str,
    system_prompt: str | None = None,
    parser: Any,
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra_messages: list[dict[str, str]] | None = None,
) -> Any:
    """Call agent and validate the response through `parser(raw_text) -> obj`.

    On parser failure we retry once with the validation error injected back into
    the prompt (the contract from the rewrite plan). Then we fall through to the
    next spec in the route. `parser` should raise a clear exception on failure.
    """
    chain = resolve_route(role)
    base_messages: list[dict[str, str]] = []
    if system_prompt:
        base_messages.append({"role": "system", "content": system_prompt})
    if extra_messages:
        base_messages.extend(extra_messages)
    base_messages.append({"role": "user", "content": user_prompt})

    errors: list[Exception] = []
    for attempt, spec in enumerate(chain, start=1):
        eff_temp = temperature if temperature is not None else spec.temperature
        eff_max = max_tokens if max_tokens is not None else spec.max_tokens

        for repair_round in range(2):  # 0 = first try, 1 = with error injection
            messages = list(base_messages)
            if repair_round == 1 and errors and isinstance(errors[-1], OutputValidationError):
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous output failed validation:\n\n"
                            f"{errors[-1]}\n\n"
                            "Return ONLY a corrected response in the requested format."
                        ),
                    }
                )
            try:
                raw = await _dispatch(
                    spec,
                    messages=messages,
                    temperature=eff_temp,
                    max_tokens=eff_max,
                    response_format={"type": "json_object"},
                    role=role,
                )
                try:
                    return parser(raw)
                except Exception as parse_exc:
                    errors.append(
                        OutputValidationError(
                            f"parser rejected output: {parse_exc}",
                            raw_output=raw[:1000],
                            context=LLMErrorContext(
                                role=role,
                                provider=spec.provider,
                                model=spec.model,
                                attempt=attempt,
                            ),
                        )
                    )
                    continue  # repair round
            except (RateLimitError, ProviderError) as exc:
                errors.append(exc)
                break  # straight to next spec; no point retrying same provider on 429

    summary = "; ".join(f"{type(e).__name__}: {e}" for e in errors[-3:])
    raise LLMError(
        f"all model specs+repairs failed for role={role}: {summary}",
        context=LLMErrorContext(role=role),
    )


async def warm_up() -> None:
    """Optional: probe OpenRouter /models cache on startup."""
    try:
        await openrouter.get_client().list_models()
    except Exception:  # pragma: no cover
        logger.warning("OpenRouter warm-up failed; continuing")


__all__ = [
    "AgentRole",
    "ModelSpec",
    "ROUTES",
    "call_agent",
    "call_agent_json",
    "resolve_route",
    "warm_up",
]
