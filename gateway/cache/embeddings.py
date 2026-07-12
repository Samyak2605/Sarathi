"""Local embeddings for the semantic cache tier.

Primary path: BAAI/bge-small-en-v1.5 via fastembed (ONNX runtime, no GPU,
no API key -- downloads ~130MB of model weights on first use and caches
them under ./fastembed_cache/). If that download can't complete (offline
sandbox, no network), we fall back to a deterministic hashed
bag-of-words embedding of the same dimensionality so the cache tier still
works end-to-end -- clearly logged as a degraded mode, never silent.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod

import numpy as np

EMBEDDING_DIM = 384


class EmbeddingProvider(ABC):
    dim: int = EMBEDDING_DIM

    @abstractmethod
    def embed(self, text: str) -> list[float]: ...


class BgeSmallEmbeddingProvider(EmbeddingProvider):
    def __init__(self):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(
            model_name="BAAI/bge-small-en-v1.5", cache_dir="fastembed_cache"
        )

    def embed(self, text: str) -> list[float]:
        vec = next(self._model.embed([text]))
        return vec.tolist()


class HashedFallbackEmbeddingProvider(EmbeddingProvider):
    """Deterministic, dependency-free embedding used only if the real
    model can't be loaded. Feature-hashes word tokens into a fixed-size
    vector and L2-normalizes it, so cosine similarity is still meaningful
    for near-duplicate prompts (though weaker than a learned embedding).
    """

    _token_re = re.compile(r"[a-z0-9]+")

    def embed(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in self._token_re.findall(text.lower()):
            h = int(hashlib.blake2b(token.encode(), digest_size=8).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h // self.dim) % 2 == 0 else -1.0
            vec[idx] += sign
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()


_provider: EmbeddingProvider | None = None
_degraded = False


def get_embedding_provider() -> EmbeddingProvider:
    global _provider, _degraded
    if _provider is not None:
        return _provider
    try:
        _provider = BgeSmallEmbeddingProvider()
    except Exception as e:  # network/model-load failure -- degrade, don't crash
        import logging

        logging.getLogger("sarathi.cache").warning(
            "bge-small embedding model unavailable (%s); falling back to hashed "
            "embeddings for the semantic cache. Similarity quality will be lower.",
            e,
        )
        _degraded = True
        _provider = HashedFallbackEmbeddingProvider()
    return _provider


def embeddings_degraded() -> bool:
    return _degraded
