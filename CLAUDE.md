# CLAUDE.md — Sarathi

## What this is
Self-hostable OpenAI-compatible LLM gateway: semantic caching, verified
routing, circuit breakers/failover, metering, embedded dashboard, canary
evals, chaos testing. Spec: @docs/BLUEPRINT.md. Tasks: docs/PLAN.md.
Manual/human tasks: docs/HUMAN_TASKS.md.

## Environment
- ₹0 budget. LOCAL mode (default): mock provider, SQLite, in-memory rate
  limiting — zero credentials. LIVE mode via .env: Groq, Gemini,
  Supabase (pgvector), Upstash Redis. Deploy: Render free tier (Docker).
- Python 3.11. FastAPI + httpx (async), Pydantic v2, pytest +
  pytest-asyncio, ruff, Locust. Secrets via .env only.

## Commands
- Test: `pytest -q`   Lint: `ruff check . && ruff format --check .`
- Run: `uvicorn gateway.api.main:app --reload`
- Load: `locust -f benchmarks/locust/gateway.py`

## Hard rules
1. Request path is fully async — zero blocking I/O in gateway/.
2. Never cache temperature > 0.3; never cache across key namespaces;
   cache writes only after response validation.
3. Every request (hit/miss/failover/error) writes exactly one metering
   record. No silent paths.
4. No routing policy enabled without results/routing/ parity evidence.
5. SSE proxied, never buffered; mid-stream death → restart on fallback
   if < N tokens emitted, else graceful error — integration-tested.
6. Provider exceptions never leak past adapters (errors.py taxonomy).
7. Reliability code without failure-injection tests is not done.
8. Never fabricate numbers; benchmarks/ scripts write results/; every
   artifact labels its provider (mock runs marked provider=mock).
9. Blocked on a credential/limit in a LIVE path → STOP and tell me;
   add it to docs/HUMAN_TASKS.md. Never fake a LIVE result.

## Working style
Build end-to-end in one pass per user instruction (no artificial week
splits). Minimal diffs. Two reasonable options → present both briefly.
Tick docs/PLAN.md only with evidence. After each nontrivial file append
5 lines to docs/notes/build.md (what, key decision, one hard interview
question).
