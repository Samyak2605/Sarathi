"""Gemini adapter. Gemini's native REST API is not OpenAI-shaped (different
message envelope, system-instruction field, usageMetadata naming) so this
adapter does the translation both ways -- this is exactly the kind of
per-provider normalization the gateway exists to hide from callers.
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
from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse, ChatMessage, Usage

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _to_gemini_payload(request: ChatCompletionRequest) -> dict:
    contents = []
    system_parts = []
    for m in request.messages:
        if m.role == "system":
            system_parts.append(m.content)
            continue
        role = "model" if m.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m.content}]})

    payload: dict = {
        "contents": contents,
        "generationConfig": {"temperature": request.temperature},
    }
    if request.max_tokens:
        payload["generationConfig"]["maxOutputTokens"] = request.max_tokens
    if (request.response_format or {}).get("type") == "json_object":
        payload["generationConfig"]["responseMimeType"] = "application/json"
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
    return payload


class GeminiProvider(ProviderAdapter):
    name = "gemini"
    supported_models = ["gemini-2.0-flash"]

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise ProviderAuthError(self.name, "invalid/forbidden Gemini API key")
        if resp.status_code == 429:
            raise ProviderRateLimitError(self.name, "Gemini rate limit hit")
        if resp.status_code >= 500:
            raise ProviderUnavailableError(self.name, f"Gemini {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderInvalidRequestError(self.name, resp.text[:300])

    async def chat(
        self, request: ChatCompletionRequest, model: str, timeout_s: float
    ) -> ChatCompletionResponse:
        url = f"{BASE_URL}/{model}:generateContent"
        try:
            resp = await self._client.post(
                url,
                params={"key": self.api_key},
                json=_to_gemini_payload(request),
                timeout=timeout_s,
            )
        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(self.name, "Gemini request timed out") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailableError(self.name, "Gemini connection failed") from e

        self._raise_for_status(resp)
        data = resp.json()
        candidate = data["candidates"][0]
        text = "".join(p.get("text", "") for p in candidate["content"]["parts"])
        usage = data.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        return ChatCompletionResponse(
            id=data.get("responseId", "gemini-response"),
            model=model,
            choices=[
                {
                    "index": 0,
                    "message": ChatMessage(role="assistant", content=text),
                    "finish_reason": candidate.get("finishReason", "stop").lower(),
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
        url = f"{BASE_URL}/{model}:streamGenerateContent"
        try:
            async with self._client.stream(
                "POST",
                url,
                params={"key": self.api_key, "alt": "sse"},
                json=_to_gemini_payload(request),
                timeout=timeout_s,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    self._raise_for_status(
                        httpx.Response(resp.status_code, content=body, request=resp.request)
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = json.loads(line[len("data:") :].strip())
                    candidate = chunk["candidates"][0]
                    text = "".join(p.get("text", "") for p in candidate["content"]["parts"])
                    finish_reason = candidate.get("finishReason")
                    usage = chunk.get("usageMetadata", {})
                    yield StreamChunk(
                        delta=text,
                        finish_reason=finish_reason.lower() if finish_reason else None,
                        prompt_tokens=usage.get("promptTokenCount"),
                        completion_tokens=usage.get("candidatesTokenCount"),
                    )
        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(self.name, "Gemini stream timed out") from e
        except httpx.ConnectError as e:
            raise ProviderUnavailableError(self.name, "Gemini connection failed") from e
