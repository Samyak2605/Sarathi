from __future__ import annotations

from pydantic import BaseModel


class ApiKeyRecord(BaseModel):
    key: str
    name: str
    daily_token_budget: int
    daily_cost_budget_inr: float
    rate_limit_per_minute: int
    created_at: float
    active: bool = True


class UsageRecord(BaseModel):
    """Exactly one of these is written per inbound request. No silent paths."""

    id: str
    api_key: str
    created_at: float
    model_requested: str
    model_used: str
    route_tier: str | None
    route_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_inr: float
    latency_ms: float
    cache_status: str  # "hit_exact" | "hit_semantic" | "miss" | "not_cacheable"
    outcome: str  # "ok" | "failover" | "error"
    provider: str
    failover_chain: list[str] = []
    error_type: str | None = None
    stream: bool = False


class CacheEntry(BaseModel):
    id: str
    namespace: str  # per-API-key namespace
    prompt_hash: str
    prompt_text: str
    embedding: list[float] | None
    response_json: str
    model_used: str
    created_at: float
    expires_at: float
