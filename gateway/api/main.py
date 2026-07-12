from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.api.errors import register_error_handlers
from gateway.api.routes_admin import router as admin_router
from gateway.api.routes_chat import router as chat_router
from gateway.api.routes_dashboard import router as dashboard_router
from gateway.api.routes_models import router as models_router
from gateway.auth.dependency import ensure_local_dev_key
from gateway.auth.ratelimit import InMemoryRateLimiter, UpstashRateLimiter
from gateway.cache.manager import CacheManager
from gateway.config import get_settings
from gateway.providers.registry import build_registry
from gateway.router.policy import RoutingPolicy
from gateway.storage.factory import build_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings

    storage = await build_storage(settings)
    app.state.storage = storage
    await ensure_local_dev_key(storage)

    app.state.registry = build_registry(settings)
    app.state.routing_policy = RoutingPolicy.load(settings.routing_policy_path)
    app.state.cache_manager = CacheManager(storage, settings)

    if settings.is_live and settings.upstash_redis_rest_url and settings.upstash_redis_rest_token:
        app.state.rate_limiter = UpstashRateLimiter(
            settings.upstash_redis_rest_url, settings.upstash_redis_rest_token
        )
    else:
        app.state.rate_limiter = InMemoryRateLimiter()

    yield

    await app.state.registry.aclose()
    close = getattr(storage, "close", None)
    if close is not None:
        await close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sarathi",
        description="Self-hostable OpenAI-compatible LLM gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(chat_router)
    app.include_router(models_router)
    app.include_router(admin_router)
    app.include_router(dashboard_router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
