from __future__ import annotations

import time

from fastapi import HTTPException, Request

from gateway.auth.ratelimit import RateLimiter
from gateway.storage.base import Storage
from gateway.storage.models import ApiKeyRecord

LOCAL_DEV_KEY = "sk-local-dev"


async def ensure_local_dev_key(storage: Storage) -> None:
    existing = await storage.get_api_key(LOCAL_DEV_KEY)
    if existing is None:
        await storage.create_api_key(
            ApiKeyRecord(
                key=LOCAL_DEV_KEY,
                name="local-dev",
                daily_token_budget=2_000_000,
                daily_cost_budget_inr=1000.0,
                rate_limit_per_minute=600,
                created_at=time.time(),
            )
        )


async def authenticate(request: Request) -> ApiKeyRecord:
    storage: Storage = request.app.state.storage
    rate_limiter: RateLimiter = request.app.state.rate_limiter

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"message": "Missing or malformed Authorization header", "type": "auth_error"},
        )
    api_key = auth_header[len("Bearer ") :].strip()

    record = await storage.get_api_key(api_key)
    if record is None or not record.active:
        raise HTTPException(
            status_code=401,
            detail={"message": "Invalid API key", "type": "auth_error"},
        )

    if not await rate_limiter.allow(api_key, record.rate_limit_per_minute):
        raise HTTPException(
            status_code=429,
            detail={"message": "Rate limit exceeded", "type": "rate_limit_error"},
        )

    tokens_spent, cost_spent = await storage.get_spend_today(api_key)
    if tokens_spent >= record.daily_token_budget:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Daily token budget of {record.daily_token_budget} exceeded",
                "type": "budget_exceeded_error",
            },
        )
    if cost_spent >= record.daily_cost_budget_inr:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"Daily cost budget of Rs{record.daily_cost_budget_inr} exceeded",
                "type": "budget_exceeded_error",
            },
        )

    return record
