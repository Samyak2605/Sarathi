"""Rate limiting. LOCAL mode: in-memory token bucket. LIVE mode: same
token-bucket algorithm, implemented against Upstash Redis so it survives
process restarts and works across multiple gateway instances.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx


class RateLimiter(ABC):
    @abstractmethod
    async def allow(self, key: str, capacity_per_minute: int) -> bool: ...


class InMemoryRateLimiter(RateLimiter):
    def __init__(self):
        # key -> (tokens_remaining, last_refill_ts)
        self._buckets: dict[str, tuple[float, float]] = {}

    async def allow(self, key: str, capacity_per_minute: int) -> bool:
        now = time.time()
        tokens, last_refill = self._buckets.get(key, (float(capacity_per_minute), now))
        elapsed = now - last_refill
        refill_rate_per_s = capacity_per_minute / 60.0
        tokens = min(capacity_per_minute, tokens + elapsed * refill_rate_per_s)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        tokens -= 1
        self._buckets[key] = (tokens, now)
        return True


class UpstashRateLimiter(RateLimiter):
    """Fixed-window counter via Upstash Redis REST API (INCR + EXPIRE).

    Simpler than a true token bucket but sufficient for a per-minute cap
    and safe under concurrent gateway instances, unlike the in-process
    version.
    """

    def __init__(self, rest_url: str, rest_token: str):
        self.rest_url = rest_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {rest_token}"}, timeout=5.0
        )

    async def allow(self, key: str, capacity_per_minute: int) -> bool:
        window = int(time.time() // 60)
        redis_key = f"ratelimit:{key}:{window}"
        resp = await self._client.post(f"{self.rest_url}/incr/{redis_key}")
        resp.raise_for_status()
        count = resp.json()["result"]
        if count == 1:
            await self._client.post(f"{self.rest_url}/expire/{redis_key}/60")
        return count <= capacity_per_minute

    async def aclose(self) -> None:
        await self._client.aclose()
