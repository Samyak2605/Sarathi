from __future__ import annotations

from gateway.router.policy import RoutingPolicy, decide
from gateway.schemas import ChatCompletionRequest

POLICY = RoutingPolicy.load("policies/routing.yaml")


def _req(text: str, **kwargs) -> ChatCompletionRequest:
    return ChatCompletionRequest(messages=[{"role": "user", "content": text}], **kwargs)


def test_short_prompt_routes_small():
    decision = decide(_req("hi, quick question"), POLICY)
    assert decision.tier == "small"


def test_code_heavy_prompt_routes_higher_tier():
    long_code_prompt = (
        "explain step by step why this raises a traceback:\n```def f(): return 1/0```" * 3
    )
    decision = decide(_req(long_code_prompt), POLICY)
    assert decision.tier in {"mid", "large"}


def test_quality_first_always_routes_large():
    decision = decide(_req("hi", route_mode="quality-first"), POLICY)
    assert decision.tier == "large"


def test_manual_pin_uses_models_own_tier():
    decision = decide(_req("hi", model="gemini-2.0-flash", route_mode="pin"), POLICY)
    assert decision.tier == "mid"
    assert decision.pinned_model == "gemini-2.0-flash"


def test_long_prompt_pushes_toward_large_tier():
    short_decision = decide(_req("short"), POLICY)
    long_text = " ".join(["word"] * 500)
    long_decision = decide(_req(long_text), POLICY)
    tier_order = {"small": 0, "mid": 1, "large": 2}
    assert tier_order[long_decision.tier] >= tier_order[short_decision.tier]
