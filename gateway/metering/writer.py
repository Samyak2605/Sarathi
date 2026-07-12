from __future__ import annotations

import time
import uuid

from gateway.metering.pricing import cost_inr
from gateway.storage.base import Storage
from gateway.storage.models import UsageRecord


async def record_usage(
    storage: Storage,
    *,
    api_key: str,
    model_requested: str,
    model_used: str,
    route_tier: str | None,
    route_reason: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    cache_status: str,
    outcome: str,
    provider: str,
    failover_chain: list[str],
    error_type: str | None = None,
    stream: bool = False,
) -> UsageRecord:
    """The single write path for metering. Call this exactly once per
    inbound request -- on a cache hit, a clean miss, a failover, or an
    error -- so every request produces exactly one record.
    """
    record = UsageRecord(
        id=uuid.uuid4().hex,
        api_key=api_key,
        created_at=time.time(),
        model_requested=model_requested,
        model_used=model_used,
        route_tier=route_tier,
        route_reason=route_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cost_inr=0.0
        if cache_status.startswith("hit")
        else cost_inr(model_used, prompt_tokens, completion_tokens),
        latency_ms=latency_ms,
        cache_status=cache_status,
        outcome=outcome,
        provider=provider,
        failover_chain=failover_chain,
        error_type=error_type,
        stream=stream,
    )
    await storage.write_usage_record(record)
    return record
