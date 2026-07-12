from __future__ import annotations

import httpx
import pytest

from gateway.config import get_settings


@pytest.fixture
def sqlite_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def app_settings(sqlite_path, monkeypatch):
    monkeypatch.setenv("SARATHI_MODE", "local")
    monkeypatch.setenv("SQLITE_PATH", sqlite_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    yield settings
    get_settings.cache_clear()


@pytest.fixture
async def app(app_settings):
    from gateway.api.main import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


AUTH_HEADERS = {"Authorization": "Bearer sk-local-dev"}
