"""LLM-judge used by both the nightly canary probes and the offline
routing parity eval.

Judging is done by calling the gateway's own large-tier chain with a
rubric prompt -- in LIVE mode that's a real 70B-class model judging
win/tie/loss; no separate judge infrastructure needed. If the model's
reply can't be parsed as WIN/LOSS/TIE (which is what happens against the
mock provider's canned replies, since it never emits those words), we
fall back to an embedding-similarity heuristic and label the verdict
`mode="heuristic_fallback"` so results are never silently mislabeled as
a real LLM judgment.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from gateway.cache.embeddings import get_embedding_provider
from gateway.providers.failover import chat_with_failover
from gateway.providers.registry import ProviderRegistry
from gateway.schemas import ChatCompletionRequest

RUBRIC = """You are grading whether two AI answers to the same question are \
equivalent in quality and correctness for a real user.

Question: {question}

Answer A (reference): {reference}

Answer B (candidate): {candidate}

Reply with exactly one word: WIN if B is clearly better than A, LOSS if B is \
clearly worse than A, or TIE if they are roughly equivalent in usefulness \
and correctness."""


class JudgeVerdict(BaseModel):
    verdict: str  # "win" | "tie" | "loss"
    mode: str  # "llm" | "heuristic_fallback"
    raw: str = ""


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / ((np.linalg.norm(va) * np.linalg.norm(vb)) + 1e-8))


async def judge(
    registry: ProviderRegistry, question: str, reference: str, candidate: str
) -> JudgeVerdict:
    prompt = RUBRIC.format(question=question, reference=reference, candidate=candidate)
    request = ChatCompletionRequest(
        messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=10
    )
    try:
        response, _ = await chat_with_failover(registry, "large", request)
        raw = response.choices[0].message.content.strip().upper()
    except Exception:
        raw = ""

    if "WIN" in raw:
        return JudgeVerdict(verdict="win", mode="llm", raw=raw)
    if "LOSS" in raw:
        return JudgeVerdict(verdict="loss", mode="llm", raw=raw)
    if "TIE" in raw:
        return JudgeVerdict(verdict="tie", mode="llm", raw=raw)

    embedder = get_embedding_provider()
    similarity = _cosine(embedder.embed(reference), embedder.embed(candidate))
    verdict = "tie" if similarity >= 0.80 else "loss"
    return JudgeVerdict(
        verdict=verdict, mode="heuristic_fallback", raw=f"similarity={similarity:.3f}"
    )
