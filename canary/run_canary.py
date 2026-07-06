"""Nightly canary: fires the 30-prompt probe set at every configured LIVE
provider/model, scores answers against known-good reference_answers via
canary/judge.py, and auto-opens a GitHub issue on drift (a provider
silently swapping the model behind a name, or quality regressing).

Skips cleanly with no LIVE credentials configured (LOCAL mode) --
correctness of the skip path is exactly what
tests/test_canary.py::test_skips_without_credentials checks.

    python -m canary.run_canary
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from canary.judge import judge
from gateway.config import get_settings
from gateway.providers.errors import ProviderRateLimitError
from gateway.providers.registry import build_registry
from gateway.schemas import ChatCompletionRequest

PROBES_PATH = Path(__file__).parent / "probe_set" / "probes.jsonl"
RESULTS_DIR = Path(__file__).parent.parent / "results" / "canary"
DRIFT_THRESHOLD = 0.85
PROBE_TIMEOUT_S = 20.0
# Below this many completed (non-rate-limited) probes, a low pass rate is
# free-tier flakiness, not a meaningful drift signal -- don't cry wolf.
MIN_COMPLETED_FOR_DRIFT_SIGNAL = 15


def load_probes() -> list[dict]:
    with open(PROBES_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


PROBE_PACING_S = 2.0  # spacing between probes -- free-tier Groq/Gemini RPM limits


async def canary_model(registry, provider_name: str, model: str, probes: list[dict]) -> dict:
    adapter = registry.adapters[provider_name]
    outcomes = []
    for probe in probes:
        request = ChatCompletionRequest(
            messages=[{"role": "user", "content": probe["prompt"]}], temperature=0, max_tokens=50
        )
        try:
            response = await adapter.chat(request, model, timeout_s=PROBE_TIMEOUT_S)
            candidate = response.choices[0].message.content
            await asyncio.sleep(PROBE_PACING_S)
            verdict = await judge(registry, probe["prompt"], probe["reference_answer"], candidate)
            outcomes.append(
                {
                    "id": probe["id"],
                    "candidate": candidate,
                    "verdict": verdict.verdict,
                    "judge_mode": verdict.mode,
                }
            )
        except ProviderRateLimitError as e:
            # Free-tier rate limiting is infra flakiness, not a quality
            # signal -- exclude it from the pass rate rather than let it
            # masquerade as drift. Recorded separately so it's still visible.
            outcomes.append({"id": probe["id"], "error": str(e), "verdict": "rate_limited"})
        except Exception as e:
            outcomes.append({"id": probe["id"], "error": str(e), "verdict": "loss"})
        await asyncio.sleep(PROBE_PACING_S)

    completed = [o for o in outcomes if o["verdict"] != "rate_limited"]
    rate_limited_count = len(outcomes) - len(completed)
    passed = sum(1 for o in completed if o["verdict"] in ("win", "tie"))
    pass_rate = (passed / len(completed)) if completed else None
    insufficient_data = len(completed) < MIN_COMPLETED_FOR_DRIFT_SIGNAL
    return {
        "provider": provider_name,
        "model": model,
        "probes_total": len(outcomes),
        "rate_limited_count": rate_limited_count,
        "completed_count": len(completed),
        "pass_rate": pass_rate,
        "insufficient_data": insufficient_data,
        "drift_detected": (not insufficient_data)
        and pass_rate is not None
        and pass_rate < DRIFT_THRESHOLD,
        "outcomes": outcomes,
    }


async def maybe_open_github_issue(drifted: list[dict]) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo or not drifted:
        return
    title = f"Canary drift detected: {', '.join(d['provider'] + '/' + d['model'] for d in drifted)}"
    body_lines = ["Nightly canary detected quality drift or a possible silent model swap.\n"]
    for d in drifted:
        body_lines.append(f"- **{d['provider']}/{d['model']}**: pass_rate={d['pass_rate']:.2%}")
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"title": title, "body": "\n".join(body_lines), "labels": ["canary-drift"]},
        )


async def main() -> None:
    settings = get_settings()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if not settings.groq_api_key and not settings.gemini_api_key:
        skip_report = {
            "status": "skipped",
            "reason": "no LIVE provider credentials configured (GROQ_API_KEY/GEMINI_API_KEY unset)",
            "timestamp": time.time(),
        }
        with open(RESULTS_DIR / "latest.json", "w") as f:
            json.dump(skip_report, f, indent=2)
        print("canary: skipped (no LIVE credentials) -- this is expected in LOCAL mode")
        return

    registry = build_registry(settings)
    probes = load_probes()

    targets = []
    if settings.groq_api_key:
        targets += [("groq", m) for m in registry.adapters["groq"].supported_models]
    if settings.gemini_api_key:
        targets += [("gemini", m) for m in registry.adapters["gemini"].supported_models]

    # Sequential, not gathered -- Groq's rate limit is per-account, not
    # per-model, so testing two Groq models concurrently just doubles the
    # request rate against the same shared quota.
    reports = []
    for provider, model in targets:
        reports.append(await canary_model(registry, provider, model, probes))
    await registry.aclose()

    drifted = [r for r in reports if r["drift_detected"]]
    report = {
        "status": "ok",
        "timestamp": time.time(),
        "drift_threshold": DRIFT_THRESHOLD,
        "results": reports,
        "drifted": [
            {"provider": r["provider"], "model": r["model"], "pass_rate": r["pass_rate"]}
            for r in drifted
        ],
    }
    with open(RESULTS_DIR / "latest.json", "w") as f:
        json.dump(report, f, indent=2)

    await maybe_open_github_issue(drifted)

    for r in reports:
        pr = f"{r['pass_rate']:.2%}" if r["pass_rate"] is not None else "n/a"
        print(
            f"{r['provider']}/{r['model']}: pass_rate={pr} "
            f"(completed {r['completed_count']}/{r['probes_total']}, "
            f"{r['rate_limited_count']} rate-limited) drift={r['drift_detected']}"
        )

    if drifted:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
