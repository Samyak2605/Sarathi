from __future__ import annotations

import httpx
import pytest

from gateway.config import get_settings


@pytest.fixture
def sqlite_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def app_settings(sqlite_path, monkeypatch):
    # pydantic-settings reads .env directly (env_file="."), so a real
    # GROQ_API_KEY/GEMINI_API_KEY on disk survives monkeypatch.delenv --
    # that only clears os.environ, not the .env file source. Force these
    # off explicitly so tests always exercise the mock-only LOCAL registry,
    # regardless of what's in the developer's local .env.
    monkeypatch.setenv("SARATHI_MODE", "local")
    monkeypatch.setenv("SQLITE_PATH", sqlite_path)
    get_settings.cache_clear()
    settings = get_settings()
    settings.groq_api_key = None
    settings.gemini_api_key = None
    settings.sarathi_demo_mode = False
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
