from __future__ import annotations

from dataclasses import dataclass

import yaml

from gateway.metering.pricing import PRICING
from gateway.router.classifier import complexity_score
from gateway.router.features import extract_features
from gateway.schemas import ChatCompletionRequest


@dataclass
class TierConfig:
    chain: str
    max_complexity: float


@dataclass
class RoutingPolicy:
    default_mode: str
    tiers: list[tuple[str, TierConfig]]  # ordered small -> large
    raw: dict

    @classmethod
    def load(cls, path: str) -> RoutingPolicy:
        with open(path) as f:
            raw = yaml.safe_load(f)
        tier_order = ["small", "mid", "large"]
        tiers = [
            (name, TierConfig(**raw["tiers"][name])) for name in tier_order if name in raw["tiers"]
        ]
        return cls(default_mode=raw["default_mode"], tiers=tiers, raw=raw)


@dataclass
class RouterDecision:
    tier: str
    chain: str
    reason: str
    complexity: float | None = None
    pinned_model: str | None = None


def decide(request: ChatCompletionRequest, policy: RoutingPolicy) -> RouterDecision:
    # Manual pin: an explicit, known model id routes straight to its tier
    # and locks the chain to that single model (no complexity scoring).
    if request.route_mode == "pin" or request.model in PRICING:
        if request.model in PRICING:
            price = PRICING[request.model]
            for name, tier_cfg in policy.tiers:
                if name == price.tier:
                    return RouterDecision(
                        tier=name,
                        chain=tier_cfg.chain,
                        reason=f"manual pin to model '{request.model}'",
                        pinned_model=request.model,
                    )

    mode = request.route_mode or policy.default_mode

    if mode == "quality-first":
        name, tier_cfg = policy.tiers[-1]
        return RouterDecision(tier=name, chain=tier_cfg.chain, reason="quality-first mode")

    # cost-first (default): pick the cheapest tier whose ceiling clears the
    # request's complexity score.
    features = extract_features(request)
    score = complexity_score(features, policy.raw)
    for name, tier_cfg in policy.tiers:
        if score <= tier_cfg.max_complexity:
            return RouterDecision(
                tier=name,
                chain=tier_cfg.chain,
                reason=f"cost-first: complexity {score:.2f} <= {tier_cfg.max_complexity}",
                complexity=score,
            )

    name, tier_cfg = policy.tiers[-1]
    return RouterDecision(
        tier=name,
        chain=tier_cfg.chain,
        reason=f"cost-first: complexity {score:.2f} (max tier)",
        complexity=score,
    )
