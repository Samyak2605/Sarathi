"""Offline routing parity eval -- the evidence required before
policies/routing.yaml's cost-first mode is allowed to run: never ship a
routing policy you haven't scored.

For every prompt in routing_dataset.jsonl that the router would route
*down* from the large tier, this calls both the router's chosen tier and
the large tier, judges them (canary/judge.py), and reports what fraction
of routed-down answers matched the large model (win or tie). Every result
records whether it ran against real providers or the mock, and whether
judging used the real LLM judge or the heuristic fallback (no Groq/Gemini
keys configured).

    python -m benchmarks.replay.routing_eval
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path

from canary.judge import judge
from gateway.config import get_settings
from gateway.providers.failover import chat_with_failover
from gateway.providers.registry import build_registry
from gateway.router.policy import RoutingPolicy, decide
from gateway.schemas import ChatCompletionRequest

DATASET_PATH = Path(__file__).parent / "routing_dataset.jsonl"
RESULTS_DIR = Path(__file__).parent.parent.parent / "results" / "routing"
# Overridable via env: a LIVE run against a free-tier provider needs much
# lower concurrency and a smaller sample than the full mock-mode sweep.
CONCURRENCY = int(os.environ.get("SARATHI_BENCH_CONCURRENCY", 16))
SAMPLE_SIZE = os.environ.get("SARATHI_BENCH_SAMPLE_SIZE")
PACING_S = float(os.environ.get("SARATHI_BENCH_PACING_S", 0))


def load_dataset() -> list[dict]:
    rows = []
    with open(DATASET_PATH) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


async def evaluate_row(registry, routing_policy, row: dict, sem: asyncio.Semaphore) -> dict:
    request = ChatCompletionRequest(
        messages=[{"role": "user", "content": row["prompt"]}], temperature=0
    )
    decision = decide(request, routing_policy)
    result = {
        "id": row["id"],
        "task_type": row["task_type"],
        "expected_tier": row["expected_tier"],
        "routed_tier": decision.tier,
        "tier_match": decision.tier == row["expected_tier"],
        "eligible": decision.tier != "large",
    }
    if not result["eligible"]:
        return result

    async with sem:
        try:
            candidate_resp, _ = await chat_with_failover(registry, decision.chain, request)
            if PACING_S:
                await asyncio.sleep(PACING_S)
            reference_resp, _ = await chat_with_failover(registry, "large", request)
            if PACING_S:
                await asyncio.sleep(PACING_S)
            verdict = await judge(
                registry,
                row["prompt"],
                reference_resp.choices[0].message.content,
                candidate_resp.choices[0].message.content,
            )
        except Exception as e:
            result["error"] = str(e)
            return result

    result["verdict"] = verdict.verdict
    result["judge_mode"] = verdict.mode
    return result


async def main() -> None:
    settings = get_settings()
    registry = build_registry(settings)
    routing_policy = RoutingPolicy.load(settings.routing_policy_path)
    rows = load_dataset()
    if SAMPLE_SIZE:
        rows = rows[: int(SAMPLE_SIZE)]

    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(
        *(evaluate_row(registry, routing_policy, row, sem) for row in rows)
    )
    await registry.aclose()

    total = len(results)
    tier_correct = sum(1 for r in results if r["tier_match"])
    eligible = [r for r in results if r["eligible"] and "verdict" in r]
    parity_pass = sum(1 for r in eligible if r["verdict"] in ("win", "tie"))
    parity_rate = parity_pass / len(eligible) if eligible else None

    by_category: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "eligible": 0, "parity_pass": 0}
    )
    for r in results:
        cat = by_category[r["task_type"]]
        cat["total"] += 1
        if r["eligible"] and "verdict" in r:
            cat["eligible"] += 1
            if r["verdict"] in ("win", "tie"):
                cat["parity_pass"] += 1

    judge_modes = {r.get("judge_mode") for r in eligible}
    provider_mode = "mock" if not (settings.groq_api_key or settings.gemini_api_key) else "live"

    caveat = None
    if provider_mode == "mock":
        caveat = (
            "provider=mock: the mock provider returns the same canned reply "
            "regardless of which tier/model is asked (it only echoes word "
            "count), so candidate and reference text are byte-identical here "
            "and parity_rate=1.0 is an artifact of that, not evidence of real "
            "quality parity. This run validates the harness end-to-end with "
            "zero credentials; the real parity numbers require a LIVE "
            "benchmark session against actual Groq/Gemini models before "
            "this policy's claim can be trusted."
        )

    summary = {
        "provider": provider_mode,
        "caveat": caveat,
        "methodology": (
            "Router.decide() run over a 500-prompt labeled dataset "
            "(benchmarks/replay/routing_dataset.jsonl). For every prompt routed "
            "below the large tier, both the routed tier and the large tier were "
            "called and judged by canary/judge.py (LLM judge via the large-tier "
            "chain, or a heuristic embedding-similarity fallback where noted)."
        ),
        "dataset_size": total,
        "router_tier_accuracy": tier_correct / total,
        "eligible_routed_down_count": len(eligible),
        "parity_rate": parity_rate,
        "parity_target": 0.90,
        "parity_pass_threshold_met": (parity_rate is not None and parity_rate >= 0.90),
        "judge_modes_used": sorted(m for m in judge_modes if m),
        "by_category": dict(by_category),
        "results": results,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"parity_{provider_mode}.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(
        f"provider={provider_mode} tier_accuracy={summary['router_tier_accuracy']:.2%} "
        f"eligible={len(eligible)} parity_rate={parity_rate} -> {out_path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
