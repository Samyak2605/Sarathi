"""Embedded dashboard -- one HTML route in the same service, no second
app to deploy. Reads straight from the metering DB (SQLite locally,
Supabase in LIVE mode) via the same Storage interface everything else
uses. Styled per the project's dataviz conventions: fixed categorical
hue order (color = tier, not row index), a reserved status palette for
breaker state, tabular figures in data columns, selected light/dark mode.
"""

from __future__ import annotations

import html
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway.metering.pricing import tier_of

router = APIRouter()

TIER_COLOR_VAR = {
    "small": "--series-blue",
    "mid": "--series-yellow",
    "large": "--series-violet",
    "cache": "--series-aqua",
}
STATE_STATUS = {"closed": "good", "half_open": "warning", "open": "critical"}
STATE_LABEL = {"closed": "Closed", "half_open": "Half-open", "open": "Open"}


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def _model_color_var(model: str) -> str:
    if model == "cache":
        return TIER_COLOR_VAR["cache"]
    return TIER_COLOR_VAR.get(tier_of(model), "--series-magenta")


def _bar_row(label: str, value: float, max_value: float, color_var: str, value_label: str) -> str:
    pct = (value / max_value * 100) if max_value > 0 else 0
    return f"""
    <div class="bar-row">
      <div class="bar-label">{html.escape(label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%;background:var({color_var})"></div></div>
      <div class="bar-value">{value_label}</div>
    </div>"""


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

        model_key = "cache" if r.cache_status.startswith("hit") else (r.model_used or "(none)")
        m = by_model.setdefault(model_key, {"requests": 0, "cost": 0.0})
        m["requests"] += 1
        m["cost"] += r.cost_inr

    breaker_rows = ""
    for name, breaker in registry.breakers.items():
        snap = breaker.snapshot()
        status = STATE_STATUS.get(snap["state"], "good")
        label = STATE_LABEL.get(snap["state"], snap["state"])
        breaker_rows += f"""
        <tr><td>{html.escape(name)}</td>
        <td><span class="badge badge-{status}"><span class="dot"></span>{label}</span></td>
        <td class="num">{snap["events_in_window"]}</td></tr>"""

    if by_key:
        max_key_cost = max(v["cost"] for v in by_key.values()) or 1.0
        key_bars = "".join(
            _bar_row(
                f"{k[:14]}…",
                v["cost"],
                max_key_cost,
                "--series-blue",
                f"Rs{v['cost']:.4f} · {v['requests']} req",
            )
            for k, v in sorted(by_key.items(), key=lambda kv: -kv[1]["cost"])[:8]
        )
    else:
        key_bars = '<p class="empty">No requests yet in the last 7 days.</p>'

    if by_model:
        max_model_cost = max(v["cost"] for v in by_model.values()) or 1.0
        model_bars = "".join(
            _bar_row(
                m,
                v["cost"],
                max_model_cost,
                _model_color_var(m),
                f"Rs{v['cost']:.4f} · {v['requests']} req",
            )
            for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"])[:8]
        )
    else:
        model_bars = '<p class="empty">No requests yet in the last 7 days.</p>'

    if records:
        recent_rows = "".join(
            f"""<tr><td class="num muted">{time.strftime("%H:%M:%S", time.localtime(r.created_at))}</td>
            <td>{html.escape(r.model_used or "-")}</td>
            <td class="muted">{html.escape(r.route_tier or "-")}</td>
            <td>{html.escape(r.cache_status)}</td>
            <td>{html.escape(r.outcome)}</td>
            <td class="num">{r.latency_ms:.0f}ms</td>
            <td class="num">Rs{r.cost_inr:.5f}</td></tr>"""
            for r in records[:50]
        )
    else:
        recent_rows = '<tr><td colspan="7"><p class="empty">Nothing yet -- send a request to /v1/chat/completions and reload.</p></td></tr>'

    html_page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Sarathi Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    color-scheme: light dark;
    --page: #f9f9f7; --surface-1: #fcfcfb; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --muted: #898781; --grid: #e1e0d9;
    --border: rgba(11,11,11,0.10);
    --series-blue: #2a78d6; --series-aqua: #1baf7a; --series-yellow: #eda100;
    --series-violet: #4a3aa7; --series-magenta: #e87ba4;
    --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --page: #0d0d0d; --surface-1: #1a1a19; --text-primary: #ffffff;
      --text-secondary: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
      --border: rgba(255,255,255,0.10);
      --series-blue: #3987e5; --series-aqua: #199e70; --series-yellow: #c98500;
      --series-violet: #9085e9; --series-magenta: #d55181;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          margin: 0; background: var(--page); color: var(--text-primary); }}
  .num {{ font-variant-numeric: tabular-nums; }}
  .muted {{ color: var(--text-secondary); }}
  header {{ padding: 28px 32px; border-bottom: 1px solid var(--border); }}
  header h1 {{ margin: 0; font-size: 21px; font-weight: 650; letter-spacing: -.01em; }}
  header p {{ margin: 4px 0 0; color: var(--text-secondary); font-size: 13px; }}
  main {{ padding: 28px 32px 8px; max-width: 1180px; margin: 0 auto; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px; margin-bottom: 32px; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border);
           border-radius: 10px; padding: 16px; }}
  .card .label {{ font-size: 11px; color: var(--muted); text-transform: uppercase;
                  letter-spacing: .05em; font-weight: 600; }}
  .card .value {{ font-size: 25px; font-weight: 650; margin-top: 6px;
                  font-variant-numeric: tabular-nums; letter-spacing: -.01em; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  section {{ margin-bottom: 32px; }}
  section h2 {{ font-size: 12px; text-transform: uppercase; color: var(--muted);
                letter-spacing: .06em; font-weight: 650; margin: 0 0 12px; }}
  .panel {{ background: var(--surface-1); border: 1px solid var(--border);
            border-radius: 10px; padding: 18px; overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--grid); }}
  th {{ color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase;
        letter-spacing: .04em; }}
  tr:last-child td {{ border-bottom: none; }}
  .badge {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12px;
            font-weight: 600; padding: 2px 8px 2px 6px; border-radius: 999px;
            border: 1px solid var(--border); }}
  .badge .dot {{ width: 7px; height: 7px; border-radius: 50%; display: inline-block; }}
  .badge-good {{ color: var(--good); }} .badge-good .dot {{ background: var(--good); }}
  .badge-warning {{ color: var(--warning); }} .badge-warning .dot {{ background: var(--warning); }}
  .badge-critical {{ color: var(--critical); }} .badge-critical .dot {{ background: var(--critical); }}
  .bar-row {{ display: grid; grid-template-columns: 130px 1fr auto; align-items: center;
              gap: 12px; padding: 7px 0; }}
  .bar-label {{ font-size: 12.5px; color: var(--text-secondary);
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .bar-track {{ background: var(--grid); border-radius: 4px; height: 10px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 4px; min-width: 2px; }}
  .bar-value {{ font-size: 12px; color: var(--text-secondary); white-space: nowrap;
                font-variant-numeric: tabular-nums; }}
  .empty {{ color: var(--muted); font-size: 13px; padding: 12px 0; margin: 0; }}
  footer {{ padding: 20px 32px 32px; color: var(--muted); font-size: 12px; max-width: 1180px;
            margin: 0 auto; }}
</style></head>
<body>
<header>
  <h1>Sarathi</h1>
  <p>Cost, latency, cache and reliability across every request — last 7 days</p>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="label">Requests</div><div class="value">{total_requests:,}</div></div>
    <div class="card"><div class="label">Total cost</div><div class="value">Rs{total_cost:.3f}</div></div>
    <div class="card"><div class="label">Tokens</div><div class="value">{total_tokens:,}</div></div>
    <div class="card"><div class="label">Cache hit rate</div><div class="value">{cache_rate:.1f}%</div></div>
    <div class="card"><div class="label">Failovers</div><div class="value">{failover_count}</div></div>
    <div class="card"><div class="label">Errors</div><div class="value">{error_count}</div></div>
    <div class="card"><div class="label">p50 / p95 / p99</div>
      <div class="value" style="font-size:16px">{p50:.0f} / {p95:.0f} / {p99:.0f}<span class="muted"> ms</span></div></div>
  </div>

  <div class="grid-2">
    <section><h2>Circuit breakers</h2>
      <div class="panel"><table><tr><th>Provider</th><th>State</th><th>Events</th></tr>{breaker_rows}</table></div>
    </section>
    <section><h2>Cost by model</h2>
      <div class="panel">{model_bars}</div>
    </section>
  </div>

  <section><h2>Cost by API key</h2>
    <div class="panel">{key_bars}</div>
  </section>

  <section><h2>Recent requests</h2>
    <div class="panel"><table>
      <tr><th>Time</th><th>Model</th><th>Tier</th><th>Cache</th><th>Outcome</th><th>Latency</th><th>Cost</th></tr>
      {recent_rows}
    </table></div>
  </section>
</main>
<footer>Reload to refresh. This view is live operational data from the metering DB —
mock-vs-live provider labeling for benchmark evidence lives in <code>results/</code>, not here.</footer>
</body></html>"""
    return HTMLResponse(content=html_page)
