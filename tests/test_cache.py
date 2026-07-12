from __future__ import annotations

import pytest

from tests.conftest import AUTH_HEADERS

pytestmark = pytest.mark.asyncio


async def test_exact_cache_hit_on_identical_prompt(client, app):
    body = {
        "messages": [{"role": "user", "content": "what is the capital of France"}],
        "temperature": 0,
    }
    first = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=body)
    second = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=body)

    assert first.json()["sarathi"]["cache_status"] == "miss"
    assert second.json()["sarathi"]["cache_status"] == "hit_exact"
    assert (
        second.json()["choices"][0]["message"]["content"]
        == (first.json()["choices"][0]["message"]["content"])
    )

    records = await app.state.storage.query_usage()
    assert len(records) == 2
    hit_record = next(r for r in records if r.cache_status == "hit_exact")
    assert hit_record.cost_inr == 0.0


async def test_semantic_cache_hit_on_near_duplicate(client):
    first = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "messages": [{"role": "user", "content": "deterministic caching test prompt words"}],
            "temperature": 0,
        },
    )
    second = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "messages": [{"role": "user", "content": "deterministic caching test prompts word"}],
            "temperature": 0,
        },
    )
    assert first.json()["sarathi"]["cache_status"] == "miss"
    assert second.json()["sarathi"]["cache_status"] == "hit_semantic"
    assert second.json()["sarathi"]["similarity"] >= 0.86


async def test_high_temperature_never_cached(client):
    body = {
        "messages": [{"role": "user", "content": "high temperature request should not cache"}],
        "temperature": 0.9,
    }
    first = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=body)
    second = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=body)
    assert first.json()["sarathi"]["cache_status"] == "miss"
    assert second.json()["sarathi"]["cache_status"] == "miss"


async def test_cache_namespaced_per_api_key(app):
    from gateway.schemas import ChatCompletionRequest

    cache = app.state.cache_manager
    req = ChatCompletionRequest(
        messages=[{"role": "user", "content": "namespace isolation check"}], temperature=0
    )
    from gateway.schemas import ChatCompletionResponse

    fake_response = ChatCompletionResponse(
        id="x",
        model="mock-small",
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": "cached"},
                "finish_reason": "stop",
            }
        ],
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    await cache.store(req, "key-a", fake_response)
    hit_same_ns = await cache.lookup(req, "key-a")
    hit_other_ns = await cache.lookup(req, "key-b")
    assert hit_same_ns is not None
    assert hit_other_ns is None
