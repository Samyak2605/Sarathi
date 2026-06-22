from __future__ import annotations

import pytest

from tests.conftest import AUTH_HEADERS

pytestmark = pytest.mark.asyncio

ADMIN_HEADERS = {"x-admin-token": "change-me"}


async def test_create_key_requires_admin_token(client):
    resp = await client.post("/admin/keys", json={"name": "test"})
    assert resp.status_code == 403


async def test_create_key_and_use_it(client):
    resp = await client.post("/admin/keys", headers=ADMIN_HEADERS, json={"name": "test-app"})
    assert resp.status_code == 200
    new_key = resp.json()["key"]

    chat = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {new_key}"},
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert chat.status_code == 200


async def test_chaos_endpoint_toggles_mock_provider(client, app):
    resp = await client.post(
        "/admin/chaos", headers=ADMIN_HEADERS, json={"provider": "mock", "inject_500": True}
    )
    assert resp.status_code == 200
    assert app.state.registry.adapters["mock"].chaos.inject_500 is True

    chat = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"messages": [{"role": "user", "content": "short prompt"}]},
    )
    assert chat.status_code == 502  # only provider in chain, now failing


async def test_chaos_endpoint_rejects_unknown_provider(client):
    resp = await client.post(
        "/admin/chaos", headers=ADMIN_HEADERS, json={"provider": "groq", "inject_500": True}
    )
    assert resp.status_code == 400
