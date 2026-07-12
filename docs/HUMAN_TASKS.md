# Human tasks

Things only you can do (credentials, clicks, judgment calls). I will keep
this file current and stop to ask when I hit one of these instead of
faking a result.

## Pending

- [ ] **Groq API key** — free tier, for LIVE mode provider adapter + the one
      LIVE benchmark session. Get at https://console.groq.com
- [ ] **Gemini API key** — free tier, for LIVE mode provider adapter + LIVE
      benchmark session. Get at https://aistudio.google.com/app/apikey
- [ ] **Supabase project** (Postgres + pgvector) — for LIVE keys/usage DB and
      vector cache. Free tier at https://supabase.com. Need: project URL +
      service key.
- [ ] **Upstash Redis** — free tier, for LIVE rate limiting. https://upstash.com
      Need: REST URL + token.
- [ ] **Render account + deploy** — free tier. `render.yaml` is already in
      the repo root (Blueprint spec) so connecting the repo at
      https://render.com should auto-detect it; fill in GROQ_API_KEY /
      GEMINI_API_KEY / SUPABASE_* / UPSTASH_* as available (all optional --
      the service runs in LOCAL mode with none of them set).
- [ ] **SupportMind 2.0 base_url swap** — point it at the deployed Sarathi
      URL so its traffic shows up on the dashboard for the README screenshot.
- [ ] **Sit through one LIVE benchmark session** — I'll hand you scripts to
      run once Groq/Gemini keys are in `.env`; this produces the real cost
      and quality-parity numbers for the README.
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

(none yet)

## Notes

Nothing above blocks building the gateway itself — everything runs in
LOCAL mode (mock provider, SQLite, in-memory rate limiting) with zero
credentials. I'll build and test all of that first, then come back to
this list only when a LIVE-only step is actually next.
