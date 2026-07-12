from __future__ import annotations

import json

import pytest

from gateway.providers.mock import ChaosConfig
from tests.conftest import AUTH_HEADERS
from tests.helpers import NamedMockProvider

pytestmark = pytest.mark.asyncio


async def _stream_and_collect(client, body):
    events = []
    async with client.stream(
        "POST", "/v1/chat/completions", headers=AUTH_HEADERS, json=body
    ) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            events.append(json.loads(payload))
    return events


async def test_nonstreaming_fails_over_to_healthy_provider(client, app):
    registry = app.state.registry
    registry.adapters["groq"] = NamedMockProvider("groq", chaos=ChaosConfig(inject_500=True))

    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "short prompt"}]},
    )
    assert resp.status_code == 200
    sarathi = resp.json()["sarathi"]
    assert sarathi["provider"] == "mock"
    assert "groq" in sarathi["failover_chain"]

    records = await app.state.storage.query_usage()
    assert len(records) == 1
    assert records[0].outcome == "failover"


async def test_nonstreaming_all_providers_down_returns_502(client, app):
    registry = app.state.registry
    registry.adapters["groq"] = NamedMockProvider("groq", chaos=ChaosConfig(inject_500=True))
    registry.adapters["mock"] = NamedMockProvider("mock", chaos=ChaosConfig(inject_500=True))

    resp = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "short prompt"}]},
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "provider_error"

    records = await app.state.storage.query_usage()
    assert len(records) == 1
    assert records[0].outcome == "error"


async def test_stream_dies_before_threshold_silently_fails_over(client, app):
    registry = app.state.registry
    registry.adapters["groq"] = NamedMockProvider(
        "groq", chaos=ChaosConfig(die_mid_stream_after_tokens=2)
    )
    # mock (fallback) is healthy by default

    events = await _stream_and_collect(
        client,
        {"messages": [{"role": "user", "content": "short prompt"}], "stream": True},
    )
    assert not any("error" in e for e in events)
    full_text = "".join(e["choices"][0]["delta"].get("content", "") for e in events)
    assert "mock reply" in full_text

    records = await app.state.storage.query_usage()
    assert len(records) == 1
    assert records[0].outcome == "failover"
    assert "groq" in records[0].failover_chain


async def test_stream_dies_after_threshold_surfaces_graceful_error(client, app):
    registry = app.state.registry
    # Only provider in the small-tier chain that's actually configured is
    # "mock" -- make IT die after the buffer has already committed
    # (threshold=8), so there's nowhere left to fail over to.
    registry.adapters["mock"] = NamedMockProvider(
        "mock", chaos=ChaosConfig(die_mid_stream_after_tokens=15)
    )

    events = await _stream_and_collect(
        client,
        {"messages": [{"role": "user", "content": "short prompt"}], "stream": True},
    )
    assert any("error" in e for e in events)

    records = await app.state.storage.query_usage()
    assert len(records) == 1
    assert records[0].outcome == "error"


async def test_stream_dies_after_threshold_does_not_retry_even_if_fallback_exists(client, app):
    registry = app.state.registry
    registry.adapters["groq"] = NamedMockProvider(
        "groq", chaos=ChaosConfig(die_mid_stream_after_tokens=15)
    )
    # mock stays healthy, but must NOT be used once groq has committed past
    # the threshold -- you can't un-send a partial answer.

    events = await _stream_and_collect(
        client,
        {"messages": [{"role": "user", "content": "short prompt"}], "stream": True},
    )
    assert any("error" in e for e in events)
    full_text = "".join(
        e["choices"][0]["delta"].get("content", "") for e in events if "error" not in e
    )
    # exactly the 15 words groq emitted before dying, nothing from mock
    assert len(full_text.split()) == 15

    records = await app.state.storage.query_usage()
    assert len(records) == 1
    assert records[0].outcome == "error"
    assert records[0].failover_chain == ["groq"]
