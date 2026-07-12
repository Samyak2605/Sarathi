"""Pricing tables for cost metering.

Rates are approximate public list prices (USD, per 1K tokens) as of early
2026 for the real models each tier maps to, converted to INR at an
assumed USD/INR = 83. Groq and Gemini are used here on their free tiers
(actual spend is Rs0), and the mock provider costs nothing to run --
these tables exist so `benchmarks/cost_report.py` can report what the
*same traffic* would have cost on each tier, which is the entire point of
the cost benchmark. Every generated report states this assumption
explicitly; nothing here is presented as a real invoice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_1k_inr: float
    output_per_1k_inr: float
    tier: str


USD_TO_INR = 83.0

PRICING: dict[str, ModelPrice] = {
    # small tier -- Groq Llama-3.1-8B-instant list price ~$0.05/$0.08 per 1M
    "llama-3.1-8b-instant": ModelPrice(0.05 / 1000 * USD_TO_INR, 0.08 / 1000 * USD_TO_INR, "small"),
    "mock-small": ModelPrice(0.05 / 1000 * USD_TO_INR, 0.08 / 1000 * USD_TO_INR, "small"),
    # mid tier -- Gemini 2.0 Flash list price ~$0.10/$0.40 per 1M
    "gemini-2.0-flash": ModelPrice(0.10 / 1000 * USD_TO_INR, 0.40 / 1000 * USD_TO_INR, "mid"),
    "mock-mid": ModelPrice(0.10 / 1000 * USD_TO_INR, 0.40 / 1000 * USD_TO_INR, "mid"),
    # large tier -- Groq Llama-3.3-70B-versatile list price ~$0.59/$0.79 per 1M
    "llama-3.3-70b-versatile": ModelPrice(
        0.59 / 1000 * USD_TO_INR, 0.79 / 1000 * USD_TO_INR, "large"
    ),
    "mock-large": ModelPrice(0.59 / 1000 * USD_TO_INR, 0.79 / 1000 * USD_TO_INR, "large"),
}

DEFAULT_PRICE = ModelPrice(0.10 / 1000 * USD_TO_INR, 0.40 / 1000 * USD_TO_INR, "mid")


def cost_inr(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICING.get(model, DEFAULT_PRICE)
    return (
        prompt_tokens / 1000 * price.input_per_1k_inr
        + completion_tokens / 1000 * price.output_per_1k_inr
    )


def tier_of(model: str) -> str:
    return PRICING.get(model, DEFAULT_PRICE).tier
