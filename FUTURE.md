# Future work (explicitly out of scope for v1)

- **Entity-aware semantic cache guard.** results/cache/tau_sweep.json shows
  that at the operating threshold (tau=0.90), ~7.5% of "same template,
  different entity" prompts (e.g. "capital of France" vs "capital of
  Japan") still collide in the semantic tier, since embedding similarity
  is dominated by template structure over the one varying entity token.
  A fix would extract key entities/nouns and require them to match (or
  differ) as a second guard alongside cosine similarity, not just raise
  tau further (which trades away real hit rate, per the same chart).

- Hedged requests (fire to two providers, take the first valid response)
- Multi-region deployment / edge routing
- Admin UI polish beyond the embedded dashboard (auth, charts library, dark mode)
- Additional provider adapters (OpenAI, Anthropic, Bedrock, Azure)
- Per-tenant fine-grained RBAC
- Streaming function/tool-call passthrough beyond basic text SSE
- Adaptive tau (auto-tuning cache threshold from live false-hit measurements)
