"""Heuristic complexity classifier.

No labeled training budget for a learned model in v1 (see the README
Roadmap for what a learned classifier would need), so complexity is a
weighted combination of cheap structural signals. This is exactly what
results/routing/parity.json validates offline before the policy is ever
allowed to run live traffic (see gateway/router/policy.py) -- no routing
policy ships without that evidence.
"""

from __future__ import annotations

from gateway.router.features import RouterFeatures


def complexity_score(features: RouterFeatures, config: dict) -> float:
    weights = config["complexity_weights"]
    length_norm = config["length_words_norm"]
    tokens_norm = config["max_tokens_norm"]

    length_component = min(1.0, features.prompt_words / length_norm)
    code_component = 1.0 if features.has_code_signal else 0.0
    reasoning_component = 1.0 if features.has_reasoning_signal else 0.0
    depth_component = min(1.0, features.message_count / 6.0)
    tokens_component = min(1.0, features.requested_max_tokens / tokens_norm)

    # code_signal and reasoning_signal are weighted separately (not
    # max()'d together) so a prompt that trips BOTH -- e.g. "explain this
    # traceback" -- scores meaningfully higher than one that trips only
    # one, which is what actually separates "debugging" (large) from
    # plain "write me a function" (mid) in the labeled routing dataset.
    score = (
        weights["length_words"] * length_component
        + weights["code_signal"] * code_component
        + weights["reasoning_signal"] * reasoning_component
        + weights["conversation_depth"] * depth_component
        + weights["max_tokens_requested"] * tokens_component
    )
    return max(0.0, min(1.0, score))
