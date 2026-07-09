"""OpenAI-compatible request/response schemas plus Sarathi's internal types."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage]
    temperature: float = 1.0
    max_tokens: int | None = None
    # OpenAI's newer name for the same field (what recent openai-python /
    # Groq SDK clients send). Normalized into max_tokens below so the rest
    # of the gateway only ever deals with one field.
    max_completion_tokens: int | None = None
    response_format: dict | None = None
    stream: bool = False
    user: str | None = None

    # Sarathi extensions (ignored by strict OpenAI clients, all optional)
    route_mode: Literal["cost-first", "quality-first", "pin"] | None = None

    @model_validator(mode="after")
    def _normalize_max_tokens(self) -> ChatCompletionRequest:
        if self.max_tokens is None and self.max_completion_tokens is not None:
            self.max_tokens = self.max_completion_tokens
        return self


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChatCompletionChoice]
    usage: Usage

    # Sarathi extensions surfaced for transparency (non-standard field,
    # additive only — does not break OpenAI-compatible clients).
    sarathi: dict | None = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "sarathi"
    tier: str | None = None


class ModelList(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


class ErrorDetail(BaseModel):
    message: str
    type: str
    code: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
