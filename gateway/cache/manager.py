"""Two-tier semantic cache facade used by the API layer.

Tier 1: exact-match hash (near-free). Tier 2: embedding cosine similarity
>= tau, per-key namespace, TTL'd. Only requests with temperature <=
cache_max_temperature are cacheable; writes only happen after the caller
has a validated response in hand (never before).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from gateway.cache.embeddings import get_embedding_provider
from gateway.cache.exact import cache_key_material, is_cacheable, prompt_hash
from gateway.config import Settings
from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse
from gateway.storage.base import Storage
from gateway.storage.models import CacheEntry


@dataclass
class CacheHit:
    response: ChatCompletionResponse
    status: str  # "hit_exact" | "hit_semantic"
    similarity: float | None = None


class CacheManager:
    def __init__(self, storage: Storage, settings: Settings):
        self.storage = storage
        self.settings = settings

    def cacheable(self, request: ChatCompletionRequest) -> bool:
        return is_cacheable(request, self.settings.cache_max_temperature)

    async def lookup(self, request: ChatCompletionRequest, namespace: str) -> CacheHit | None:
        if not self.cacheable(request):
            return None

        p_hash = prompt_hash(request)
        exact = await self.storage.get_exact_cache(namespace, p_hash)
        if exact is not None:
            return CacheHit(
                response=ChatCompletionResponse.model_validate_json(exact.response_json),
                status="hit_exact",
            )

        embedding = get_embedding_provider().embed(cache_key_material(request))
        matches = await self.storage.semantic_search(namespace, embedding, top_k=1)
        if matches:
            entry, similarity = matches[0]
            if similarity >= self.settings.cache_similarity_threshold:
                return CacheHit(
                    response=ChatCompletionResponse.model_validate_json(entry.response_json),
                    status="hit_semantic",
                    similarity=similarity,
                )
        return None

    async def store(
        self, request: ChatCompletionRequest, namespace: str, response: ChatCompletionResponse
    ) -> None:
        if not self.cacheable(request):
            return
        now = time.time()
        embedding = get_embedding_provider().embed(cache_key_material(request))
        entry = CacheEntry(
            id=uuid.uuid4().hex,
            namespace=namespace,
            prompt_hash=prompt_hash(request),
            prompt_text=cache_key_material(request),
            embedding=embedding,
            response_json=response.model_dump_json(),
            model_used=response.model,
            created_at=now,
            expires_at=now + self.settings.cache_ttl_seconds,
        )
        await self.storage.put_cache_entry(entry)
