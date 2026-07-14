"""Benchmark table #1 (cost): replays 1,000 requests through Sarathi
(caching + routing) vs a "route everything to the biggest model to be
safe" baseline, and reports Rs per 1,000 requests with cache savings and
routing savings broken out separately.

Traffic: synthetic but realistic -- sampled from the same 500-prompt
labeled dataset used for routing validation, with a "hot" subset repeated
often (FAQ-like support traffic) and a long tail asked once. This is NOT
real SupportMind 2.0 traffic; that requires pointing SupportMind at the
deployed gateway via base_url (see README). Every number this script
writes says so explicitly.

    python -m benchmarks.cost_report
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from pathlib import Path

from gateway.cache.manager import CacheManager
from gateway.config import get_settings
from gateway.metering.pricing import cost_inr
from gateway.providers.failover import chat_with_failover
from gateway.providers.registry import build_registry
from gateway.router.policy import RoutingPolicy, decide
from gateway.schemas import ChatCompletionRequest
from gateway.storage.sqlite_store import SQLiteStorage

DATASET_PATH = Path(__file__).parent / "replay" / "routing_dataset.jsonl"
RESULTS_DIR = Path(__file__).parent.parent / "results" / "cost"
# Overridable via env so a LIVE run against a free-tier provider can use a
# smaller sample than the full mock-mode traffic replay (rate limits).
TRAFFIC_SIZE = int(os.environ.get("SARATHI_BENCH_TRAFFIC_SIZE", 1000))
HOT_SUBSET_SIZE = min(40, TRAFFIC_SIZE)
HOT_TRAFFIC_FRACTION = 0.45
RANDOM_SEED = 1234


def load_dataset() -> list[dict]:
    with open(DATASET_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def build_traffic(dataset: list[dict]) -> list[dict]:
    rng = random.Random(RANDOM_SEED)
    hot_subset = dataset[:HOT_SUBSET_SIZE]
    traffic = []
    for _ in range(TRAFFIC_SIZE):
        if rng.random() < HOT_TRAFFIC_FRACTION:
            traffic.append(rng.choice(hot_subset))
        else:
            traffic.append(rng.choice(dataset))
    return traffic


async def main() -> None:
    settings = get_settings()
    registry = build_registry(settings)
    routing_policy = RoutingPolicy.load(settings.routing_policy_path)
    storage = SQLiteStorage(":memory:")
    await storage.init()
    cache = CacheManager(storage, settings)
    namespace = "cost-report-eval"

    dataset = load_dataset()
    traffic = build_traffic(dataset)

    direct_cost_total = 0.0
    sarathi_cost_total = 0.0
    cache_hits = 0
    cache_avoided_cost = 0.0
    routing_avoided_cost = 0.0
    tier_counts: dict[str, int] = {}
    direct_provider_counts: dict[str, int] = {}
    candidate_provider_counts: dict[str, int] = {}

    pacing_s = float(os.environ.get("SARATHI_BENCH_PACING_S", 0))

    for row in traffic:
        request = ChatCompletionRequest(
            messages=[{"role": "user", "content": row["prompt"]}], temperature=0
        )

        # Baseline: always the large tier, no cache, no routing.
        direct_resp, direct_outcome = await chat_with_failover(registry, "large", request)
        if pacing_s:
            await asyncio.sleep(pacing_s)
        direct_provider_counts[direct_outcome.provider_used] = (
            direct_provider_counts.get(direct_outcome.provider_used, 0) + 1
        )
        direct_request_cost = cost_inr(
            direct_resp.model, direct_resp.usage.prompt_tokens, direct_resp.usage.completion_tokens
        )
        direct_cost_total += direct_request_cost

        # Treatment: Sarathi's cache + router.
        hit = await cache.lookup(request, namespace)
        if hit is not None:
            cache_hits += 1
            cache_avoided_cost += direct_request_cost
            tier_counts["cache"] = tier_counts.get("cache", 0) + 1
            continue

        decision = decide(request, routing_policy)
        tier_counts[decision.tier] = tier_counts.get(decision.tier, 0) + 1
        candidate_resp, candidate_outcome = await chat_with_failover(
            registry, decision.chain, request
        )
        if pacing_s:
            await asyncio.sleep(pacing_s)
        candidate_provider_counts[candidate_outcome.provider_used] = (
            candidate_provider_counts.get(candidate_outcome.provider_used, 0) + 1
        )
        candidate_cost = cost_inr(
            candidate_resp.model,
            candidate_resp.usage.prompt_tokens,
            candidate_resp.usage.completion_tokens,
        )
        sarathi_cost_total += candidate_cost
        routing_avoided_cost += max(0.0, direct_request_cost - candidate_cost)
        await cache.store(request, namespace, candidate_resp)

    await registry.aclose()
    await storage.close()

    n = len(traffic)
    provider_mode = "mock" if not (settings.groq_api_key or settings.gemini_api_key) else "live"
    summary = {
        "provider": provider_mode,
        "caveat": (
            "provider=mock: token counts and pricing tiers are real (see "
            "gateway/metering/pricing.py), but responses are mock-generated, "
            "not real model output. Traffic is synthetic, sampled from the "
            "500-prompt routing dataset, not real SupportMind 2.0 logs."
            if provider_mode == "mock"
            else "provider=live: traffic is still synthetic (not real SupportMind 2.0 "
            "logs). Some requests may have failed over to the mock provider if a "
            "free-tier rate limit was hit mid-run -- see "
            "direct_baseline_provider_mix/candidate_provider_mix for the real split."
        ),
        "methodology": (
            f"{n} synthetic support-style requests ({HOT_TRAFFIC_FRACTION:.0%} drawn "
            f"from a {HOT_SUBSET_SIZE}-prompt 'hot' FAQ-like subset, the rest from the "
            "full 500-prompt labeled dataset), replayed once against an always-large "
            "direct baseline and once through Sarathi's cache + cost-first router."
        ),
        "traffic_size": n,
        "direct_cost_inr_per_1k": direct_cost_total / n * 1000,
        "sarathi_cost_inr_per_1k": (sarathi_cost_total) / n * 1000,
        "cache_hit_rate": cache_hits / n,
        "cache_savings_inr_per_1k": cache_avoided_cost / n * 1000,
        "routing_savings_inr_per_1k": routing_avoided_cost / n * 1000,
        "total_savings_inr_per_1k": (cache_avoided_cost + routing_avoided_cost) / n * 1000,
        "savings_pct": (
            (direct_cost_total - sarathi_cost_total) / direct_cost_total * 100
            if direct_cost_total
            else None
        ),
        "tier_distribution": tier_counts,
        "direct_baseline_provider_mix": direct_provider_counts,
        "candidate_provider_mix": candidate_provider_counts,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"cost_report_{provider_mode}.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(
        f"provider={provider_mode} direct=Rs{summary['direct_cost_inr_per_1k']:.2f}/1k "
        f"sarathi=Rs{summary['sarathi_cost_inr_per_1k']:.2f}/1k "
        f"savings={summary['savings_pct']:.1f}% -> {out_path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
