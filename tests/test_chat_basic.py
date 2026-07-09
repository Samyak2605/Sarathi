from __future__ import annotations

import json

import pytest

from tests.conftest import AUTH_HEADERS

pytestmark = pytest.mark.asyncio


async def test_non_streaming_completion_logs_cost(client, app):
    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "hello world, this is a test"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"]
    assert body["usage"]["total_tokens"] > 0
    assert body["sarathi"]["cache_status"] == "miss"
    assert body["sarathi"]["provider"] == "mock"

    records = await app.state.storage.query_usage()
    assert len(records) == 1
    assert records[0].outcome == "ok"
    assert records[0].total_tokens == body["usage"]["total_tokens"]


async def test_streaming_completion_assembles_full_response(client, app):
    full_text = ""
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "stream this please"}], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            delta = chunk["choices"][0]["delta"]
            full_text += delta.get("content", "")

    assert "mock reply" in full_text

    records = await app.state.storage.query_usage()
    assert len(records) == 1
    assert records[0].stream is True
    assert records[0].outcome == "ok"


async def test_malformed_body_returns_clean_422(client):
    # Regression: a non-JSON body (e.g. sent as form-encoded, no
    # Content-Type) used to crash the validation error handler itself --
    # exc.errors() carries the raw body as bytes, which plain JSONResponse
    # can't serialize. Must return a clean 422, not a 500.
    resp = await client.post(
        "/v1/chat/completions",
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        content=b"not json at all",
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["type"] == "invalid_request_error"


async def test_json_mode_falls_back_to_valid_json_on_mock(client):
    # A caller in JSON mode (e.g. SupportMind's agent) must get parseable
    # JSON even if the request lands on the mock provider (failover) --
    # a prose canned reply would crash json.loads() downstream.
    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {"type": "json_object"},
        },
    )
    assert resp.status_code == 200
    content = resp.json()["choices"][0]["message"]["content"]
    import json

    json.loads(content)  # must not raise


async def test_openai_prefix_alias_matches_groq_sdk_path(client):
    # The real `groq` SDK hardcodes "openai/v1/chat/completions" relative
    # to base_url (mirroring api.groq.com's own path shape) -- this alias
    # is what lets a client just swap base_url with zero code changes.
    resp = await client.post(
        "/openai/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200


async def test_models_endpoint_lists_all_tiers(client):
    resp = await client.get("/v1/models", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["data"]}
    assert {"mock-small", "mock-mid", "mock-large", "auto"} <= ids


async def test_missing_auth_rejected(client):
    resp = await client.post(
        "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "auth_error"


async def test_invalid_key_rejected(client):
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer sk-not-a-real-key"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


async def test_dashboard_renders(client):
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "Sarathi" in resp.text
