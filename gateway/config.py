from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    sarathi_mode: str = "local"  # "local" | "live"

    groq_api_key: str | None = None
    gemini_api_key: str | None = None

    supabase_url: str | None = None
    supabase_service_key: str | None = None

    upstash_redis_rest_url: str | None = None
    upstash_redis_rest_token: str | None = None

    sarathi_admin_token: str = "change-me"

    # When true AND no real Groq/Gemini credentials are configured,
    # registers mock-backed stand-ins under the "groq"/"gemini" provider
    # slots so the full multi-provider failover chain is exercisable with
    # zero credentials -- this is what benchmarks/chaos/run_chaos_test.py
    # and the manual chaos-demo recording use. Never enabled by default;
    # has no effect in LIVE mode with real keys set.
    sarathi_demo_mode: bool = False

    sqlite_path: str = str(REPO_ROOT / "data" / "sarathi.db")

    routing_policy_path: str = str(REPO_ROOT / "policies" / "routing.yaml")
    failover_policy_path: str = str(REPO_ROOT / "policies" / "failover.yaml")

    # Calibrated from results/cache/tau_sweep.json: 0.86 held hit_rate=1.0
    # but false_hit_rate=0.24 on confusable prompts; 0.90 keeps hit_rate=1.0
    # while cutting false_hit_rate to 0.04. See benchmarks/cache/tau_sweep.py.
    cache_similarity_threshold: float = 0.90
    cache_ttl_seconds: int = 60 * 60 * 24
    cache_max_temperature: float = 0.3

    # streaming fallback policy: if fewer than this many tokens have been
    # emitted when a stream dies, restart on the fallback provider instead
    # of surfacing an error to the client.
    stream_fallback_token_threshold: int = 8

    @property
    def is_live(self) -> bool:
        return self.sarathi_mode == "live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
