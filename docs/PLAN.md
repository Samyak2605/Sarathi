# Sarathi build plan

Built in one continuous pass (no week-by-week split). Checked only with
evidence (tests passing / results file generated / manual step done).

## Scaffold
- [x] Repo init, CLAUDE.md, docs/BLUEPRINT.md, FUTURE.md, docs/HUMAN_TASKS.md
- [x] Folder structure per BLUEPRINT.md section 8

## Core proxy + reliability (LOCAL mode)
- [x] Config + Pydantic schemas + storage interface (SQLite impl, Supabase impl)
- [x] Provider adapters: mock (with chaos flags), Groq, Gemini; errors.py taxonomy
- [x] Retries+backoff, per-provider timeout budgets, circuit breakers, failover chains
- [x] OpenAI-compatible /v1/chat/completions (streaming + non-streaming), /v1/models
- [x] API keys, per-key budgets, in-memory (+ Upstash) rate limiting
- [x] Metering: one record per request (hit/miss/failover/error)
- [x] Tests: streaming, non-streaming, chaos/failure injection, failover, breaker states (37 passing)

## Cache + router
- [x] Exact-match cache tier
- [x] Semantic cache tier (bge-small embeddings + numpy cosine), per-key namespace, TTL
- [x] tau-sweep experiment + false-hit measurement harness -> results/cache/tau_sweep.json
      (operating tau recalibrated 0.86 -> 0.90 from the real sweep; also
      surfaced and documented an entity-collision limitation, see FUTURE.md)
- [x] Router: features + heuristic classifier, policies/routing.yaml
- [x] 500-prompt routing dataset + offline parity eval -> results/routing/parity_mock.json
      (tier accuracy 89.2%, provider=mock, real LIVE parity numbers still pending
      the LIVE benchmark session -- see docs/HUMAN_TASKS.md)
- [x] Cost-first policy enabled (parity results file exists, mock-labeled)

## Evidence + ship
- [x] Embedded /dashboard route
- [x] Locust load suite (concurrency 10/50/100) against mock, labeled -> results/load/load_test.json
      (gateway overhead ~2-5ms over raw provider latency, zero failures)
- [x] Chaos harness: kill provider mid-load, verify zero dropped requests ->
      results/chaos/chaos_test.json (7295 requests, 0 failed, breaker opened
      in ~1s and recovered correctly -- this run also found and fixed a real
      bug in the breaker's time-window design, see gateway/providers/breaker.py)
- [x] cost_report.py + tau_sweep.py generating results/ artifacts (97.5% cost
      savings on synthetic replayed traffic, provider=mock, labeled)
- [x] Canary probe set + judge.py + nightly GitHub Actions workflow (skips w/o secrets)
- [x] CI workflow (lint + test + docker build)
- [x] Dockerfile (built and smoke-tested locally; pre-fetches the embedding model)
- [x] README: lifecycle diagram, design decisions, why-not-LiteLLM, benchmark tables, limitations

## Requires human action (see docs/HUMAN_TASKS.md)
- [ ] LIVE mode credentials wired (Groq, Gemini, Supabase, Upstash)
- [ ] One supervised LIVE benchmark session -> real cost/quality tables
- [ ] SupportMind 2.0 pointed at Sarathi via base_url; dashboard screenshot
- [ ] Render deploy; public URL + /dashboard live
- [ ] Chaos-kill demo video recorded

## Definition of done
- [ ] Live Render URL + public /dashboard
- [ ] Five benchmark tables with real numbers, stated methodology, provider labels
- [ ] Chaos demo video: provider killed mid-load, zero dropped requests
- [ ] SupportMind 2.0 traffic visible on the dashboard (screenshot in README)
- [ ] CI green; nightly canary configured; routing policy backed by a parity results file
- [ ] README: lifecycle diagram, Design Decisions, why-not-LiteLLM, honest Limitations
