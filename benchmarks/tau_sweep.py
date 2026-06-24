"""The tau-sweep experiment: hit rate vs false-hit rate as the semantic
cache's similarity threshold moves, plus an LLM-judged false-hit
measurement at the configured operating threshold.

Two parts:
1. Structural sweep (no API calls): embed every near-duplicate and
   confusable query once, compare each to its cluster's canonical
   embedding, then sweep tau in pure Python. Produces
   results/cache/tau_sweep.png + .json.
2. LLM-judged false-hit check at the ONE operating tau actually used in
   production (gateway.config.Settings.cache_similarity_threshold): run
   the real CacheManager, and for every semantic hit, judge the cached
   answer against a freshly generated answer for the new query
   (canary/judge.py). A "loss" verdict is a genuine false hit -- the
   cache returned a wrong answer. This is the honesty metric CLAUDE.md
   requires published next to the raw hit rate.

    python -m benchmarks.tau_sweep
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from canary.judge import judge
from gateway.cache.embeddings import embeddings_degraded, get_embedding_provider
from gateway.cache.manager import CacheManager
from gateway.config import get_settings
from gateway.providers.failover import chat_with_failover
from gateway.providers.registry import build_registry
from gateway.schemas import ChatCompletionRequest
from gateway.storage.sqlite_store import SQLiteStorage

DATASET_PATH = Path(__file__).parent / "cache" / "cache_eval_dataset.jsonl"
RESULTS_DIR = Path(__file__).parent.parent / "results" / "cache"
TAU_SWEEP = [round(x, 2) for x in np.arange(0.60, 0.99, 0.02)]


def load_clusters() -> list[dict]:
    with open(DATASET_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def cosine(a, b) -> float:
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / ((np.linalg.norm(va) * np.linalg.norm(vb)) + 1e-8))


def structural_sweep(clusters: list[dict]) -> dict:
    embedder = get_embedding_provider()
    near_sims: list[float] = []
    confusable_sims: list[float] = []
    entity_confusable_sims: list[float] = []

    for cluster in clusters:
        canon_emb = embedder.embed(cluster["canonical"])
        for q in cluster["near_duplicates"]:
            near_sims.append(cosine(canon_emb, embedder.embed(q)))
        for q in cluster["confusable"]:
            confusable_sims.append(cosine(canon_emb, embedder.embed(q)))
        for q in cluster.get("entity_confusable", []):
            entity_confusable_sims.append(cosine(canon_emb, embedder.embed(q)))

    sweep = []
    for tau in TAU_SWEEP:
        hit_rate = sum(1 for s in near_sims if s >= tau) / len(near_sims)
        false_hit_rate = sum(1 for s in confusable_sims if s >= tau) / len(confusable_sims)
        entity_false_hit_rate = sum(1 for s in entity_confusable_sims if s >= tau) / len(
            entity_confusable_sims
        )
        sweep.append(
            {
                "tau": tau,
                "hit_rate": hit_rate,
                "false_hit_rate": false_hit_rate,
                "entity_false_hit_rate": entity_false_hit_rate,
            }
        )

    return {
        "embedding_degraded_fallback": embeddings_degraded(),
        "near_duplicate_count": len(near_sims),
        "confusable_count": len(confusable_sims),
        "entity_confusable_count": len(entity_confusable_sims),
        "sweep": sweep,
    }


def plot_sweep(sweep: list[dict], operating_tau: float, out_path: Path) -> None:
    taus = [p["tau"] for p in sweep]
    hit_rates = [p["hit_rate"] for p in sweep]
    false_hit_rates = [p["false_hit_rate"] for p in sweep]
    entity_false_hit_rates = [p["entity_false_hit_rate"] for p in sweep]

    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    ax.plot(taus, hit_rates, marker="o", label="hit rate (true near-duplicates)", color="#4f8cff")
    ax.plot(
        taus,
        false_hit_rates,
        marker="o",
        label="false-hit rate (different intent, same entity)",
        color="#f87171",
    )
    ax.plot(
        taus,
        entity_false_hit_rates,
        marker="o",
        label="false-hit rate (same template, different entity)",
        color="#fb923c",
    )
    ax.axvline(
        operating_tau, color="#999", linestyle="--", label=f"operating tau = {operating_tau}"
    )
    ax.set_xlabel("similarity threshold (tau)")
    ax.set_ylabel("rate")
    ax.set_title("Semantic cache: hit rate vs false-hit rate by tau")
    ax.legend(loc="center left", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


async def llm_judged_false_hit_check(
    operating_tau: float, clusters: list[dict], sample_size: int = 20
) -> dict:
    settings = get_settings()
    settings.cache_similarity_threshold = operating_tau
    registry = build_registry(settings)
    storage = SQLiteStorage(":memory:")
    await storage.init()
    cache = CacheManager(storage, settings)
    namespace = "tau-sweep-eval"

    checked = 0
    false_hits = 0
    details = []
    for cluster in clusters[:sample_size]:
        canonical_req = ChatCompletionRequest(
            messages=[{"role": "user", "content": cluster["canonical"]}], temperature=0
        )
        canonical_resp, _ = await chat_with_failover(registry, "small", canonical_req)
        await cache.store(canonical_req, namespace, canonical_resp)

        for query in cluster["near_duplicates"] + cluster["confusable"]:
            req = ChatCompletionRequest(
                messages=[{"role": "user", "content": query}], temperature=0
            )
            hit = await cache.lookup(req, namespace)
            if hit is None:
                continue
            checked += 1
            fresh_resp, _ = await chat_with_failover(registry, "small", req)
            verdict = await judge(
                registry,
                query,
                fresh_resp.choices[0].message.content,
                hit.response.choices[0].message.content,
            )
            is_false_hit = verdict.verdict == "loss"
            false_hits += int(is_false_hit)
            details.append(
                {
                    "query": query,
                    "cache_status": hit.status,
                    "similarity": hit.similarity,
                    "verdict": verdict.verdict,
                    "judge_mode": verdict.mode,
                    "false_hit": is_false_hit,
                }
            )

    await registry.aclose()
    await storage.close()
    return {
        "operating_tau": operating_tau,
        "hits_checked": checked,
        "false_hits": false_hits,
        "false_hit_rate": (false_hits / checked) if checked else None,
        "details": details,
    }


async def main() -> None:
    settings = get_settings()
    clusters = load_clusters()

    structural = structural_sweep(clusters)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    plot_sweep(
        structural["sweep"], settings.cache_similarity_threshold, RESULTS_DIR / "tau_sweep.png"
    )

    llm_check = await llm_judged_false_hit_check(settings.cache_similarity_threshold, clusters)

    provider_mode = "mock" if not (settings.groq_api_key or settings.gemini_api_key) else "live"
    caveat = None
    if provider_mode == "mock":
        caveat = (
            "provider=mock: fresh and cached answers can be textually "
            "identical when word counts match (the mock provider only "
            "echoes word count), which understates the false-hit rate a "
            "real model would show. The structural (embedding-similarity) "
            "sweep above is provider-independent and is the primary "
            "evidence here; the LLM-judged false-hit numbers are indicative "
            "only until run against LIVE models."
        )

    operating_point = (
        next(
            p
            for p in structural["sweep"]
            if p["tau"] == round(settings.cache_similarity_threshold, 2)
        )
        if any(
            p["tau"] == round(settings.cache_similarity_threshold, 2) for p in structural["sweep"]
        )
        else None
    )

    summary = {
        "provider": provider_mode,
        "caveat": caveat,
        "operating_tau": settings.cache_similarity_threshold,
        "operating_point_structural": operating_point,
        "structural_sweep": structural,
        "llm_judged_false_hit_check": llm_check,
    }
    with open(RESULTS_DIR / "tau_sweep.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(
        f"provider={provider_mode} operating_tau={settings.cache_similarity_threshold} "
        f"structural_hit_rate={operating_point['hit_rate'] if operating_point else 'n/a'} "
        f"structural_false_hit_rate={operating_point['false_hit_rate'] if operating_point else 'n/a'} "
        f"llm_judged_false_hit_rate={llm_check['false_hit_rate']}"
    )
    print(f"chart -> {RESULTS_DIR / 'tau_sweep.png'}")
    print(f"data  -> {RESULTS_DIR / 'tau_sweep.json'}")


if __name__ == "__main__":
    asyncio.run(main())
