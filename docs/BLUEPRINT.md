# Sarathi — Complete Build Kit (from scratch)
### Self-hostable LLM gateway: cheaper, faster, unkillable AI traffic

*Sarathi (सारथी) = charioteer — it steers every request your AI products make.*

---

## 1. Problem statement

Every AI product depends on third-party LLM APIs, and three things hurt every team building on them in 2026:

1. **Cost.** Applications route every request to the biggest model "to be safe," even though a large share of real traffic (short lookups, classification, rephrasing, near-duplicate questions) would be answered identically by a model 10–30x cheaper. Token bills scale linearly with traffic; margins don't.
2. **Reliability.** Providers rate-limit, time out, degrade silently, and go down. An app integrated against one provider inherits that provider's downtime — usually discovered in production, at night.
3. **Blindness.** Nobody can answer "which feature spent what yesterday," "did p95 regress after the provider's silent model update," or "which API key is about to blow its budget," because calls scatter straight from app code to provider SDKs with no layer in between.

**The claim Sarathi makes:** one self-hostable, OpenAI-compatible gateway between applications and every provider that (a) cuts cost through a two-tier semantic cache and complexity-based routing whose quality parity is *verified by offline evals*, (b) survives provider failures through failover chains and circuit breakers with **zero dropped requests under chaos testing**, and (c) meters cost, latency, and quality per key, per model, per day.

This is the "tokenomics" project — the exact skill Indian hiring managers are naming in 2026: cutting inference bills through caching, routing, quantization-aware model choice, and production monitoring. Interview one-liner: *"I built the infrastructure my AI products run through, and I can show you the bill going down."*

---

## 2. Architecture — the request lifecycle

```
  Client apps (anything OpenAI-compatible; SupportMind 2.0 = first customer)
         |  POST /v1/chat/completions  +  API key
         v
 +-----------------------------------------------+
 | 1. Auth & governance                          |
 |    key check . per-key budget . rate limit    |
 +---------------------+---------------------------+
                       v
 +-----------------------------------------------+   hit
 | 2. Semantic cache                             |--------> return cached
 |    exact hash -> embedding sim >= tau          |          (metered, logged)
 +---------------------+---------------------------+
                       | miss
                       v
 +-----------------------------------------------+
 | 3. Router                                     |
 |    complexity features -> model tier          |
 |    modes: cost-first . quality-first . pin    |
 +---------------------+---------------------------+
                       v
 +-----------------------------------------------+
 | 4. Provider layer                             |
 |    adapters: Groq . Gemini . MOCK             |
 |    retries+backoff . timeout budgets          |
 |    circuit breakers . failover chains         |
 |    SSE streaming passthrough                  |
 +---------------------+---------------------------+
                       v
 +-----------------------------------------------+
 | 5. Response path                              |
 |    validate -> cache write -> meter tokens x Rs|
 +---------------------+---------------------------+
                       v
                    client

 Side planes:  /dashboard (embedded HTML page reading the metering DB)
               canary evals (GitHub Actions nightly cron)
               Locust load tests + chaos flags on the mock provider
```

---

## 3. Local-first design — the autonomy unlock

Two runtime profiles, switched by `.env`.

| Concern | LOCAL mode (default — zero credentials) | LIVE mode |
|---|---|---|
| Providers | built-in mock provider (configurable latency, error rates, streaming, chaos flags) | Groq + Gemini free tiers |
| Metering & keys DB | SQLite | Supabase Postgres |
| Semantic cache vectors | SQLite + numpy cosine (bge-small embeddings run locally) | Supabase pgvector |
| Rate limiting | in-memory token bucket | Upstash Redis |
| Deploy | n/a | Render free tier (Docker) |

Every feature — caching, routing, breakers, failover, streaming, metering, dashboard — is fully testable in LOCAL mode. LIVE mode swaps implementations behind the same small adapter interfaces. Keys are needed exactly twice: the one live benchmark session, and the deploy.

---

## 4. Component design

**API surface.** OpenAI-compatible `POST /v1/chat/completions` (+ `/v1/models`, plus a small admin API for keys and policies). Full SSE streaming passthrough, never buffered.

**Auth & governance.** API keys and per-key daily token/Rs budgets in the keys DB; token-bucket rate limiting; budgets hard-stop with a clear error; sampled audit logging.

**Semantic cache (two tiers).** Tier 1: exact-match hash. Tier 2: embedding similarity — bge-small embeddings, cosine >= tau, per-key namespaces, TTL'd. Only requests with temperature <= 0.3 are cacheable; cache writes happen only after response validation. False-hit rate measured by LLM-judging a sample of semantic hits against fresh generations, published next to hit rate. Includes a tau-sweep experiment.

**Router.** Features per request: prompt length, task-type signal, conversation depth, requested max_tokens. Tiers defined in `policies/routing.yaml` — small (Groq Llama-8B), mid (Gemini Flash), large (Groq Llama-70B). Three modes: cost-first, quality-first, manual pin. No policy ships without offline validation — a labeled routing dataset judge-scored for parity, results in `results/routing/` before the policy is enabled.

**Provider layer.** One adapter per provider normalizing requests, responses, error taxonomies, pricing tables. Retries with exponential backoff + jitter; per-provider timeout budgets; circuit breakers on error-rate windows with half-open probing; failover chains from `policies/failover.yaml`. Streaming failure policy: if a stream dies before N tokens were emitted, silently restart on the fallback provider; after N tokens, surface a graceful mid-stream error. The mock provider supports chaos flags: blackhole, inject-500s, inject-latency, die-mid-stream.

**Metering & dashboard.** Every request — hit, miss, failover, or error — writes exactly one record: model, route decision, tokens, computed cost, latency, cache status, failover events. `/dashboard` is an embedded HTML route in the same service showing cost per key/model/day, latency percentiles, cache hit rates, breaker states, failover history.

**Canary evals.** A nightly GitHub Actions cron fires a probe set at each LIVE provider, scores against references + LLM-judge, and auto-opens a GitHub issue on drift. Runs only when repo secrets exist; skips cleanly otherwise.

**Chaos & load harness.** Locust scenarios for p50/p95/p99 at concurrency 10/50/100 plus measured gateway overhead vs a direct provider call. Kill a provider mid-load-test — the dashboard shows breakers opening and traffic failing over, with zero client-facing errors.

---

## 5. The five benchmark tables (the project's evidence)

1. **Cost:** replayed traffic — direct-to-large-model vs through Sarathi -> Rs per 1k requests, cache savings and routing savings reported separately.
2. **Quality parity:** LLM-judge win/tie/loss on routed-down responses (target >=90% parity on eligible traffic).
3. **Cache:** hit rate, false-hit rate, p50 latency cached vs uncached, plus the tau-sweep chart.
4. **Reliability:** chaos run — availability %, dropped requests (target: 0), failover latency penalty.
5. **Load:** latency percentiles under concurrency + gateway overhead in ms vs direct calls.

Every artifact labels its provider — high-concurrency runs served by the mock are marked `provider=mock`, always.

---

## 6. The Rs0 stack

| Piece | Tool | Cost |
|---|---|---|
| Gateway | FastAPI + httpx (fully async) | free |
| LOCAL storage | SQLite (+ numpy cosine for vectors) | free |
| LIVE keys/usage/vectors | Supabase Postgres + pgvector | free tier |
| LIVE rate limits | Upstash Redis | free tier |
| Providers | Groq + Gemini free tiers + built-in mock | free |
| Deploy | Render free tier (Docker; cold starts noted honestly) | free |
| Dashboard | embedded HTML route in the same service | free |
| CI + nightly canary | GitHub Actions | free |
| Load/chaos tests | Locust | free |

---

## 7. Design decisions

1. **Why OpenAI-compatible?** Adoption is a base_url swap.
2. **Why measure cache false-hits?** A cache that returns wrong answers is worse than no cache.
3. **Why offline router validation?** Never ship a policy you haven't scored; the routing dataset doubles as the regression suite.
4. **Why circuit breakers, not just retries?** Retrying a dead provider burns the latency budget; failing fast + half-open probes recover automatically.
5. **Why chaos testing?** Reliability claims without failure injection are vibes.
6. **Why not just use LiteLLM/Portkey?** Because the benchmarked internals — tau-sweeps, parity scoring, breaker behavior under chaos — are precisely the engineering those tools abstract away.

---

## 8. Repo structure

```
sarathi/
|-- gateway/
|   |-- api/            # openai-compatible routes, sse streaming, /dashboard
|   |-- auth/           # keys, budgets, rate limits (local + live impls)
|   |-- cache/          # exact + semantic tiers, tau config
|   |-- router/         # features, classifier, policy engine
|   |-- providers/      # adapters (groq, gemini, mock), breakers, failover, errors.py
|   |-- metering/       # usage records, pricing tables
|   `-- storage/        # sqlite / supabase adapters behind one interface
|-- policies/           # routing.yaml, failover.yaml
|-- canary/             # probe_set/, judge.py
|-- benchmarks/         # locust/, chaos/, replay/, cost_report.py, tau_sweep.py
|-- results/            # every number lives here, generated by scripts
|-- docs/               # BLUEPRINT.md, PLAN.md, HUMAN_TASKS.md, notes/
|-- tests/
|-- Dockerfile
`-- .github/workflows/  # ci.yml, canary.yml (nightly, skips without secrets)
```

---

## 9. Definition of done

- [ ] Live Render URL + public /dashboard
- [ ] Five benchmark tables with real numbers, stated methodology, provider labels
- [ ] Chaos demo video: provider killed mid-load, zero dropped requests
- [ ] SupportMind 2.0 traffic visible on the dashboard (screenshot in README)
- [ ] CI green; nightly canary configured; routing policy backed by a parity results file
- [ ] README: lifecycle diagram, Design Decisions, why-not-LiteLLM, honest Limitations

**Anti-goals:** no savings % not measured on replayed traffic; no caching above temperature 0.3; no policy without parity evidence; no unlabeled mock numbers; no second service for the dashboard; "production-grade" appears nowhere unless CI + tests back it.

## Risks

Free-tier rate limits during load tests -> low concurrency against LIVE, high concurrency against the mock (labeled). SSE proxying edge cases -> dedicated streaming integration tests. Render free-tier cold starts -> note honestly in README. Scope creep -> hedged requests, multi-region, UI polish live in `FUTURE.md`.
