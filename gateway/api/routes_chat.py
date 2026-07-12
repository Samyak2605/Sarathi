from __future__ import annotations

import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from gateway.api.sse import chunk_payload, new_completion_id, sse_done, sse_event
from gateway.auth.dependency import authenticate
from gateway.metering.writer import record_usage
from gateway.providers.errors import ProviderError
from gateway.providers.failover import FailoverOutcome, chat_with_failover, stream_with_failover
from gateway.router.policy import decide
from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse
from gateway.storage.models import ApiKeyRecord

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(
    payload: ChatCompletionRequest,
    request: Request,
    api_key: ApiKeyRecord = Depends(authenticate),
):
    state = request.app.state
    namespace = api_key.key
    start = time.time()

    cache_hit = await state.cache_manager.lookup(payload, namespace)

    if payload.stream:
        return StreamingResponse(
            _stream_response(payload, request, api_key, cache_hit, start),
            media_type="text/event-stream",
        )

    if cache_hit is not None:
        latency_ms = (time.time() - start) * 1000
        await record_usage(
            state.storage,
            api_key=api_key.key,
            model_requested=payload.model,
            model_used=cache_hit.response.model,
            route_tier=None,
            route_reason="cache",
            prompt_tokens=cache_hit.response.usage.prompt_tokens,
            completion_tokens=cache_hit.response.usage.completion_tokens,
            latency_ms=latency_ms,
            cache_status=cache_hit.status,
            outcome="ok",
            provider="cache",
            failover_chain=[],
        )
        return cache_hit.response.model_copy(
            update={
                "sarathi": {
                    "cache_status": cache_hit.status,
                    "similarity": cache_hit.similarity,
                    "latency_ms": round(latency_ms, 2),
                }
            }
        )

    decision = decide(payload, state.routing_policy)

    try:
        response, outcome = await chat_with_failover(state.registry, decision.chain, payload)
    except ProviderError as e:
        latency_ms = (time.time() - start) * 1000
        await record_usage(
            state.storage,
            api_key=api_key.key,
            model_requested=payload.model,
            model_used="",
            route_tier=decision.tier,
            route_reason=decision.reason,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            cache_status="not_cacheable" if not state.cache_manager.cacheable(payload) else "miss",
            outcome="error",
            provider=e.provider,
            failover_chain=getattr(e, "attempts", []),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"All providers in chain failed: {e.message}",
                "type": "provider_error",
            },
        ) from e

    await state.cache_manager.store(payload, namespace, response)

    latency_ms = (time.time() - start) * 1000
    await record_usage(
        state.storage,
        api_key=api_key.key,
        model_requested=payload.model,
        model_used=outcome.model_used,
        route_tier=decision.tier,
        route_reason=decision.reason,
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        latency_ms=latency_ms,
        cache_status="not_cacheable" if not state.cache_manager.cacheable(payload) else "miss",
        outcome="failover" if len(outcome.attempts) > 1 else "ok",
        provider=outcome.provider_used,
        failover_chain=outcome.attempts,
    )

    return response.model_copy(
        update={
            "sarathi": {
                "cache_status": "miss",
                "route_tier": decision.tier,
                "route_reason": decision.reason,
                "provider": outcome.provider_used,
                "failover_chain": outcome.attempts,
                "latency_ms": round(latency_ms, 2),
            }
        }
    )


async def _stream_response(
    payload: ChatCompletionRequest,
    request: Request,
    api_key: ApiKeyRecord,
    cache_hit,
    start: float,
) -> AsyncIterator[str]:
    state = request.app.state
    completion_id = new_completion_id()
    model_label = payload.model

    if cache_hit is not None:
        model_label = cache_hit.response.model
        yield sse_event(chunk_payload(completion_id, model_label, role="assistant"))
        content = cache_hit.response.choices[0].message.content
        for word in content.split(" "):
            yield sse_event(chunk_payload(completion_id, model_label, content=word + " "))
        yield sse_event(chunk_payload(completion_id, model_label, finish_reason="stop"))
        yield sse_done()

        latency_ms = (time.time() - start) * 1000
        await record_usage(
            state.storage,
            api_key=api_key.key,
            model_requested=payload.model,
            model_used=cache_hit.response.model,
            route_tier=None,
            route_reason="cache",
            prompt_tokens=cache_hit.response.usage.prompt_tokens,
            completion_tokens=cache_hit.response.usage.completion_tokens,
            latency_ms=latency_ms,
            cache_status=cache_hit.status,
            outcome="ok",
            provider="cache",
            failover_chain=[],
            stream=True,
        )
        return

    decision = decide(payload, state.routing_policy)
    outcome = FailoverOutcome()
    yield sse_event(chunk_payload(completion_id, model_label, role="assistant"))

    collected: list[str] = []
    error_raised: ProviderError | None = None
    try:
        async for stream_chunk in stream_with_failover(
            state.registry, decision.chain, payload, outcome
        ):
            model_label = outcome.model_used or model_label
            if stream_chunk.delta:
                collected.append(stream_chunk.delta)
                yield sse_event(
                    chunk_payload(completion_id, model_label, content=stream_chunk.delta)
                )
            if stream_chunk.finish_reason == "error":
                yield sse_event(
                    {
                        "error": {
                            "message": "stream terminated after partial output; cannot "
                            "un-send a partial answer, so no fallback was attempted",
                            "type": "mid_stream_error",
                        }
                    }
                )
            elif stream_chunk.finish_reason:
                yield sse_event(
                    chunk_payload(
                        completion_id, model_label, finish_reason=stream_chunk.finish_reason
                    )
                )
        yield sse_done()
    except ProviderError as e:
        error_raised = e
        yield sse_event({"error": {"message": e.message, "type": "provider_error"}})
        yield sse_done()

    latency_ms = (time.time() - start) * 1000

    if error_raised is not None:
        await record_usage(
            state.storage,
            api_key=api_key.key,
            model_requested=payload.model,
            model_used=outcome.model_used,
            route_tier=decision.tier,
            route_reason=decision.reason,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            latency_ms=latency_ms,
            cache_status="miss",
            outcome="error",
            provider=outcome.provider_used or error_raised.provider,
            failover_chain=outcome.attempts,
            error_type=type(error_raised).__name__,
            stream=True,
        )
        return

    full_text = "".join(collected)
    if full_text and not outcome.mid_stream_error:
        synthetic_response = ChatCompletionResponse(
            id=completion_id,
            model=outcome.model_used,
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }
            ],
            usage={
                "prompt_tokens": outcome.prompt_tokens,
                "completion_tokens": outcome.completion_tokens or len(full_text.split()),
                "total_tokens": outcome.prompt_tokens
                + (outcome.completion_tokens or len(full_text.split())),
            },
        )
        await state.cache_manager.store(payload, api_key.key, synthetic_response)

    await record_usage(
        state.storage,
        api_key=api_key.key,
        model_requested=payload.model,
        model_used=outcome.model_used,
        route_tier=decision.tier,
        route_reason=decision.reason,
        prompt_tokens=outcome.prompt_tokens,
        completion_tokens=outcome.completion_tokens,
        latency_ms=latency_ms,
        cache_status="not_cacheable" if not state.cache_manager.cacheable(payload) else "miss",
        outcome=(
            "error"
            if outcome.mid_stream_error
            else ("failover" if len(outcome.attempts) > 1 else "ok")
        ),
        provider=outcome.provider_used,
        failover_chain=outcome.attempts,
        error_type=outcome.error_type if outcome.mid_stream_error else None,
        stream=True,
    )
