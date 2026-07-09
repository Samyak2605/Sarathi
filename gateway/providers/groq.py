"""Groq adapter -- Groq exposes an OpenAI-compatible chat/completions API,
so this is a thin, faithful passthrough with our error taxonomy applied.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from gateway.providers.base import ProviderAdapter, StreamChunk
from gateway.providers.errors import (
    ProviderAuthError,
    ProviderInvalidRequestError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse

BASE_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(ProviderAdapter):
    name = "groq"
    supported_models = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise ProviderAuthError(self.name, "invalid Groq API key")
        if resp.status_code == 429:
            raise ProviderRateLimitError(self.name, "Groq rate limit hit")
        if resp.status_code >= 500:
            raise ProviderUnavailableError(self.name, f"Groq {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderInvalidRequestError(self.name, resp.text[:300])

    async def chat(
        self, request: ChatCompletionRequest, model: str, timeout_s: float
    ) -> ChatCompletionResponse:
        payload = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.response_format:
            payload["response_format"] = request.response_format
        try:
            resp = await self._client.post(BASE_URL, json=payload, timeout=timeout_s)
        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(self.name, "Groq request timed out") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailableError(self.name, "Groq connection failed") from e

        self._raise_for_status(resp)
        data = resp.json()
        data["model"] = model
        return ChatCompletionResponse(**data)

    async def chat_stream(
        self, request: ChatCompletionRequest, model: str, timeout_s: float
    ) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
            "temperature": request.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.response_format:
            payload["response_format"] = request.response_format

        try:
            async with self._client.stream(
                "POST", BASE_URL, json=payload, timeout=timeout_s
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    self._raise_for_status(
                        httpx.Response(resp.status_code, content=body, request=resp.request)
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload_str = line[len("data:") :].strip()
                    if payload_str == "[DONE]":
                        break
                    chunk = json.loads(payload_str)
                    usage = chunk.get("usage")
                    choices = chunk.get("choices") or []
                    delta = ""
                    finish_reason = None
                    if choices:
                        delta = choices[0].get("delta", {}).get("content") or ""
                        finish_reason = choices[0].get("finish_reason")
                    yield StreamChunk(
                        delta=delta,
                        finish_reason=finish_reason,
                        prompt_tokens=usage.get("prompt_tokens") if usage else None,
                        completion_tokens=usage.get("completion_tokens") if usage else None,
                    )
        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(self.name, "Groq stream timed out") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailableError(self.name, "Groq connection failed") from e
