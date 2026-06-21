from __future__ import annotations

import json
import time
import uuid


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"


def chunk_payload(
    completion_id: str,
    model: str,
    *,
    role: str | None = None,
    content: str | None = None,
    finish_reason: str | None = None,
) -> dict:
    delta: dict = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def new_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"
