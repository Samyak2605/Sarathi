from __future__ import annotations

import re

from pydantic import BaseModel

from gateway.schemas import ChatCompletionRequest

CODE_SIGNAL_PATTERN = re.compile(
    r"```|\bdef \b|\bclass \b|\bfunction\b|\bimport \b|SELECT .* FROM|<\w+>|\btraceback\b",
    re.IGNORECASE,
)
REASONING_SIGNAL_PATTERN = re.compile(
    r"\bwhy\b|\bexplain\b|\bstep by step\b|\bprove\b|\bdesign\b|\bcompare\b|\btrade-?off\b",
    re.IGNORECASE,
)


class RouterFeatures(BaseModel):
    prompt_words: int
    message_count: int
    has_code_signal: bool
    has_reasoning_signal: bool
    requested_max_tokens: int


def extract_features(request: ChatCompletionRequest) -> RouterFeatures:
    full_text = " ".join(m.content for m in request.messages)
    return RouterFeatures(
        prompt_words=len(full_text.split()),
        message_count=len(request.messages),
        has_code_signal=bool(CODE_SIGNAL_PATTERN.search(full_text)),
        has_reasoning_signal=bool(REASONING_SIGNAL_PATTERN.search(full_text)),
        requested_max_tokens=request.max_tokens or 256,
    )
