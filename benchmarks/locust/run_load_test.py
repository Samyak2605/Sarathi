"""Benchmark table #5 (load): starts the gateway in LOCAL mode, drives it
with Locust at concurrency 10/50/100, and separately measures the mock
provider's raw in-process latency to report gateway overhead (auth +
cache lookup + router + metering, minus the provider call itself).

Locust runs as a real subprocess (not imported in-process) to avoid its
gevent monkey-patching interfering with this script's own asyncio loop.

    python -m benchmarks.locust.run_load_test
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).parent.parent.parent
RESULTS_DIR = REPO_ROOT / "results" / "load"
LOCUSTFILE = Path(__file__).parent / "gateway.py"
PORT = 8901
DURATION_S = "12s"
CONCURRENCIES = [10, 50, 100]


def wait_for_healthy(base_url: str, timeout_s: float = 20) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError("gateway did not become healthy in time")


def mint_benchmark_key(base_url: str, admin_token: str) -> str:
    resp = httpx.post(
        f"{base_url}/admin/keys",
        headers={"x-admin-token": admin_token},
        json={
            "name": "benchmark",
            "daily_token_budget": 10_000_000,
            "daily_cost_budget_inr": 100_000,
            "rate_limit_per_minute": 1_000_000,
        },
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()["key"]


def run_locust(users: int, base_url: str, csv_prefix: Path, bench_key: str) -> dict:
    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(LOCUSTFILE),
        "--headless",
        "-u",
        str(users),
        "-r",
        str(users),
        "-t",
        DURATION_S,
        "--host",
        base_url,
        "--csv",
        str(csv_prefix),
        "--only-summary",
    ]
    # Locust exits non-zero if any request failed (e.g. a deliberately
    # induced 429/502 during a chaos run) -- that's information we record
    # in the CSV, not a reason to crash this script.
    subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, "SARATHI_BENCH_KEY": bench_key},
    )

    stats_path = Path(f"{csv_prefix}_stats.csv")
    with open(stats_path) as f:
        rows = list(csv.DictReader(f))
    row = next(r for r in rows if r["Name"] == "/v1/chat/completions")
    return {
        "users": users,
        "requests": int(row["Request Count"]),
        "failures": int(row["Failure Count"]),
        "rps": float(row["Requests/s"]),
        "p50_ms": float(row["50%"]),
        "p95_ms": float(row["95%"]),
        "p99_ms": float(row["99%"]),
    }


async def measure_direct_provider_latency(concurrency: int, n_requests: int = 200) -> dict:
    from gateway.providers.mock import MockProvider
    from gateway.schemas import ChatCompletionRequest

    provider = MockProvider()
    request = ChatCompletionRequest(
        messages=[{"role": "user", "content": "What is the capital of France?"}], temperature=0.7
    )
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []

    async def one_call():
        async with sem:
            start = time.perf_counter()
            await provider.chat(request, "mock-small", timeout_s=5)
            latencies.append((time.perf_counter() - start) * 1000)

    await asyncio.gather(*(one_call() for _ in range(n_requests)))
    latencies.sort()

    def pct(p):
        idx = min(len(latencies) - 1, int(len(latencies) * p))
        return latencies[idx]

    return {"p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99)}


async def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    base_url = f"http://127.0.0.1:{PORT}"

    # Explicitly blank Groq/Gemini -- this benchmark measures GATEWAY
    # overhead against the mock provider's simulated latency, not a real
    # provider's network latency/rate limits. pydantic-settings reads .env
    # directly regardless of this subprocess's inherited os.environ, so a
    # real key on disk must be overridden explicitly, not just omitted.
    env = {**os.environ, "GROQ_API_KEY": "", "GEMINI_API_KEY": ""}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "gateway.api.main:app", "--port", str(PORT)],
        cwd=REPO_ROOT,
        env=env,
    )
    try:
        wait_for_healthy(base_url)
        bench_key = mint_benchmark_key(base_url, admin_token="change-me")

        results = []
        for c in CONCURRENCIES:
            gateway_stats = run_locust(c, base_url, RESULTS_DIR / f"tmp_{c}", bench_key)
            direct_stats = await measure_direct_provider_latency(c)
            overhead_ms = gateway_stats["p50_ms"] - direct_stats["p50_ms"]
            results.append(
                {
                    "concurrency": c,
                    "gateway": gateway_stats,
                    "direct_provider_call": direct_stats,
                    "gateway_overhead_p50_ms": overhead_ms,
                }
            )
            print(
                f"concurrency={c}: gateway p50={gateway_stats['p50_ms']}ms "
                f"p95={gateway_stats['p95_ms']}ms p99={gateway_stats['p99_ms']}ms "
                f"rps={gateway_stats['rps']} failures={gateway_stats['failures']} "
                f"direct_p50={direct_stats['p50_ms']:.2f}ms overhead~{overhead_ms:.2f}ms"
            )
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        for c in CONCURRENCIES:
            for suffix in ("_stats.csv", "_stats_history.csv", "_failures.csv", "_exceptions.csv"):
                p = RESULTS_DIR / f"tmp_{c}{suffix}"
                if p.exists():
                    p.unlink()

    summary = {
        "provider": "mock",
        "caveat": (
            "provider=mock: this measures gateway/routing/cache/metering "
            "overhead honestly, but request latency is the mock provider's "
            "simulated latency, not a real model's. See docs/HUMAN_TASKS.md "
            "for the LIVE benchmark session."
        ),
        "methodology": (
            f"Locust headless, {DURATION_S} per concurrency level, hitting "
            "/v1/chat/completions with temperature=0.7 (non-cacheable) prompts "
            "so this measures the real per-request compute path, not cache hits."
        ),
        "results": results,
    }
    with open(RESULTS_DIR / "load_test.json", "w") as f:
        json.dump(summary, f, indent=2)
    plot_results(results, RESULTS_DIR / "load_test.png")
    print(f"-> {RESULTS_DIR / 'load_test.json'}")


def plot_results(results: list[dict], out_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    concurrencies = [r["concurrency"] for r in results]
    p50 = [r["gateway"]["p50_ms"] for r in results]
    p95 = [r["gateway"]["p95_ms"] for r in results]
    p99 = [r["gateway"]["p99_ms"] for r in results]
    direct_p50 = [r["direct_provider_call"]["p50_ms"] for r in results]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(concurrencies, p50, marker="o", label="gateway p50", color="#4f8cff")
    ax.plot(concurrencies, p95, marker="o", label="gateway p95", color="#facc15")
    ax.plot(concurrencies, p99, marker="o", label="gateway p99", color="#f87171")
    ax.plot(
        concurrencies,
        direct_p50,
        marker="o",
        linestyle="--",
        label="direct provider call p50 (no gateway)",
        color="#999",
    )
    ax.set_xlabel("concurrency (users)")
    ax.set_ylabel("latency (ms)")
    ax.set_title("Sarathi load test: latency percentiles vs concurrency (provider=mock)")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    asyncio.run(main())
