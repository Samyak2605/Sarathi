"""Embedded dashboard -- one HTML route in the same service, no second
app to deploy. Reads straight from the metering DB (SQLite locally,
Supabase in LIVE mode) via the same Storage interface everything else
uses.
"""

from __future__ import annotations

import html
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    storage = request.app.state.storage
    registry = request.app.state.registry
    since = time.time() - 7 * 86400
    records = await storage.query_usage(since_ts=since)

    total_requests = len(records)
    total_cost = sum(r.cost_inr for r in records)
    total_tokens = sum(r.total_tokens for r in records)
    cache_hits = sum(1 for r in records if r.cache_status.startswith("hit"))
    cache_rate = (cache_hits / total_requests * 100) if total_requests else 0.0
    failover_count = sum(1 for r in records if r.outcome == "failover")
    error_count = sum(1 for r in records if r.outcome == "error")
    latencies = [r.latency_ms for r in records]
    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)

    by_key: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for r in records:
        k = by_key.setdefault(r.api_key, {"requests": 0, "cost": 0.0, "tokens": 0})
        k["requests"] += 1
        k["cost"] += r.cost_inr
        k["tokens"] += r.total_tokens

        m = by_model.setdefault(r.model_used or "(none)", {"requests": 0, "cost": 0.0})
        m["requests"] += 1
        m["cost"] += r.cost_inr

    breaker_rows = ""
    for name, breaker in registry.breakers.items():
        snap = breaker.snapshot()
        breaker_rows += (
            f"<tr><td>{html.escape(name)}</td><td class='state-{snap['state']}'>"
            f"{snap['state']}</td><td>{snap['events_in_window']}</td></tr>"
        )

    key_rows = "".join(
        f"<tr><td>{html.escape(k[:12])}...</td><td>{v['requests']}</td>"
        f"<td>Rs{v['cost']:.4f}</td><td>{v['tokens']}</td></tr>"
        for k, v in sorted(by_key.items(), key=lambda kv: -kv[1]["cost"])
    )
    model_rows = "".join(
        f"<tr><td>{html.escape(m)}</td><td>{v['requests']}</td><td>Rs{v['cost']:.4f}</td></tr>"
        for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])
    )
    recent_rows = "".join(
        f"<tr><td>{time.strftime('%H:%M:%S', time.localtime(r.created_at))}</td>"
        f"<td>{html.escape(r.model_used or '-')}</td><td>{html.escape(r.route_tier or '-')}</td>"
        f"<td>{html.escape(r.cache_status)}</td><td>{html.escape(r.outcome)}</td>"
        f"<td>{r.latency_ms:.0f}ms</td><td>Rs{r.cost_inr:.5f}</td></tr>"
        for r in records[:50]
    )

    html_page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Sarathi Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
          background: #0b0e14; color: #e6e6e6; }}
  @media (prefers-color-scheme: light) {{
    body {{ background: #f7f7f9; color: #1a1a1a; }}
    .card, table {{ background: #fff !important; border-color: #e0e0e6 !important; }}
  }}
  header {{ padding: 24px 32px; border-bottom: 1px solid #23262f; }}
  header h1 {{ margin: 0; font-size: 20px; }}
  header p {{ margin: 4px 0 0; opacity: .65; font-size: 13px; }}
  main {{ padding: 24px 32px; max-width: 1200px; margin: 0 auto; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 12px; margin-bottom: 28px; }}
  .card {{ background: #151922; border: 1px solid #23262f; border-radius: 10px; padding: 16px; }}
  .card .label {{ font-size: 12px; opacity: .6; text-transform: uppercase; letter-spacing: .04em; }}
  .card .value {{ font-size: 24px; font-weight: 600; margin-top: 6px; }}
  section {{ margin-bottom: 28px; overflow-x: auto; }}
  section h2 {{ font-size: 14px; text-transform: uppercase; opacity: .6; letter-spacing: .04em; }}
  table {{ width: 100%; border-collapse: collapse; background: #151922;
           border: 1px solid #23262f; border-radius: 8px; overflow: hidden; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #23262f; }}
  th {{ opacity: .6; font-weight: 500; }}
  .state-closed {{ color: #4ade80; }} .state-open {{ color: #f87171; }}
  .state-half_open {{ color: #fbbf24; }}
  footer {{ padding: 16px 32px; opacity: .5; font-size: 12px; }}
</style></head>
<body>
<header>
  <h1>Sarathi</h1>
  <p>cost, latency, cache and reliability across every request -- last 7 days</p>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="label">Requests</div><div class="value">{total_requests}</div></div>
    <div class="card"><div class="label">Total cost</div><div class="value">Rs{total_cost:.3f}</div></div>
    <div class="card"><div class="label">Tokens</div><div class="value">{total_tokens:,}</div></div>
    <div class="card"><div class="label">Cache hit rate</div><div class="value">{cache_rate:.1f}%</div></div>
    <div class="card"><div class="label">Failovers</div><div class="value">{failover_count}</div></div>
    <div class="card"><div class="label">Errors</div><div class="value">{error_count}</div></div>
    <div class="card"><div class="label">p50 / p95 / p99</div>
      <div class="value" style="font-size:16px">{p50:.0f} / {p95:.0f} / {p99:.0f} ms</div></div>
  </div>

  <section><h2>Circuit breakers</h2>
    <table><tr><th>Provider</th><th>State</th><th>Events in window</th></tr>{breaker_rows}</table>
  </section>

  <section><h2>Cost by API key</h2>
    <table><tr><th>Key</th><th>Requests</th><th>Cost</th><th>Tokens</th></tr>{key_rows}</table>
  </section>

  <section><h2>Cost by model</h2>
    <table><tr><th>Model</th><th>Requests</th><th>Cost</th></tr>{model_rows}</table>
  </section>

  <section><h2>Recent requests</h2>
    <table><tr><th>Time</th><th>Model</th><th>Tier</th><th>Cache</th><th>Outcome</th>
      <th>Latency</th><th>Cost</th></tr>{recent_rows}</table>
  </section>
</main>
<footer>Auto-refreshes on reload. Mock-provider traffic is labeled in benchmarks/results, not here --
this view is live operational data.</footer>
</body></html>"""
    return HTMLResponse(content=html_page)
