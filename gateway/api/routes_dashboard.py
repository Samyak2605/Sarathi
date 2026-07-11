"""Embedded dashboard -- one HTML route in the same service, no second
app to deploy. Reads straight from the metering DB (SQLite locally,
Supabase in LIVE mode) via the same Storage interface everything else
uses. Styled per the project's dataviz conventions: fixed categorical
hue order (color = tier, not row index), a reserved status palette for
breaker state, tabular figures in data columns, selected light/dark mode.
Chart.js (single axis, stacked, indexed tooltip) and Lucide icons are
loaded from CDN -- this is a served web page, not a sandboxed artifact,
so that's the pragmatic choice over hand-rolled SVG.
"""

from __future__ import annotations

import html
import json
import time
from datetime import datetime, timedelta

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
STATE_ICON = {"closed": "shield-check", "half_open": "shield-half", "open": "shield-alert"}


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


def _daily_buckets(records: list, days: int = 7) -> dict:
    now = datetime.now()
    day_keys = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    labels = [(now - timedelta(days=i)).strftime("%a %-d") for i in range(days - 1, -1, -1)]
    totals = dict.fromkeys(day_keys, 0)
    hits = dict.fromkeys(day_keys, 0)
    for r in records:
        key = datetime.fromtimestamp(r.created_at).strftime("%Y-%m-%d")
        if key in totals:
            totals[key] += 1
            if r.cache_status.startswith("hit"):
                hits[key] += 1
    non_cache = [totals[k] - hits[k] for k in day_keys]
    return {"labels": labels, "cache_hits": [hits[k] for k in day_keys], "other": non_cache}


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

    trend = _daily_buckets(records)

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
        icon = STATE_ICON.get(snap["state"], "shield")
        pulse = " pulse" if status == "critical" else ""
        breaker_rows += f"""
        <tr><td class="provider-cell"><i data-lucide="server" class="mini-icon"></i>{html.escape(name)}</td>
        <td><span class="badge badge-{status}{pulse}"><i data-lucide="{icon}" class="badge-icon"></i>{label}</span></td>
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
        key_bars = (
            '<div class="empty"><i data-lucide="inbox" class="empty-icon"></i>'
            "<p>No requests yet in the last 7 days.</p></div>"
        )

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
        model_bars = (
            '<div class="empty"><i data-lucide="inbox" class="empty-icon"></i>'
            "<p>No requests yet in the last 7 days.</p></div>"
        )

    if records:
        recent_rows = "".join(
            f"""<tr><td class="num muted">{time.strftime("%H:%M:%S", time.localtime(r.created_at))}</td>
            <td>{html.escape(r.model_used or "-")}</td>
            <td class="muted">{html.escape(r.route_tier or "-")}</td>
            <td>{_cache_pill(r.cache_status)}</td>
            <td>{_outcome_pill(r.outcome)}</td>
            <td class="num">{r.latency_ms:.0f}ms</td>
            <td class="num">Rs{r.cost_inr:.5f}</td></tr>"""
            for r in records[:50]
        )
    else:
        recent_rows = (
            '<tr><td colspan="7"><div class="empty"><i data-lucide="inbox" class="empty-icon">'
            "</i><p>Nothing yet — send a request to /v1/chat/completions and reload.</p></div>"
            "</td></tr>"
        )

    trend_json = json.dumps(trend)

    html_page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Sarathi Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🐎</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    color-scheme: light dark;
    --page: #f6f6f4; --surface-1: #ffffff; --text-primary: #0b0b0b;
    --text-secondary: #52514e; --muted: #898781; --grid: #e7e6e0;
    --border: rgba(11,11,11,0.08); --shadow: 0 1px 2px rgba(11,11,11,.04), 0 8px 24px -12px rgba(11,11,11,.10);
    --series-blue: #2a78d6; --series-aqua: #1baf7a; --series-yellow: #eda100;
    --series-violet: #4a3aa7; --series-magenta: #e87ba4;
    --good: #0ca30c; --warning: #b8790f; --serious: #ec835a; --critical: #d03b3b;
    --accent-grad: linear-gradient(90deg, var(--series-blue), var(--series-violet));
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --page: #0b0c0d; --surface-1: #17181a; --text-primary: #f5f5f4;
      --text-secondary: #b9b8b3; --muted: #83817b; --grid: #26272a;
      --border: rgba(255,255,255,0.08); --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 28px -12px rgba(0,0,0,.55);
      --series-blue: #4c93f0; --series-aqua: #22c98c; --series-yellow: #f0a832;
      --series-violet: #a397f2; --series-magenta: #e177a4;
      --good: #22c55e; --warning: #fbbf24; --critical: #f87171;
    }}
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{ font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          margin: 0; background: var(--page); color: var(--text-primary);
          -webkit-font-smoothing: antialiased; }}
  .num {{ font-variant-numeric: tabular-nums; }}
  .muted {{ color: var(--text-secondary); }}
  .accent-bar {{ height: 3px; background: var(--accent-grad); }}
  header {{ padding: 26px 32px; border-bottom: 1px solid var(--border);
            display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }}
  .brand {{ display: flex; align-items: center; gap: 12px; }}
  .brand-mark {{ width: 38px; height: 38px; border-radius: 10px; background: var(--accent-grad);
                 display: flex; align-items: center; justify-content: center; font-size: 19px;
                 box-shadow: var(--shadow); }}
  header h1 {{ margin: 0; font-size: 20px; font-weight: 750; letter-spacing: -.02em; }}
  header p {{ margin: 2px 0 0; color: var(--text-secondary); font-size: 12.5px; }}
  .live-pill {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600;
                color: var(--good); background: color-mix(in srgb, var(--good) 12%, transparent);
                padding: 5px 12px 5px 8px; border-radius: 999px; }}
  .live-dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--good); animation: pulse-dot 2s infinite; }}
  @keyframes pulse-dot {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: .35; }} }}
  main {{ padding: 28px 32px 12px; max-width: 1180px; margin: 0 auto; }}
  .fade-in {{ animation: fade-up .5s ease both; }}
  @keyframes fade-up {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(158px, 1fr));
            gap: 14px; margin-bottom: 28px; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px;
           padding: 18px; box-shadow: var(--shadow); transition: transform .15s ease, box-shadow .15s ease; }}
  .card:hover {{ transform: translateY(-2px); box-shadow: 0 2px 4px rgba(11,11,11,.06), 0 16px 32px -14px rgba(11,11,11,.16); }}
  .card-icon {{ width: 30px; height: 30px; border-radius: 9px; display: flex; align-items: center;
                justify-content: center; margin-bottom: 10px; background: color-mix(in srgb, var(--icon-color, var(--series-blue)) 14%, transparent); }}
  .card-icon i {{ width: 16px; height: 16px; color: var(--icon-color, var(--series-blue)); }}
  .card .label {{ font-size: 11px; color: var(--muted); text-transform: uppercase;
                  letter-spacing: .05em; font-weight: 650; }}
  .card .value {{ font-size: 26px; font-weight: 700; margin-top: 4px; letter-spacing: -.02em; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 860px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
  section {{ margin-bottom: 28px; }}
  section h2 {{ font-size: 12.5px; text-transform: uppercase; color: var(--muted);
                letter-spacing: .06em; font-weight: 700; margin: 0 0 12px; display: flex;
                align-items: center; gap: 7px; }}
  section h2 i {{ width: 14px; height: 14px; }}
  .panel {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px;
            padding: 20px; overflow-x: auto; box-shadow: var(--shadow); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 10px; border-bottom: 1px solid var(--grid); }}
  th {{ color: var(--muted); font-weight: 650; font-size: 10.5px; text-transform: uppercase;
        letter-spacing: .05em; }}
  tr:last-child td {{ border-bottom: none; }}
  tbody tr {{ transition: background .12s ease; }}
  tbody tr:hover {{ background: color-mix(in srgb, var(--series-blue) 5%, transparent); }}
  .provider-cell {{ display: flex; align-items: center; gap: 7px; }}
  .mini-icon {{ width: 14px; height: 14px; color: var(--muted); }}
  .badge {{ display: inline-flex; align-items: center; gap: 5px; font-size: 12px;
            font-weight: 650; padding: 3px 10px 3px 7px; border-radius: 999px;
            border: 1px solid var(--border); }}
  .badge-icon {{ width: 13px; height: 13px; }}
  .badge-good {{ color: var(--good); }}
  .badge-warning {{ color: var(--warning); }}
  .badge-critical {{ color: var(--critical); }}
  .badge.pulse {{ animation: pulse-badge 1.6s infinite; }}
  @keyframes pulse-badge {{ 0%,100% {{ box-shadow: 0 0 0 0 color-mix(in srgb, var(--critical) 35%, transparent); }}
                            50% {{ box-shadow: 0 0 0 5px color-mix(in srgb, var(--critical) 0%, transparent); }} }}
  .pill {{ display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px; font-weight: 600;
           padding: 2px 8px; border-radius: 999px; }}
  .pill-hit {{ color: var(--good); background: color-mix(in srgb, var(--good) 12%, transparent); }}
  .pill-miss {{ color: var(--text-secondary); background: var(--grid); }}
  .pill-ok {{ color: var(--good); background: color-mix(in srgb, var(--good) 12%, transparent); }}
  .pill-failover {{ color: var(--warning); background: color-mix(in srgb, var(--warning) 14%, transparent); }}
  .pill-error {{ color: var(--critical); background: color-mix(in srgb, var(--critical) 12%, transparent); }}
  .bar-row {{ display: grid; grid-template-columns: 130px 1fr auto; align-items: center;
              gap: 12px; padding: 8px 0; }}
  .bar-label {{ font-size: 12.5px; color: var(--text-secondary);
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .bar-track {{ background: var(--grid); border-radius: 5px; height: 10px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 5px; min-width: 3px; transition: width .6s cubic-bezier(.16,1,.3,1); }}
  .bar-value {{ font-size: 12px; color: var(--text-secondary); white-space: nowrap;
                font-variant-numeric: tabular-nums; }}
  .empty {{ display: flex; flex-direction: column; align-items: center; justify-content: center;
            color: var(--muted); font-size: 13px; padding: 32px 12px; gap: 8px; }}
  .empty-icon {{ width: 26px; height: 26px; opacity: .5; }}
  .empty p {{ margin: 0; }}
  .chart-wrap {{ position: relative; height: 220px; }}
  footer {{ padding: 24px 32px 36px; color: var(--muted); font-size: 12px; max-width: 1180px;
            margin: 0 auto; display: flex; align-items: center; gap: 8px; }}
  footer i {{ width: 13px; height: 13px; }}
  code {{ background: var(--grid); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }}
</style></head>
<body>
<div class="accent-bar"></div>
<header>
  <div class="brand">
    <div class="brand-mark">🐎</div>
    <div>
      <h1>Sarathi</h1>
      <p>Cost, latency, cache and reliability across every request</p>
    </div>
  </div>
  <span class="live-pill"><span class="live-dot"></span>Live · last 7 days</span>
</header>
<main>
  <div class="cards fade-in">
    <div class="card"><div class="card-icon" style="--icon-color:var(--series-blue)"><i data-lucide="activity"></i></div>
      <div class="label">Requests</div><div class="value">{total_requests:,}</div></div>
    <div class="card"><div class="card-icon" style="--icon-color:var(--series-violet)"><i data-lucide="indian-rupee"></i></div>
      <div class="label">Total cost</div><div class="value">Rs{total_cost:.3f}</div></div>
    <div class="card"><div class="card-icon" style="--icon-color:var(--series-yellow)"><i data-lucide="database"></i></div>
      <div class="label">Tokens</div><div class="value">{total_tokens:,}</div></div>
    <div class="card"><div class="card-icon" style="--icon-color:var(--series-aqua)"><i data-lucide="zap"></i></div>
      <div class="label">Cache hit rate</div><div class="value">{cache_rate:.1f}%</div></div>
    <div class="card"><div class="card-icon" style="--icon-color:var(--warning)"><i data-lucide="git-branch"></i></div>
      <div class="label">Failovers</div><div class="value">{failover_count}</div></div>
    <div class="card"><div class="card-icon" style="--icon-color:var(--critical)"><i data-lucide="alert-triangle"></i></div>
      <div class="label">Errors</div><div class="value">{error_count}</div></div>
    <div class="card"><div class="card-icon" style="--icon-color:var(--series-magenta)"><i data-lucide="gauge"></i></div>
      <div class="label">p50 / p95 / p99</div>
      <div class="value" style="font-size:17px">{p50:.0f} / {p95:.0f} / {p99:.0f}<span class="muted"> ms</span></div></div>
  </div>

  <section class="fade-in"><h2><i data-lucide="trending-up"></i>Requests per day</h2>
    <div class="panel"><div class="chart-wrap"><canvas id="trendChart"></canvas></div></div>
  </section>

  <div class="grid-2 fade-in">
    <section><h2><i data-lucide="shield"></i>Circuit breakers</h2>
      <div class="panel"><table><tr><th>Provider</th><th>State</th><th>Events</th></tr>{breaker_rows}</table></div>
    </section>
    <section><h2><i data-lucide="bar-chart-3"></i>Cost by model</h2>
      <div class="panel">{model_bars}</div>
    </section>
  </div>

  <section class="fade-in"><h2><i data-lucide="key-round"></i>Cost by API key</h2>
    <div class="panel">{key_bars}</div>
  </section>

  <section class="fade-in"><h2><i data-lucide="list"></i>Recent requests</h2>
    <div class="panel"><table>
      <tr><th>Time</th><th>Model</th><th>Tier</th><th>Cache</th><th>Outcome</th><th>Latency</th><th>Cost</th></tr>
      {recent_rows}
    </table></div>
  </section>
</main>
<footer><i data-lucide="info"></i>Reload to refresh. This view is live operational data from the metering DB —
mock-vs-live provider labeling for benchmark evidence lives in <code>results/</code>, not here.</footer>
<script>
  lucide.createIcons();

  // Opt-in auto-refresh for recordings/demos: /dashboard?autorefresh=3
  // reloads every 3s. Off by default so normal browsing isn't disrupted.
  const autoRefreshSeconds = Number(new URLSearchParams(location.search).get('autorefresh'));
  if (autoRefreshSeconds > 0) {{
    setTimeout(() => location.reload(), autoRefreshSeconds * 1000);
  }}

  const trend = {trend_json};
  const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const ink = isDark ? '#b9b8b3' : '#52514e';
  const grid = isDark ? '#26272a' : '#e7e6e0';
  const blue = isDark ? '#4c93f0' : '#2a78d6';
  const aqua = isDark ? '#22c98c' : '#1baf7a';

  new Chart(document.getElementById('trendChart'), {{
    type: 'bar',
    data: {{
      labels: trend.labels,
      datasets: [
        {{ label: 'Cache hits', data: trend.cache_hits, backgroundColor: aqua, stack: 's', borderRadius: 4, maxBarThickness: 24 }},
        {{ label: 'Other', data: trend.other, backgroundColor: blue, stack: 's', borderRadius: 4, maxBarThickness: 24 }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ stacked: true, grid: {{ display: false }}, ticks: {{ color: ink, font: {{ family: 'Inter', size: 11 }} }} }},
        y: {{ stacked: true, beginAtZero: true, grid: {{ color: grid }}, ticks: {{ color: ink, precision: 0, font: {{ family: 'Inter', size: 11 }} }} }}
      }},
      plugins: {{
        legend: {{ position: 'top', align: 'end', labels: {{ color: ink, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'circle', font: {{ family: 'Inter', size: 12 }} }} }},
        tooltip: {{ backgroundColor: isDark ? '#17181a' : '#ffffff', titleColor: isDark ? '#f5f5f4' : '#0b0b0b',
                    bodyColor: isDark ? '#f5f5f4' : '#0b0b0b', borderColor: grid, borderWidth: 1, padding: 10,
                    boxPadding: 4, usePointStyle: true }}
      }}
    }}
  }});
</script>
</body></html>"""
    return HTMLResponse(content=html_page)


def _cache_pill(status: str) -> str:
    cls = "pill-hit" if status.startswith("hit") else "pill-miss"
    icon = "zap" if status.startswith("hit") else "circle"
    return f'<span class="pill {cls}"><i data-lucide="{icon}" style="width:11px;height:11px"></i>{html.escape(status)}</span>'


def _outcome_pill(outcome: str) -> str:
    cls = {"ok": "pill-ok", "failover": "pill-failover", "error": "pill-error"}.get(
        outcome, "pill-miss"
    )
    icon = {"ok": "check", "failover": "git-branch", "error": "x"}.get(outcome, "circle")
    return f'<span class="pill {cls}"><i data-lucide="{icon}" style="width:11px;height:11px"></i>{html.escape(outcome)}</span>'
