from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from gateway.providers.mock import ChaosConfig, MockProvider
from gateway.storage.models import ApiKeyRecord

router = APIRouter(prefix="/admin")


class CreateKeyRequest(BaseModel):
    name: str
    daily_token_budget: int = 100_000
    daily_cost_budget_inr: float = 100.0
    rate_limit_per_minute: int = 120


class CreateKeyResponse(BaseModel):
    key: str
    name: str


def _require_admin(request: Request) -> None:
    provided = request.headers.get("x-admin-token", "")
    expected = request.app.state.settings.sarathi_admin_token
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=403,
            detail={"message": "Invalid admin token", "type": "auth_error"},
        )


@router.post("/keys", response_model=CreateKeyResponse)
async def create_key(payload: CreateKeyRequest, request: Request) -> CreateKeyResponse:
    _require_admin(request)
    storage = request.app.state.storage
    key = f"sk-{secrets.token_hex(20)}"
    await storage.create_api_key(
        ApiKeyRecord(
            key=key,
            name=payload.name,
            daily_token_budget=payload.daily_token_budget,
            daily_cost_budget_inr=payload.daily_cost_budget_inr,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            created_at=time.time(),
        )
    )
    return CreateKeyResponse(key=key, name=payload.name)


class ChaosRequest(BaseModel):
    provider: str = "mock"
    blackhole: bool = False
    inject_500: bool = False
    inject_latency_ms: float = 0.0
    error_rate: float = 0.0
    die_mid_stream_after_tokens: int | None = None


@router.post("/chaos")
async def set_chaos(payload: ChaosRequest, request: Request) -> dict:
    """Fault injection for reliability demos and the chaos benchmark
    (benchmarks/chaos/run_chaos_test.py) -- the mechanism behind "kill a
    provider mid-load-test on camera". Only the mock provider implements
    ChaosConfig; real Groq/Gemini traffic can't be faked into failing.
    """
    _require_admin(request)
    registry = request.app.state.registry
    adapter = registry.adapters.get(payload.provider)
    if adapter is None or not isinstance(adapter, MockProvider):
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"provider '{payload.provider}' does not support chaos injection "
                "(only the mock provider does)",
                "type": "invalid_request_error",
            },
        )
    adapter.chaos = ChaosConfig(
        blackhole=payload.blackhole,
        inject_500=payload.inject_500,
        inject_latency_ms=payload.inject_latency_ms,
        error_rate=payload.error_rate,
        die_mid_stream_after_tokens=payload.die_mid_stream_after_tokens,
    )
    return {"provider": payload.provider, "chaos": adapter.chaos.model_dump()}


@router.get("/breakers")
async def get_breakers(request: Request) -> dict:
    """Read-only circuit breaker snapshot -- lets the chaos benchmark (and
    the manual demo recipe) poll breaker state transitions over HTTP
    without needing in-process access to the registry.
    """
    _require_admin(request)
    registry = request.app.state.registry
    return {name: breaker.snapshot() for name, breaker in registry.breakers.items()}
