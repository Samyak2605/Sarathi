"""Built-in mock provider. Zero credentials, deterministic-enough to test
against, with chaos flags for reliability testing (blackhole, inject-500s,
inject-latency, die-mid-stream). This is what makes the whole gateway
testable offline.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator

from pydantic import BaseModel

from gateway.providers.base import ProviderAdapter, StreamChunk
from gateway.providers.errors import ProviderUnavailableError
from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse, ChatMessage, Usage


class ChaosConfig(BaseModel):
    blackhole: bool = False
    inject_500: bool = False
    inject_latency_ms: float = 0.0
    error_rate: float = 0.0
    # deterministic mid-stream death: raise right after this many tokens
    # have been emitted. None disables it.
    die_mid_stream_after_tokens: int | None = None


class MockProvider(ProviderAdapter):
    name = "mock"
    supported_models = ["mock-small", "mock-mid", "mock-large"]

    def __init__(self, chaos: ChaosConfig | None = None, base_latency_ms: float = 30.0):
        self.chaos = chaos or ChaosConfig()
        self.base_latency_ms = base_latency_ms

    def _maybe_fail(self) -> None:
        if self.chaos.inject_500:
            raise ProviderUnavailableError(self.name, "chaos: inject_500 flag set")
        if self.chaos.error_rate and random.random() < self.chaos.error_rate:
            raise ProviderUnavailableError(self.name, "chaos: random error_rate triggered")

    async def _maybe_blackhole(self, timeout_s: float) -> None:
        if self.chaos.blackhole:
            await asyncio.sleep(timeout_s + 5)  # never resolves before caller's timeout

    def _fake_reply(self, request: ChatCompletionRequest) -> str:
        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        word_count = max(1, len(last_user.split()))
        if (request.response_format or {}).get("type") == "json_object":
            # A caller requesting JSON mode will try to json.loads() this --
            # a prose reply would crash it. Valid-but-empty is the honest
            # answer for "the real provider was unavailable," not a fake
            # structured payload pretending to be a real completion.
            return "{}"
        return (
            f"[mock reply] Acknowledged a {word_count}-word prompt. "
            f"This is a deterministic canned completion used for offline "
            f"testing and load/chaos benchmarking of the Sarathi gateway."
        )

    async def chat(
        self, request: ChatCompletionRequest, model: str, timeout_s: float
    ) -> ChatCompletionResponse:
        await self._maybe_blackhole(timeout_s)
        self._maybe_fail()
        await asyncio.sleep((self.base_latency_ms + self.chaos.inject_latency_ms) / 1000)

        content = self._fake_reply(request)
        prompt_tokens = sum(len(m.content.split()) for m in request.messages)
        completion_tokens = len(content.split())
        return ChatCompletionResponse(
            id=f"mock-{uuid.uuid4().hex[:12]}",
            model=model,
            choices=[
                {
                    "index": 0,
                    "message": ChatMessage(role="assistant", content=content),
                    "finish_reason": "stop",
                }
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    async def chat_stream(
        self, request: ChatCompletionRequest, model: str, timeout_s: float
    ) -> AsyncIterator[StreamChunk]:
        await self._maybe_blackhole(timeout_s)
        self._maybe_fail()

        content = self._fake_reply(request)
        words = content.split(" ")
        prompt_tokens = sum(len(m.content.split()) for m in request.messages)

        for i, word in enumerate(words):
            if (
                self.chaos.die_mid_stream_after_tokens is not None
                and i == self.chaos.die_mid_stream_after_tokens
            ):
                raise ProviderUnavailableError(self.name, "chaos: die-mid-stream triggered")
            await asyncio.sleep(self.base_latency_ms / 1000 / 5)
            yield StreamChunk(delta=word + " ")

        yield StreamChunk(
            delta="",
            finish_reason="stop",
            prompt_tokens=prompt_tokens,
            completion_tokens=len(words),
        )


class NamedMockProvider(MockProvider):
    """A MockProvider registered under a different provider slot name.

    Used to (a) simulate multi-provider failover chains in tests without
    real Groq/Gemini credentials, and (b) back SARATHI_DEMO_MODE, which
    registers one of these under "groq" so the chaos benchmark and demo
    recording can show a real kill -> failover -> zero-dropped-requests
    sequence with zero credentials. Always clearly labeled -- never
    mistaken for real provider traffic.
    """

    def __init__(self, name: str, chaos: ChaosConfig | None = None, base_latency_ms: float = 30.0):
        super().__init__(chaos=chaos, base_latency_ms=base_latency_ms)
        self.name = name
