"""LIVE-mode storage backend: Supabase Postgres via the PostgREST HTTP API.

Same Storage interface as SQLiteStorage. Requires SUPABASE_URL and
SUPABASE_SERVICE_KEY in .env. Run docs/supabase_schema.sql once against
the project (Supabase SQL editor) before first use.
"""

from __future__ import annotations

import time

import httpx

from gateway.storage.base import Storage
from gateway.storage.models import ApiKeyRecord, CacheEntry, UsageRecord


class SupabaseStorage(Storage):
    def __init__(self, url: str, service_key: str):
        self.base_url = url.rstrip("/") + "/rest/v1"
        self._client = httpx.AsyncClient(
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            timeout=10.0,
        )

    async def init(self) -> None:
        # Schema is provisioned out-of-band via docs/supabase_schema.sql
        # (PostgREST has no DDL endpoint). Verify connectivity instead.
        resp = await self._client.get(f"{self.base_url}/api_keys", params={"limit": 1})
        resp.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()

    async def get_api_key(self, key: str) -> ApiKeyRecord | None:
        resp = await self._client.get(f"{self.base_url}/api_keys", params={"key": f"eq.{key}"})
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        r = rows[0]
        return ApiKeyRecord(**r)

    async def create_api_key(self, record: ApiKeyRecord) -> None:
        resp = await self._client.post(f"{self.base_url}/api_keys", json=record.model_dump())
        resp.raise_for_status()

    async def get_spend_today(self, key: str) -> tuple[int, float]:
        since = time.time() - 86400
        resp = await self._client.get(
            f"{self.base_url}/usage_records",
            params={"api_key": f"eq.{key}", "created_at": f"gte.{since}"},
        )
        resp.raise_for_status()
        rows = resp.json()
        tokens = sum(r["total_tokens"] for r in rows)
        cost = sum(r["cost_inr"] for r in rows)
        return tokens, cost

    async def write_usage_record(self, record: UsageRecord) -> None:
        resp = await self._client.post(f"{self.base_url}/usage_records", json=record.model_dump())
        resp.raise_for_status()

    async def query_usage(
        self,
        since_ts: float = 0.0,
        api_key: str | None = None,
    ) -> list[UsageRecord]:
        params = {"created_at": f"gte.{since_ts}", "order": "created_at.desc"}
        if api_key:
            params["api_key"] = f"eq.{api_key}"
        resp = await self._client.get(f"{self.base_url}/usage_records", params=params)
        resp.raise_for_status()
        return [UsageRecord(**r) for r in resp.json()]

    async def get_exact_cache(self, namespace: str, prompt_hash: str) -> CacheEntry | None:
        now = time.time()
        resp = await self._client.get(
            f"{self.base_url}/cache_entries",
            params={
                "namespace": f"eq.{namespace}",
                "prompt_hash": f"eq.{prompt_hash}",
                "expires_at": f"gt.{now}",
                "order": "created_at.desc",
                "limit": 1,
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        return CacheEntry(**rows[0]) if rows else None

    async def put_cache_entry(self, entry: CacheEntry) -> None:
        resp = await self._client.post(f"{self.base_url}/cache_entries", json=entry.model_dump())
        resp.raise_for_status()

    async def semantic_search(
        self, namespace: str, embedding: list[float], top_k: int = 1
    ) -> list[tuple[CacheEntry, float]]:
        # Uses the match_cache_entries() Postgres function (pgvector `<=>`
        # operator) defined in docs/supabase_schema.sql.
        resp = await self._client.post(
            f"{self.base_url}/rpc/match_cache_entries",
            json={
                "p_namespace": namespace,
                "p_query_embedding": embedding,
                "p_match_count": top_k,
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        out: list[tuple[CacheEntry, float]] = []
        for r in rows:
            similarity = r.pop("similarity")
            emb = r.get("embedding")
            if isinstance(emb, str):
                # PostgREST serializes pgvector columns as "[0.1,0.2,...]" strings.
                r["embedding"] = [float(x) for x in emb.strip("[]").split(",") if x]
            out.append((CacheEntry(**r), float(similarity)))
        return out

    async def purge_expired_cache(self) -> int:
        now = time.time()
        resp = await self._client.delete(
            f"{self.base_url}/cache_entries", params={"expires_at": f"lte.{now}"}
        )
        resp.raise_for_status()
        rows = resp.json() if resp.content else []
        return len(rows)
