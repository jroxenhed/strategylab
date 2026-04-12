# Equity Curve Macro Mode

Zoomed-out equity curve view for long timescales and high-frequency backtests. Aggregates raw per-bar equity data into daily/weekly/monthly/quarterly/yearly buckets with a rich visual display: baseline-colored close line, stepped high/low band with drawdown shading, and trade density ticks that blend into a heatmap at scale.

## Problem

Backtesting on intraday intervals over long periods (e.g. 5min over 4 years) produces 200k+ equity points. The current equity curve can't zoom out enough to show the big picture — you're stuck scrolling through small slices. The main chart is so heavy it gets disabled entirely, and even the equity curve only shows fragments.

Even for shorter backtests (daily over 1–2 years), there's no way to see period-level performance at a glance — which weeks were good, which months were bad.

## Approach

Backend aggregation with a frontend macro toggle. The raw backtest runs once and caches its result. A separate macro endpoint re-aggregates the cached data into the requested bucket size. Frontend switches between "Detail" (current behavior) and macro mode via bucket buttons.

## Backend

### New endpoint: `POST /api/backtest/macro`

Accepts the same `StrategyRequest` body plus:

```python
macro_bucket: str  # "D", "W", "M", "Q", "Y"
```

**Caching strategy:** The first backtest run (via existing `POST /api/backtest`) caches the raw equity curve and trades list in a module-level dict, keyed by a hash of the `StrategyRequest` (excluding `macro_bucket`). The macro endpoint re-aggregates from this cache rather than re-running the simulation. If no cache hit, it runs the full backtest first. Single-entry cache (only the most recent backtest) — keeps it simple and avoids memory bloat. No TTL needed since the user will always re-run the backtest if they change parameters.

### Bucket data shape

Each bucket in the response:

```python
{
    "time": "2024-01-15",       # bucket start date (string, daily+ format)
    "open": 10234.50,           # equity at first bar of bucket
    "high": 10890.00,           # peak equity within bucket
    "low": 10100.20,            # trough equity within bucket
    "close": 10650.00,          # equity at last bar of bucket
    "drawdown_pct": -3.2,       # max drawdown from running peak within bucket
    "trades": [                 # trades that closed within this bucket
        {"pnl": 45.20},
        {"pnl": -12.80}
    ]
}
```

### Response shape

```python
{
    "macro_curve": [...],       # list of buckets
    "summary": {...},           # same summary stats as regular backtest
    "bucket": "W"               # echo back the bucket size used
}
```

### Aggregation logic (pandas)

- Group raw equity points by bucket period using `pd.Grouper(freq=bucket)`
- Per group: first → open, max → high, min → low, last → close
- `drawdown_pct`: track running peak across all buckets; within each bucket compute `(low - running_peak) / running_peak * 100`
- Trades: match each closed trade to its bucket by exit date

## Frontend

### Controls

A button row in the equity curve tab, next to the existing baseline checkbox:

```
[Show buy & hold baseline]     Detail | D | W | M | Q | Y
```

- **"Detail"** — current full-resolution behavior, synced to main chart
- **D / W / M / Q / Y** — macro mode, decoupled from main chart
- Active button highlighted blue (same style as tab bar active state)
- First click fires the macro endpoint; result is cached so switching back is instant
- Switching back to "Detail" restores synced full-resolution view

### Auto-default bucket

When first entering macro mode, auto-select based on raw data density:

| Raw equity points | Default bucket |
|---|---|
| < 500 | W |
| 500–5,000 | D |
| 5,000–50,000 | W |
| 50,000+ | M |

User can always override to any bucket size.

### State persistence across backtests

- **Active tab** (summary / equity / trades / trace) persists when new backtest results arrive — no more resetting to summary
- **Macro bucket selection** persists across backtests — if "W" was selected, the next backtest auto-fetches weekly macro

Both are React state that lives above the Results component, not reset on new results.

### Macro chart rendering

**Close line:**
- `BaselineSeries` with `baseValue` at initial capital (same as current equity curve)
- Green above initial capital, red below

**High/low band (stepped):**
- Two line series (high and low) with area fill between them
- Stepped at bucket boundaries — each bucket is a flat segment, not interpolated between buckets
- Band color shifts from blue (normal) to red based on `drawdown_pct` of each bucket
- Calm periods: subtle blue fill (`rgba(88, 166, 255, 0.12)`)
- Deep drawdown periods: red fill, intensity proportional to drawdown depth

**Trade density ticks:**
- Thin vertical marks along the bottom edge of the chart
- Each trade is one mark, colored by P&L:
  - Green for winners, red for losers
  - Color intensity proportional to P&L magnitude (larger win = brighter green, larger loss = brighter red)
- When sparse (few trades per bucket): individual ticks are distinct and visible
- When dense (many trades per bucket): ticks naturally overlap and blend into a heatmap effect — cluster of mostly-green = good stretch, cluster of mostly-red = rough stretch
- Implemented as a histogram series on a hidden price scale pinned to the chart bottom

**No main chart sync in macro mode.** Chart calls `fitContent()` to show the full curve.

**Crosshair tooltip:** On hover, shows bucket details — period label, open/close equity, high/low, return %, number of trades.

## Out of scope

- **Main chart macro mode** — aggregating candlesticks + indicators for zoomed-out main chart is a separate problem
- **Persisting macro settings to saved strategies** — bucket selection is session state only
- **Custom bucket sizes** (e.g. "2 weeks") — D/W/M/Q/Y only
- **Baseline curve in macro mode** — buy & hold overlay not available in macro v1; could aggregate baseline the same way later
