"""Benchmark table #4 (reliability): the signature demo. Starts the
gateway with SARATHI_DEMO_MODE=1 (registers a mock stand-in under the
"groq" slot so the small-tier chain has two providers -- see
gateway/config.py -- with ZERO real credentials), drives continuous
concurrent traffic, kills the primary provider mid-load via
POST /admin/chaos, and verifies every single request still succeeds
(failed over to the healthy backup) -- then revives it and watches the
circuit breaker recover from OPEN through HALF_OPEN back to CLOSED.

This is exactly what docs/HUMAN_TASKS.md's manual chaos-video recipe
does by hand, against a real running server with the dashboard open in
a browser -- this script is the automated, evidence-producing version.

    python -m benchmarks.chaos.run_chaos_test
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).parent.parent.parent
RESULTS_DIR = REPO_ROOT / "results" / "chaos"
PORT = 8902
CONCURRENCY = 20
WARMUP_S = 3
KILL_DURATION_S = 10
RECOVERY_WAIT_S = 18
ADMIN_TOKEN = "change-me"


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


async def worker(client: httpx.AsyncClient, bench_key: str, events: list, stop_at: float) -> None:
    while time.time() < stop_at:
        start = time.perf_counter()
        try:
            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {bench_key}"},
                json={
                    "messages": [{"role": "user", "content": "short prompt for chaos test"}],
                    "temperature": 0.7,
                },
                timeout=10.0,
            )
            latency_ms = (time.perf_counter() - start) * 1000
            ok = resp.status_code == 200
            failover_chain = resp.json().get("sarathi", {}).get("failover_chain", []) if ok else []
            events.append(
                {
                    "ts": time.time(),
                    "ok": ok,
                    "status": resp.status_code,
                    "latency_ms": latency_ms,
                    "failover_chain": failover_chain,
                }
            )
        except httpx.HTTPError as e:
            events.append(
                {
                    "ts": time.time(),
                    "ok": False,
                    "status": None,
                    "latency_ms": (time.perf_counter() - start) * 1000,
                    "error": str(e),
                }
            )
        await asyncio.sleep(0.05)


async def sample_breakers(client: httpx.AsyncClient, samples: list, stop_at: float) -> None:
    while time.time() < stop_at:
        try:
            resp = await client.get("/admin/breakers", headers={"x-admin-token": ADMIN_TOKEN})
            samples.append({"ts": time.time(), "breakers": resp.json()})
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1.0)


async def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    base_url = f"http://127.0.0.1:{PORT}"

    env = {
        "SARATHI_MODE": "local",
        "SARATHI_DEMO_MODE": "true",
        "SARATHI_ADMIN_TOKEN": ADMIN_TOKEN,
        "SQLITE_PATH": str(REPO_ROOT / "data" / "chaos_test.db"),
        "PATH": os.environ.get("PATH", ""),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "gateway.api.main:app", "--port", str(PORT)],
        cwd=REPO_ROOT,
        env=env,
    )
    events: list = []
    breaker_samples: list = []
    try:
        wait_for_healthy(base_url)

        async with httpx.AsyncClient(base_url=base_url) as client:
            key_resp = await client.post(
                "/admin/keys",
                headers={"x-admin-token": ADMIN_TOKEN},
                json={
                    "name": "chaos-bench",
                    "daily_token_budget": 10_000_000,
                    "daily_cost_budget_inr": 100_000,
                    "rate_limit_per_minute": 1_000_000,
                },
            )
            bench_key = key_resp.json()["key"]

            total_duration = WARMUP_S + KILL_DURATION_S + RECOVERY_WAIT_S
            stop_at = time.time() + total_duration

            workers = [
                asyncio.create_task(worker(client, bench_key, events, stop_at))
                for _ in range(CONCURRENCY)
            ]
            sampler = asyncio.create_task(sample_breakers(client, breaker_samples, stop_at))

            print(f"warming up for {WARMUP_S}s ...")
            await asyncio.sleep(WARMUP_S)

            print("killing primary provider (groq demo stand-in) via /admin/chaos ...")
            kill_ts = time.time()
            await client.post(
                "/admin/chaos",
                headers={"x-admin-token": ADMIN_TOKEN},
                json={"provider": "groq", "inject_500": True},
            )

            await asyncio.sleep(KILL_DURATION_S)

            print("reviving provider ...")
            revive_ts = time.time()
            await client.post(
                "/admin/chaos",
                headers={"x-admin-token": ADMIN_TOKEN},
                json={"provider": "groq"},
            )

            print(f"observing recovery for {RECOVERY_WAIT_S}s ...")
            await asyncio.gather(*workers, sampler)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    total = len(events)
    failed = [e for e in events if not e["ok"]]
    failed_over = [e for e in events if e.get("failover_chain") and len(e["failover_chain"]) > 1]
    during_outage = [e for e in events if kill_ts <= e["ts"] < revive_ts]
    failed_during_outage = [e for e in during_outage if not e["ok"]]

    summary = {
        "provider": "mock",
        "caveat": (
            "provider=mock: SARATHI_DEMO_MODE registers a second mock-backed "
            "adapter under the 'groq' slot (no real Groq credentials involved) "
            "so a real two-provider failover chain is exercisable with zero "
            "credentials. The failover/breaker/retry machinery being tested is "
            "the real production code path."
        ),
        "methodology": (
            f"{CONCURRENCY} concurrent workers hit /v1/chat/completions continuously "
            f"for {total_duration}s. After {WARMUP_S}s warmup, the primary provider "
            f"was killed via POST /admin/chaos for {KILL_DURATION_S}s, then revived, "
            f"with {RECOVERY_WAIT_S}s observed afterward for breaker recovery."
        ),
        "total_requests": total,
        "failed_requests": len(failed),
        "availability_pct": (total - len(failed)) / total * 100 if total else None,
        "requests_during_outage": len(during_outage),
        "failed_during_outage": len(failed_during_outage),
        "zero_dropped_requests_during_outage": len(failed_during_outage) == 0,
        "requests_that_failed_over": len(failed_over),
        "avg_latency_ms": sum(e["latency_ms"] for e in events) / total if total else None,
        "avg_latency_ms_during_outage": (
            sum(e["latency_ms"] for e in during_outage) / len(during_outage)
            if during_outage
            else None
        ),
        "breaker_timeline": [
            {"ts": s["ts"] - kill_ts, "groq_state": s["breakers"].get("groq", {}).get("state")}
            for s in breaker_samples
        ],
        "kill_ts_offset_s": 0,
        "revive_ts_offset_s": revive_ts - kill_ts,
    }
    with open(RESULTS_DIR / "chaos_test.json", "w") as f:
        json.dump(summary, f, indent=2)

    plot_timeline(events, breaker_samples, kill_ts, revive_ts, RESULTS_DIR / "chaos_test.png")

    print(
        f"total={total} failed={len(failed)} availability={summary['availability_pct']:.2f}% "
        f"zero_dropped_during_outage={summary['zero_dropped_requests_during_outage']} "
        f"failed_over={len(failed_over)} -> {RESULTS_DIR / 'chaos_test.json'}"
    )


def plot_timeline(events, breaker_samples, kill_ts, revive_ts, out_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not events:
        return
    t0 = events[0]["ts"]
    xs = [e["ts"] - t0 for e in events]
    ys = [e["latency_ms"] for e in events]
    colors = ["#4ade80" if e["ok"] else "#f87171" for e in events]

    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.scatter(xs, ys, c=colors, s=10, alpha=0.6)
    ax.axvline(kill_ts - t0, color="#f87171", linestyle="--", label="provider killed")
    ax.axvline(revive_ts - t0, color="#4ade80", linestyle="--", label="provider revived")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("request latency (ms)")
    ax.set_title("Chaos test: request latency over time (green=ok, red=failed)")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    asyncio.run(main())
