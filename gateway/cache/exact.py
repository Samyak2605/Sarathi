from __future__ import annotations

import hashlib
import json

from gateway.schemas import ChatCompletionRequest


def cache_key_material(request: ChatCompletionRequest) -> str:
    """Text used for both the exact-match hash and the semantic embedding."""
    return "\n".join(f"{m.role}:{m.content}" for m in request.messages)


def prompt_hash(request: ChatCompletionRequest) -> str:
    material = json.dumps(
        {"messages": cache_key_material(request), "max_tokens": request.max_tokens},
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def is_cacheable(request: ChatCompletionRequest, max_temperature: float) -> bool:
    # Streaming requests are cacheable too -- a hit is replayed to the
    # client as a synthesized SSE stream (see gateway/api/routes_chat.py),
    # so callers get the same cost/latency win regardless of stream=True.
    return request.temperature <= max_temperature
