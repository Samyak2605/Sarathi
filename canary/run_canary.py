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
from gateway.providers.registry import build_registry
from gateway.schemas import ChatCompletionRequest

PROBES_PATH = Path(__file__).parent / "probe_set" / "probes.jsonl"
RESULTS_DIR = Path(__file__).parent.parent / "results" / "canary"
DRIFT_THRESHOLD = 0.85
PROBE_TIMEOUT_S = 20.0


def load_probes() -> list[dict]:
    with open(PROBES_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


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
            verdict = await judge(registry, probe["prompt"], probe["reference_answer"], candidate)
            outcomes.append(
                {
                    "id": probe["id"],
                    "candidate": candidate,
                    "verdict": verdict.verdict,
                    "judge_mode": verdict.mode,
                }
            )
        except Exception as e:
            outcomes.append({"id": probe["id"], "error": str(e), "verdict": "loss"})

    passed = sum(1 for o in outcomes if o["verdict"] in ("win", "tie"))
    pass_rate = passed / len(outcomes)
    return {
        "provider": provider_name,
        "model": model,
        "pass_rate": pass_rate,
        "drift_detected": pass_rate < DRIFT_THRESHOLD,
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

    reports = await asyncio.gather(
        *(canary_model(registry, provider, model, probes) for provider, model in targets)
    )
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
        print(
            f"{r['provider']}/{r['model']}: pass_rate={r['pass_rate']:.2%} drift={r['drift_detected']}"
        )

    if drifted:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
