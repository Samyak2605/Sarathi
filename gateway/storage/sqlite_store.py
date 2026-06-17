from __future__ import annotations

import json
import time
from pathlib import Path

import aiosqlite

from gateway.storage.base import Storage
from gateway.storage.models import ApiKeyRecord, CacheEntry, UsageRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    daily_token_budget INTEGER NOT NULL,
    daily_cost_budget_inr REAL NOT NULL,
    rate_limit_per_minute INTEGER NOT NULL,
    created_at REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS usage_records (
    id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    model_requested TEXT NOT NULL,
    model_used TEXT NOT NULL,
    route_tier TEXT,
    route_reason TEXT,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cost_inr REAL NOT NULL,
    latency_ms REAL NOT NULL,
    cache_status TEXT NOT NULL,
    outcome TEXT NOT NULL,
    provider TEXT NOT NULL,
    failover_chain TEXT NOT NULL,
    error_type TEXT,
    stream INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_key_time ON usage_records(api_key, created_at);

CREATE TABLE IF NOT EXISTS cache_entries (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    embedding TEXT,
    response_json TEXT NOT NULL,
    model_used TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_ns_hash ON cache_entries(namespace, prompt_hash);
CREATE INDEX IF NOT EXISTS idx_cache_ns ON cache_entries(namespace);
"""


class SQLiteStorage(Storage):
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "call init() first"
        return self._db

    async def init(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()

    # -- API keys ---------------------------------------------------------
    async def get_api_key(self, key: str) -> ApiKeyRecord | None:
        cur = await self.db.execute("SELECT * FROM api_keys WHERE key = ?", (key,))
        row = await cur.fetchone()
        if row is None:
            return None
        return ApiKeyRecord(
            key=row["key"],
            name=row["name"],
            daily_token_budget=row["daily_token_budget"],
            daily_cost_budget_inr=row["daily_cost_budget_inr"],
            rate_limit_per_minute=row["rate_limit_per_minute"],
            created_at=row["created_at"],
            active=bool(row["active"]),
        )

    async def create_api_key(self, record: ApiKeyRecord) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO api_keys
               (key, name, daily_token_budget, daily_cost_budget_inr,
                rate_limit_per_minute, created_at, active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record.key,
                record.name,
                record.daily_token_budget,
                record.daily_cost_budget_inr,
                record.rate_limit_per_minute,
                record.created_at,
                int(record.active),
            ),
        )
        await self.db.commit()

    async def get_spend_today(self, key: str) -> tuple[int, float]:
        since = time.time() - 86400
        cur = await self.db.execute(
            """SELECT COALESCE(SUM(total_tokens), 0), COALESCE(SUM(cost_inr), 0)
               FROM usage_records WHERE api_key = ? AND created_at >= ?""",
            (key, since),
        )
        row = await cur.fetchone()
        return int(row[0]), float(row[1])

    # -- Metering -----------------------------------------------------------
    async def write_usage_record(self, record: UsageRecord) -> None:
        await self.db.execute(
            """INSERT INTO usage_records
               (id, api_key, created_at, model_requested, model_used, route_tier,
                route_reason, prompt_tokens, completion_tokens, total_tokens,
                cost_inr, latency_ms, cache_status, outcome, provider,
                failover_chain, error_type, stream)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record.id,
                record.api_key,
                record.created_at,
                record.model_requested,
                record.model_used,
                record.route_tier,
                record.route_reason,
                record.prompt_tokens,
                record.completion_tokens,
                record.total_tokens,
                record.cost_inr,
                record.latency_ms,
                record.cache_status,
                record.outcome,
                record.provider,
                json.dumps(record.failover_chain),
                record.error_type,
                int(record.stream),
            ),
        )
        await self.db.commit()

    async def query_usage(
        self,
        since_ts: float = 0.0,
        api_key: str | None = None,
    ) -> list[UsageRecord]:
        if api_key:
            cur = await self.db.execute(
                "SELECT * FROM usage_records WHERE created_at >= ? AND api_key = ? "
                "ORDER BY created_at DESC",
                (since_ts, api_key),
            )
        else:
            cur = await self.db.execute(
                "SELECT * FROM usage_records WHERE created_at >= ? ORDER BY created_at DESC",
                (since_ts,),
            )
        rows = await cur.fetchall()
        return [
            UsageRecord(
                id=r["id"],
                api_key=r["api_key"],
                created_at=r["created_at"],
                model_requested=r["model_requested"],
                model_used=r["model_used"],
                route_tier=r["route_tier"],
                route_reason=r["route_reason"],
                prompt_tokens=r["prompt_tokens"],
                completion_tokens=r["completion_tokens"],
                total_tokens=r["total_tokens"],
                cost_inr=r["cost_inr"],
                latency_ms=r["latency_ms"],
                cache_status=r["cache_status"],
                outcome=r["outcome"],
                provider=r["provider"],
                failover_chain=json.loads(r["failover_chain"]),
                error_type=r["error_type"],
                stream=bool(r["stream"]),
            )
            for r in rows
        ]

    # -- Cache ------------------------------------------------------------
    async def get_exact_cache(self, namespace: str, prompt_hash: str) -> CacheEntry | None:
        now = time.time()
        cur = await self.db.execute(
            """SELECT * FROM cache_entries
               WHERE namespace = ? AND prompt_hash = ? AND expires_at > ?
               ORDER BY created_at DESC LIMIT 1""",
            (namespace, prompt_hash, now),
        )
        row = await cur.fetchone()
        return self._row_to_entry(row) if row else None

    async def put_cache_entry(self, entry: CacheEntry) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO cache_entries
               (id, namespace, prompt_hash, prompt_text, embedding, response_json,
                model_used, created_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                entry.id,
                entry.namespace,
                entry.prompt_hash,
                entry.prompt_text,
                json.dumps(entry.embedding) if entry.embedding is not None else None,
                entry.response_json,
                entry.model_used,
                entry.created_at,
                entry.expires_at,
            ),
        )
        await self.db.commit()

    async def semantic_search(
        self, namespace: str, embedding: list[float], top_k: int = 1
    ) -> list[tuple[CacheEntry, float]]:
        import numpy as np

        now = time.time()
        cur = await self.db.execute(
            """SELECT * FROM cache_entries
               WHERE namespace = ? AND expires_at > ? AND embedding IS NOT NULL""",
            (namespace, now),
        )
        rows = await cur.fetchall()
        if not rows:
            return []
        candidates = [self._row_to_entry(r) for r in rows]
        query = np.array(embedding, dtype=np.float32)
        query_norm = query / (np.linalg.norm(query) + 1e-8)
        scored: list[tuple[CacheEntry, float]] = []
        for entry in candidates:
            vec = np.array(entry.embedding, dtype=np.float32)
            vec_norm = vec / (np.linalg.norm(vec) + 1e-8)
            sim = float(np.dot(query_norm, vec_norm))
            scored.append((entry, sim))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    async def purge_expired_cache(self) -> int:
        now = time.time()
        cur = await self.db.execute("DELETE FROM cache_entries WHERE expires_at <= ?", (now,))
        await self.db.commit()
        return cur.rowcount or 0

    @staticmethod
    def _row_to_entry(row) -> CacheEntry:
        return CacheEntry(
            id=row["id"],
            namespace=row["namespace"],
            prompt_hash=row["prompt_hash"],
            prompt_text=row["prompt_text"],
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
            response_json=row["response_json"],
            model_used=row["model_used"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )
