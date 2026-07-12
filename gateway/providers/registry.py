from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from gateway.config import Settings
from gateway.providers.base import ProviderAdapter
from gateway.providers.breaker import CircuitBreaker
from gateway.providers.gemini import GeminiProvider
from gateway.providers.groq import GroqProvider
from gateway.providers.mock import MockProvider, NamedMockProvider


@dataclass
class ChainStep:
    provider: str
    model: str


@dataclass
class FailoverPolicy:
    chains: dict[str, list[ChainStep]]
    max_attempts_per_provider: int
    backoff_base_ms: float
    backoff_max_ms: float
    timeouts: dict[str, float]
    breaker_config: dict
    stream_fallback_token_threshold: int

    @classmethod
    def load(cls, path: str) -> FailoverPolicy:
        with open(path) as f:
            raw = yaml.safe_load(f)
        chains = {
            tier: [ChainStep(**step) for step in steps] for tier, steps in raw["chains"].items()
        }
        return cls(
            chains=chains,
            max_attempts_per_provider=raw["retry"]["max_attempts_per_provider"],
            backoff_base_ms=raw["retry"]["backoff_base_ms"],
            backoff_max_ms=raw["retry"]["backoff_max_ms"],
            timeouts={
                "groq": raw["timeouts"]["groq_s"],
                "gemini": raw["timeouts"]["gemini_s"],
                "mock": raw["timeouts"]["mock_s"],
            },
            breaker_config=raw["circuit_breaker"],
            stream_fallback_token_threshold=raw["stream_fallback_token_threshold"],
        )


@dataclass
class ProviderRegistry:
    adapters: dict[str, ProviderAdapter] = field(default_factory=dict)
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)
    policy: FailoverPolicy | None = None

    def available_providers(self) -> set[str]:
        return set(self.adapters.keys())

    async def aclose(self) -> None:
        for adapter in self.adapters.values():
            await adapter.aclose()


def build_registry(settings: Settings) -> ProviderRegistry:
    policy = FailoverPolicy.load(settings.failover_policy_path)
    registry = ProviderRegistry(policy=policy)

    # Mock is always available -- the guaranteed-available safety net.
    registry.adapters["mock"] = MockProvider()

    if settings.groq_api_key:
        registry.adapters["groq"] = GroqProvider(settings.groq_api_key)
    elif settings.sarathi_demo_mode:
        import logging

        logging.getLogger("sarathi.providers").warning(
            "SARATHI_DEMO_MODE: registering a mock stand-in under the 'groq' slot "
            "(no GROQ_API_KEY set) so the failover chain has a second provider to "
            "demo against. This is never real Groq traffic."
        )
        registry.adapters["groq"] = NamedMockProvider("groq")

    if settings.gemini_api_key:
        registry.adapters["gemini"] = GeminiProvider(settings.gemini_api_key)

    for name in ("mock", "groq", "gemini"):
        registry.breakers[name] = CircuitBreaker(
            failure_rate_threshold=policy.breaker_config["failure_rate_threshold"],
            window_size=policy.breaker_config["window_size"],
            open_seconds=policy.breaker_config["open_seconds"],
        )

    return registry
