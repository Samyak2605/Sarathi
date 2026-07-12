# Human tasks

Things only you can do (credentials, clicks, judgment calls). I will keep
this file current and stop to ask when I hit one of these instead of
faking a result.

## Pending

- [ ] **Fix the Gemini API key.** Every model returns `429 RESOURCE_EXHAUSTED
      ... limit: 0` — a quota-provisioning issue, not a code bug (Groq works
      fine with the same code path). The key you gave (`AQ.Ab8RN6...`) doesn't
      match the standard Gemini Developer API key format (`AIzaSy...`) issued
      by https://aistudio.google.com/app/apikey — go there directly, click
      "Get API key" → "Create API key in new project", and swap the value in
      `.env`. Until then, `policies/failover.yaml`'s mid tier runs Groq
      primary / Gemini fallback, so the gateway degrades gracefully instead
      of failing.
- [ ] **Supabase project** — SUPABASE_URL/SERVICE_KEY are in `.env`, but
      `docs/supabase_schema.sql` still needs to be run once in the Supabase
      SQL editor (dashboard → SQL Editor → paste the file → run) before
      SARATHI_MODE=live can use it — PostgREST has no way to create the
      tables/pgvector index itself.
- [ ] **SupportMind 2.0 integration** — on hold per your instruction until
      that product is ready; you'll share the repo/local folder when it's
      time to wire it in.
- [ ] **Full LIVE benchmark re-run** — Groq connectivity is verified
      (`GroqProvider` returned a real completion), but the benchmark scripts
      in `benchmarks/` haven't been re-run against LIVE yet (still
      `provider=mock` in `results/`). Say the word and I'll run them for real
      now that Groq works.
- [ ] **Record the chaos-kill demo video.** `benchmarks/chaos/run_chaos_test.py`
      already produces the numeric evidence (results/chaos/chaos_test.json +
      .png) automatically. For an actual on-camera recording:
      1. `SARATHI_DEMO_MODE=true uvicorn gateway.api.main:app` (registers a
         mock stand-in under the "groq" slot so there's a real 2-provider
         chain to fail over across, with zero credentials).
      2. Open `http://127.0.0.1:8000/dashboard` in a browser, screen-recorded.
      3. In another terminal, start sending traffic, e.g. a shell loop of
         `curl -s http://127.0.0.1:8000/v1/chat/completions -H "Authorization: Bearer sk-local-dev" -d '{"messages":[{"role":"user","content":"hi"}],"temperature":0.7}'`.
      4. Kill it on camera: `curl -X POST localhost:8000/admin/chaos -H "x-admin-token: change-me" -d '{"provider":"groq","inject_500":true}'`
         and narrate the dashboard's breaker state flipping to "open" while
         traffic keeps succeeding.
      5. Revive it: same command with `"inject_500": false`.

## Resolved

- [x] Groq API key — provided, verified working with a real completion.
- [x] Upstash Redis — URL + token in `.env`.
- [x] Render account + deploy — live at the Render-assigned URL, deployed
      from `main` via the `render.yaml` blueprint.
- [x] GitHub repo pushed — https://github.com/Samyak2605/Sarathi

## Notes

Nothing above blocks building the gateway itself — everything runs in
LOCAL mode (mock provider, SQLite, in-memory rate limiting) with zero
credentials. I'll build and test all of that first, then come back to
this list only when a LIVE-only step is actually next.
