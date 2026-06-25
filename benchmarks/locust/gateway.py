"""Locust load test for Sarathi's /v1/chat/completions endpoint.

Interactive UI:
    locust -f benchmarks/locust/gateway.py --host http://127.0.0.1:8000

Headless (used by benchmarks/locust/run_load_and_chaos.py):
    locust -f benchmarks/locust/gateway.py --headless -u 50 -r 50 -t 15s \
        --host http://127.0.0.1:8000 --csv results/load/tmp

Deliberately uses temperature=0.7 (non-cacheable, per CLAUDE.md rule 2:
never cache above 0.3) so this measures the real per-request compute
path -- router + provider round trip -- rather than an artificially
inflated number from cache hits.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

API_KEY = os.environ.get("SARATHI_BENCH_KEY", "sk-local-dev")

PROMPTS = [
    "What is the capital of France?",
    "Summarize the plot of a mystery novel in two sentences.",
    "Write a short function that reverses a string in Python.",
    "Explain step by step why distributed consensus is hard.",
    "Classify this review as positive or negative: 'Loved it, will buy again.'",
    "Rephrase this more formally: hey can u send that file over",
    "What is 17 times 23?",
    "Compare SQL databases versus NoSQL document stores for a high-traffic app.",
]


class GatewayUser(HttpUser):
    wait_time = between(0.05, 0.3)

    @task
    def chat_completion(self):
        prompt = random.choice(PROMPTS)
        self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
            name="/v1/chat/completions",
        )
