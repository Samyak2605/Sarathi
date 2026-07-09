"""Unit tests for the Groq/Gemini adapters' request/response translation
and error-taxonomy mapping, using pytest-httpx to mock the upstream HTTP
calls -- no real credentials or network access required.
"""

from __future__ import annotations

import json

import pytest

from gateway.providers.errors import (
    ProviderAuthError,
    ProviderInvalidRequestError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from gateway.providers.gemini import GeminiProvider
from gateway.providers.groq import GroqProvider
from gateway.schemas import ChatCompletionRequest

pytestmark = pytest.mark.asyncio


def _req():
    return ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}], temperature=0)


async def test_max_completion_tokens_normalizes_to_max_tokens():
    # SupportMind (and recent OpenAI/Groq SDKs) send max_completion_tokens,
    # not max_tokens -- both must land in the same field for adapters to see it.
    req = ChatCompletionRequest(
        messages=[{"role": "user", "content": "hi"}], max_completion_tokens=256
    )
    assert req.max_tokens == 256


async def test_groq_chat_success(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.groq.com/openai/v1/chat/completions",
        json={
            "id": "cmpl-1",
            "model": "llama-3.1-8b-instant",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        },
    )
    provider = GroqProvider(api_key="test-key")
    resp = await provider.chat(_req(), "llama-3.1-8b-instant", timeout_s=5)
    assert resp.choices[0].message.content == "hello"
    await provider.aclose()


async def test_groq_forwards_response_format(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.groq.com/openai/v1/chat/completions",
        json={
            "id": "cmpl-2",
            "model": "llama-3.1-8b-instant",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "{}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        },
    )
    provider = GroqProvider(api_key="test-key")
    req = ChatCompletionRequest(
        messages=[{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
    )
    await provider.chat(req, "llama-3.1-8b-instant", timeout_s=5)
    sent = json.loads(httpx_mock.get_requests()[0].content)
    assert sent["response_format"] == {"type": "json_object"}
    await provider.aclose()


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, ProviderAuthError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
        (400, ProviderInvalidRequestError),
    ],
)
async def test_groq_error_mapping(httpx_mock, status, expected):
    httpx_mock.add_response(
        method="POST",
        url="https://api.groq.com/openai/v1/chat/completions",
        status_code=status,
        text="error",
    )
    provider = GroqProvider(api_key="test-key")
    with pytest.raises(expected):
        await provider.chat(_req(), "llama-3.1-8b-instant", timeout_s=5)
    await provider.aclose()


async def test_gemini_chat_success(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=test-key",
        json={
            "candidates": [{"content": {"parts": [{"text": "hi there"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 2},
        },
    )
    provider = GeminiProvider(api_key="test-key")
    resp = await provider.chat(_req(), "gemini-2.0-flash", timeout_s=5)
    assert resp.choices[0].message.content == "hi there"
    assert resp.usage.total_tokens == 4
    await provider.aclose()


async def test_gemini_auth_error_mapping(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=bad-key",
        status_code=403,
        text="forbidden",
    )
    provider = GeminiProvider(api_key="bad-key")
    with pytest.raises(ProviderAuthError):
        await provider.chat(_req(), "gemini-2.0-flash", timeout_s=5)
    await provider.aclose()
