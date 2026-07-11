# Human tasks

Things only you can do (credentials, clicks, judgment calls). I will keep
this file current and stop to ask when I hit one of these instead of
faking a result.

## Pending

- [ ] **Fix the Gemini API key** (optional -- Groq covers LIVE mode).
      Every model returns `429 RESOURCE_EXHAUSTED ... limit: 0` — a
      quota-provisioning issue, not a code bug (Groq works fine with the
      same code path). The key you gave (`AQ.Ab8RN6...`) doesn't match
      the standard Gemini Developer API key format (`AIzaSy...`) issued
      by https://aistudio.google.com/app/apikey, which has its own free
      tier — go there directly, click "Get API key" → "Create API key in
      new project", and swap the value in `.env`. Not a paid-only
      feature as far as I can tell, just a mis-provisioned key; if AI
      Studio genuinely won't issue a free key for your account/region,
      that's worth confirming before writing it off. Until fixed,
      `policies/failover.yaml`'s mid tier runs Groq primary / Gemini
      fallback, so the gateway degrades gracefully instead of failing.
- [ ] **Full-scale LIVE benchmark run** (optional, accepted limitation) —
      small paced LIVE samples exist (`results/cost/cost_report_live.json`,
      `results/routing/parity_live.json`, `results/canary/latest.json`),
      but only 12-15 requests each, not the full 500/1,000-request mock
      scale. Groq's free tier rate-limits `llama-3.3-70b-versatile` (the
      large tier) hard -- often 2-3 successes per 30 attempts even at one
      request per 2.5s. Running the full scale would need either much
      longer pacing (slow) or a paid Groq tier.

## Resolved

- [x] Groq API key — provided, verified working with a real completion.
- [x] Upstash Redis — URL + token in `.env`.
- [x] Supabase project — `docs/supabase_schema.sql` run in the Supabase
      SQL editor; tables + pgvector index confirmed created.
- [x] Render account + deploy — live at the Render-assigned URL, deployed
      from `main` via the `render.yaml` blueprint.
- [x] GitHub repo pushed — https://github.com/Samyak2605/Sarathi
- [x] SupportMind 2.0 integration — pointed at Sarathi via `base_url`
      (zero code changes beyond adding the field), verified end to end
      (Ask + Resolve, real cache hit), recorded on video.
- [x] Chaos-kill demo video recorded — combined with the cache-hit demo
      in one take. Both recordings linked from the README's Demo section
      (GitHub release `demo-v1`).

## Notes

Nothing above blocks building the gateway itself — everything runs in
LOCAL mode (mock provider, SQLite, in-memory rate limiting) with zero
credentials. I'll build and test all of that first, then come back to
this list only when a LIVE-only step is actually next.
