from __future__ import annotations

from abc import ABC, abstractmethod

from gateway.storage.models import ApiKeyRecord, CacheEntry, UsageRecord


class Storage(ABC):
    """Backend-agnostic storage interface. LOCAL -> SQLite, LIVE -> Supabase.

    Every method here must exist in both implementations with identical
    semantics so the rest of the gateway never branches on mode.
    """

    @abstractmethod
    async def init(self) -> None: ...

    # -- API keys ---------------------------------------------------
    @abstractmethod
    async def get_api_key(self, key: str) -> ApiKeyRecord | None: ...

    @abstractmethod
    async def create_api_key(self, record: ApiKeyRecord) -> None: ...

    @abstractmethod
    async def get_spend_today(self, key: str) -> tuple[int, float]:
        """Returns (tokens_spent_today, cost_inr_spent_today)."""
        ...

    # -- Metering -----------------------------------------------------
    @abstractmethod
    async def write_usage_record(self, record: UsageRecord) -> None: ...

    @abstractmethod
    async def query_usage(
        self,
        since_ts: float = 0.0,
        api_key: str | None = None,
    ) -> list[UsageRecord]: ...

    # -- Cache ----------------------------------------------------------
    @abstractmethod
    async def get_exact_cache(self, namespace: str, prompt_hash: str) -> CacheEntry | None: ...

    @abstractmethod
    async def put_cache_entry(self, entry: CacheEntry) -> None: ...

    @abstractmethod
    async def semantic_search(
        self, namespace: str, embedding: list[float], top_k: int = 1
    ) -> list[tuple[CacheEntry, float]]:
        """Top-k (entry, cosine_similarity) matches in a namespace.

        LOCAL (SQLite): fetch candidates, cosine in numpy.
        LIVE (Supabase): pgvector `<=>` via an RPC function -- see
        docs/supabase_schema.sql.
        """
        ...

    @abstractmethod
    async def purge_expired_cache(self) -> int: ...
