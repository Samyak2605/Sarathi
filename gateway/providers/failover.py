"""Retries, timeout budgets, circuit breakers and failover chains, wired
together into the two entry points the API layer calls:
`chat_with_failover` (non-streaming) and `stream_with_failover`
(streaming, with the buffered mid-stream-death policy).
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from gateway.providers.base import StreamChunk
from gateway.providers.errors import ProviderError, ProviderUnavailableError
from gateway.providers.registry import ProviderRegistry
from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse


@dataclass
class FailoverOutcome:
    provider_used: str = ""
    model_used: str = ""
    attempts: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error_type: str | None = None
    mid_stream_error: bool = False


async def _sleep_backoff(attempt: int, base_ms: float, max_ms: float) -> None:
    delay_ms = min(max_ms, base_ms * (2**attempt))
    jitter = random.uniform(0.5, 1.5)
    await asyncio.sleep((delay_ms * jitter) / 1000)


async def _call_with_retry(
    adapter, breaker, request: ChatCompletionRequest, model: str, timeout_s: float, policy
) -> ChatCompletionResponse:
    last_exc: ProviderError | None = None
    for attempt in range(policy.max_attempts_per_provider):
        try:
            result = await asyncio.wait_for(
                adapter.chat(request, model, timeout_s), timeout=timeout_s
            )
            breaker.record_success()
            return result
        except TimeoutError as e:
            from gateway.providers.errors import ProviderTimeoutError

            last_exc = ProviderTimeoutError(adapter.name, "call timed out")
            last_exc.__cause__ = e
        except ProviderError as e:
            last_exc = e
            if not e.retryable:
                break
        if attempt < policy.max_attempts_per_provider - 1 and last_exc and last_exc.retryable:
            await _sleep_backoff(attempt, policy.backoff_base_ms, policy.backoff_max_ms)
    breaker.record_failure()
    assert last_exc is not None
    raise last_exc


async def chat_with_failover(
    registry: ProviderRegistry, tier: str, request: ChatCompletionRequest
) -> tuple[ChatCompletionResponse, FailoverOutcome]:
    assert registry.policy is not None
    chain = registry.policy.chains[tier]
    outcome = FailoverOutcome()
    last_exc: ProviderError | None = None

    for step in chain:
        if step.provider not in registry.adapters:
            continue
        breaker = registry.breakers[step.provider]
        if not breaker.allow_request():
            outcome.attempts.append(f"{step.provider}:breaker_open")
            continue
        outcome.attempts.append(step.provider)
        timeout_s = registry.policy.timeouts[step.provider]
        try:
            response = await _call_with_retry(
                registry.adapters[step.provider],
                breaker,
                request,
                step.model,
                timeout_s,
                registry.policy,
            )
            outcome.provider_used = step.provider
            outcome.model_used = step.model
            outcome.prompt_tokens = response.usage.prompt_tokens
            outcome.completion_tokens = response.usage.completion_tokens
            return response, outcome
        except ProviderError as e:
            last_exc = e
            continue

    outcome.error_type = type(last_exc).__name__ if last_exc else "NoProviderAvailable"
    final_exc = last_exc or ProviderUnavailableError("failover", "no providers available in chain")
    final_exc.attempts = outcome.attempts  # type: ignore[attr-defined]
    raise final_exc


async def stream_with_failover(
    registry: ProviderRegistry,
    tier: str,
    request: ChatCompletionRequest,
    outcome: FailoverOutcome,
):
    """Yields StreamChunk. Mutates `outcome` in place so the caller can read
    final metering info once the generator is exhausted.

    Buffering policy: chunks are held (not yielded) until
    `stream_fallback_token_threshold` tokens have been seen. If a provider
    fails while still buffering, the buffer is discarded and the next chain
    entry is tried -- the client never saw anything, so this is genuinely
    silent. Once the threshold is crossed the buffer is flushed and we're
    "committed" to that provider: a later failure can only be surfaced as a
    graceful mid-stream error, never retried, because we can't un-send a
    partial answer.
    """
    assert registry.policy is not None
    chain = registry.policy.chains[tier]
    threshold = registry.policy.stream_fallback_token_threshold
    last_exc: ProviderError | None = None

    for step in chain:
        if step.provider not in registry.adapters:
            continue
        breaker = registry.breakers[step.provider]
        if not breaker.allow_request():
            outcome.attempts.append(f"{step.provider}:breaker_open")
            continue
        outcome.attempts.append(step.provider)
        timeout_s = registry.policy.timeouts[step.provider]

        buffered: list[StreamChunk] = []
        tokens_seen = 0
        committed = False
        try:
            async for chunk in registry.adapters[step.provider].chat_stream(
                request, step.model, timeout_s
            ):
                if chunk.prompt_tokens is not None:
                    outcome.prompt_tokens = chunk.prompt_tokens
                if chunk.completion_tokens is not None:
                    outcome.completion_tokens = chunk.completion_tokens

                if committed:
                    yield chunk
                    continue

                buffered.append(chunk)
                if chunk.delta:
                    tokens_seen += 1
                if tokens_seen >= threshold or chunk.finish_reason:
                    committed = True
                    for buffered_chunk in buffered:
                        yield buffered_chunk
                    buffered = []

            breaker.record_success()
            outcome.provider_used = step.provider
            outcome.model_used = step.model
            return
        except ProviderError as e:
            breaker.record_failure()
            last_exc = e
            if committed:
                outcome.error_type = type(e).__name__
                outcome.mid_stream_error = True
                outcome.provider_used = step.provider
                outcome.model_used = step.model
                yield StreamChunk(delta="", finish_reason="error")
                return
            continue  # not committed yet -- silently try next provider

    outcome.error_type = type(last_exc).__name__ if last_exc else "NoProviderAvailable"
    final_exc = last_exc or ProviderUnavailableError("failover", "no providers available in chain")
    final_exc.attempts = outcome.attempts  # type: ignore[attr-defined]
    raise final_exc
