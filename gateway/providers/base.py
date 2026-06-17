from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from pydantic import BaseModel

from gateway.schemas import ChatCompletionRequest, ChatCompletionResponse


class StreamChunk(BaseModel):
    delta: str
    finish_reason: str | None = None
    prompt_tokens: int | None = None  # set on the chunk that reveals final usage
    completion_tokens: int | None = None


class ProviderAdapter(ABC):
    """One adapter per upstream provider. Normalizes requests, responses,
    and errors (see errors.py). Never lets a raw SDK/HTTP exception escape.
    """

    name: str
    supported_models: list[str]

    @abstractmethod
    async def chat(
        self, request: ChatCompletionRequest, model: str, timeout_s: float
    ) -> ChatCompletionResponse: ...

    @abstractmethod
    async def chat_stream(
        self, request: ChatCompletionRequest, model: str, timeout_s: float
    ) -> AsyncIterator[StreamChunk]: ...

    async def aclose(self) -> None:
        """Override if the adapter owns a client that needs closing."""
        return None
