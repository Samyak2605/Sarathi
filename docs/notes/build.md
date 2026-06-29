# Build notes

Terse log of what got built, the key decision behind each piece, and one
hard interview question it should survive. Written for interview prep,
not as a changelog.

## Storage layer (gateway/storage/)
What: Storage ABC with SQLite (LOCAL) and Supabase/PostgREST (LIVE)
implementations behind one interface; semantic search implemented per
backend (numpy cosine locally, pgvector `<=>` via an RPC function live).
Key decision: pgvector work happens in Postgres via a SQL function
(docs/supabase_schema.sql), not fetch-all-then-cosine-in-Python, so LIVE
mode actually gets an ANN index instead of a full scan.
Hard question: why not just use SQLAlchemy for both backends? Because
PostgREST's HTTP interface and pgvector's operator don't map cleanly onto
one ORM without losing the ability to push the cosine search into the DB.

## Provider adapters + error taxonomy (gateway/providers/)
What: One adapter per provider (mock, Groq, Gemini), each translating its
own error shapes into a shared taxonomy (errors.py) so nothing upstream
ever branches on provider-specific exceptions.
Key decision: Gemini needed real translation (its message envelope,
system-instruction field, and usageMetadata naming are nothing like
OpenAI's) -- that adapter is the clearest evidence the gateway is doing
real normalization work, not just proxying.
Hard question: what happens if a provider returns a 200 with a malformed
body? Currently a Pydantic validation error propagates as an unhandled
exception, not a ProviderError -- a real gap, not covered by the taxonomy.

## Circuit breaker (gateway/providers/breaker.py)
What: count-based sliding window (last N requests), not time-based.
Key decision: this is a rewrite. The first version used a time window,
and running the actual chaos benchmark (not just unit tests) showed it
never tripped during a 10s outage -- sustained warmup traffic left
hundreds of stale successes in the window, diluting the failure ratio
below threshold. Count-based windows don't have that failure mode.
Hard question: why not exponential backoff on the open_seconds cooldown
itself (instead of a fixed 15s)? Would recover faster from flaky-but-not-
dead providers; not implemented, noted in FUTURE.md territory.

## Streaming mid-death policy (gateway/providers/failover.py)
What: buffer chunks until `stream_fallback_token_threshold` (8) tokens
are seen; discard-and-retry-next-provider before that point, graceful
`mid_stream_error` after (never retry once bytes reached the client).
Key decision: this is the one place where "zero dropped requests" and
"never send a corrupted answer" are in tension, and the buffer is what
resolves it -- silent failover is only possible because nothing has been
flushed yet.
Hard question: what's the UX cost of buffering 8 tokens before the first
byte reaches the client? Real added latency (roughly 8 tokens' worth of
generation time) on every streaming request, paid to get the silent-
failover property. Not free.

## Router (gateway/router/)
What: heuristic complexity classifier (length, code-signal regex,
reasoning-signal regex, message depth, requested max_tokens), separately
weighted rather than max()'d together.
Key decision: initial calibration (max() of code/reasoning signals)
couldn't separate "debugging" (should route large) from "write me a
function" (should route mid) -- both tripped the same signal. Separating
the weights, plus lowering length_words_norm so real length differences
mattered, took router_tier_accuracy from a broken 100%-small result to a
credible 89.2% against the labeled dataset.
Hard question: this is a hand-tuned heuristic against one synthetic
dataset -- how would it hold up against a real traffic distribution?
Unknown; that's why routing_eval.py is a reusable harness, not a one-off
script -- rerunning it against new labeled data is the intended workflow.

## Semantic cache (gateway/cache/)
What: exact-hash tier + embedding-cosine tier (bge-small via fastembed,
hashed fallback if the model can't download), tau-sweep-calibrated
threshold.
Key decision: running the tau-sweep for real (not just reasoning about
it) surfaced a false-hit mode the design hadn't anticipated -- confusable
prompts sharing template structure but naming a different entity
("capital of France" vs "capital of Japan") collide at any threshold that
keeps hit rate high. Documented as a known limitation rather than
quietly raising tau and eating the hit-rate cost.
Hard question: why cosine similarity and not a cross-encoder reranker for
the top candidate? Would likely fix the entity-collision problem at the
cost of a second model call on every cache lookup -- exactly the kind of
cost/latency trade a "cache" isn't supposed to introduce.

## Chaos + load benchmarks (benchmarks/chaos/, benchmarks/locust/)
What: SARATHI_DEMO_MODE registers a second mock-backed provider so a real
multi-provider failover chain is exercisable with zero credentials;
Locust runs as a real subprocess (not imported) to avoid gevent/asyncio
interference with this script's own event loop.
Key decision: measure "gateway overhead" as gateway-p50 minus a raw
in-process provider call, not as a guess -- came out to ~2-5ms, small
enough to state as a real number rather than round it to "negligible."
Hard question: is DEMO_MODE a security risk if accidentally left on in
LIVE? No key material is affected (it only fills an *unconfigured* slot),
but it's honest to say it's a demo-only knob that should stay off in LIVE
deployments; nothing currently prevents it from being set alongside real
keys.
