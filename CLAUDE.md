# StrategyLab — Claude Context

Interactive trading strategy backtester + live paper trading platform. Read this before touching anything.

> **First-time contributor?** See [ONBOARDING.md](ONBOARDING.md) for setup.

This file documents non-obvious architecture and runtime behavior — the stuff a fresh agent (or human) needs to know before editing. Patterns marked in **Key Bugs Fixed** are authoritative: if code looks like it invites a "simpler" approach that conflicts with that section, don't take it.

## Research Program

Signal research (axioms, program state, charter queue, methodology rules) lives in **[docs/research/PROGRAM.md](docs/research/PROGRAM.md)** — load it when touching `backend/research/`, charters, validations, or premise ideation; don't carry it in platform sessions. Two rules survive here because they bind all research work: **every charter states its sampling clock + design MDE** (run `backend/research/power_audit.py` before locking), and **new instruments need a real-data smoke probe with pre-stated anchors before their output is believed (F338)**. Promote durable research findings to PROGRAM.md immediately — never batch for session end.

## Chart.tsx Architecture

Key files (others are standard-named, discoverable by grep):
- `frontend/src/App.tsx` — central hub for state, data fetching, layout
- `frontend/src/features/chart/Chart.tsx` — read this section before editing
- `backend/signal_engine.py` — Rule model, eval_rules()
- `backend/bot_runner.py` — async polling loop, entry/exit/fill management
- `backend/broker.py` — TradingProvider protocol + broker registry
- `backend/journal.py` — log_trade(), compute_realized_pnl()

Three `IChartApi` instances as a flex column (main + sub-panes). Read Chart.tsx before editing.

### Pane synchronization

Pan/zoom: `subscribeVisibleLogicalRangeChange` on the main chart → `setVisibleLogicalRange()` on MACD/RSI. Uses logical (bar-index) sync. Indicator data uses **whitespace entries** (`{ time }` with no `value`) for warmup bars (e.g. RSI's first 14 points) so all charts have the same bar count and stay aligned.

MACD/RSI effects sync to the main chart's logical range on mount via `getVisibleLogicalRange()`.

Price scale alignment: `syncWidths()` equalises `rightPriceScale.minimumWidth` across all three charts. Also mirrors the main chart's left axis width onto MACD/RSI as invisible left axes — otherwise MACD/RSI plot areas start further left than the main chart. Called on every range change AND via `setTimeout(100)` on initial mount.

Crosshair sync: `subscribeCrosshairMove` on each chart → `setCrosshairPosition(NaN, param.time, seriesRef)` on the other two. Requires series refs (`candleSeriesRef`, `macdSeriesRef`, `rsiSeriesRef`).

### Series priceScaleId rules (lightweight-charts v5)

In v5, `addSeries()` without an explicit `priceScaleId` creates an **independent** scale rather than sharing 'right'. Always set explicitly:
- Candlesticks, EMA, BB → `priceScaleId: 'right'`
- SPY → `priceScaleId: 'spy-scale'` (hidden, real close prices)
- QQQ → `priceScaleId: 'qqq-scale'` (hidden, real close prices)
- Volume → `priceScaleId: 'volume'` (hidden, `scaleMargins: { top: 0.75, bottom: 0 }`)

## Backend Notes

- `_fetch()` auto-clamps date ranges to yfinance limits for intraday intervals (1m=7d, 5m/15m/30m=60d, 1h=730d)
- `_format_time()` returns `"YYYY-MM-DD"` strings for daily+ intervals and **unix timestamps** (seconds, UTC) for intraday — lightweight-charts requires unique timestamps per bar
- `_series_to_list()` lives in `routes/indicators.py`; preserves null values (for indicator warmup periods) so the frontend can use whitespace data for bar alignment

### Data providers

Four providers can be registered in `shared.py`:
- `yahoo` — yfinance, always available
- `alpaca` — Alpaca SIP feed (requires `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` in `backend/.env`), paid subscription for recent intraday
- `alpaca-iex` — Alpaca IEX feed, real-time, free tier, narrower coverage (no OTC)
- `ibkr` — IBKR via `ib_insync` (requires `IBKR_HOST` + `IBKR_PORT` env vars and running IB Gateway)

Both Alpaca providers use `Adjustment.SPLIT` so historical prices are always split-adjusted.

When Alpaca `end` date is today or future, the provider substitutes `now` so intraday bars aren't cut off at midnight UTC.

### `_fetch()` TTL cache

`shared.py` has an in-memory TTL cache on `_fetch()` (2 min intraday, 1 hour historical). **Cache is in-process memory — server restart clears it.** `GET /api/cache` for diagnostics.

### Timezone handling in Chart.tsx

lightweight-charts v5 has **no `localization.timeZone` support**. All unix timestamps are shifted to ET wall-clock time via `toET()` before being passed to any series. `toET()` uses `Intl.DateTimeFormat` with `America/New_York` to reconstruct the timestamp as UTC so the chart displays 9:30–16:00 for NYSE hours. Daily date strings pass through unchanged.

## Signal Engine

`signal_engine.py` — rule evaluation for backtester + bot runner.

`Rule` fields: `indicator`, `condition`, `value`, `param`, `threshold`, `muted`, `negated`. Conditions include crossover, above/below, crosses_above/below, turns_up/turns_down (slope change detection).

**Rule negation (NOT):** `Rule.negated: bool`. Applied in `eval_rules()` — if `negated` and `i >= 1`, the rule result is inverted. Guard condition (`i < 1`) always returns False regardless of negation. UI: small **NOT** button on each rule row in RuleRow.tsx, orange when active.

## Backtester Cost Model

`StrategyRequest` cost fields:
- `slippage_bps` — unsigned modeled cost per leg (≥ 0, default 2.0 bps). Applied as `price * (1 ± drag)` directionally (longs worse on entry / better on exit, shorts inverse). All sign/unit conventions live in `backend/slippage.py` — never reinvent them. Helpers: `slippage_cost_bps(side, expected, fill) → ≥0`, `fill_bias_bps(side, expected, fill) → signed` (positive = favorable), `decide_modeled_bps(symbol) → ModeledSlippage` (policy: empirical can only floor *up* from the 2 bps default — favorable empirical never makes the backtest cheaper).
- `per_share_rate` + `min_per_order` — per-leg commission via `per_leg_commission(shares, req)` in `routes/backtest.py`. **Default `0.0` / `0.0`** (commission-free, matches Alpaca US equities). For IBKR Fixed, set `0.0035` / `0.35`.
- `borrow_rate_annual` (default `0.5` %) — annual short borrow rate. `borrow_cost(...)` computes `shares * entry_price * (rate/100/365) * hold_days` and deducts from short PnL. Zero for longs.
- Each trade carries `slippage`, `commission`, and `borrow_cost` fields. Journal rows additionally cache `slippage_bps` (unsigned cost) when `expected_price` is set.

Slippage endpoint: `GET /api/slippage/{symbol}` returns `{modeled_bps, measured_bps, fill_bias_bps, fill_count, source}`.

## Short Selling (direction field)

`StrategyRequest` and `BotConfig` have `direction: "long" | "short"` (defaults to `"long"`). The rule engine (`eval_rules`) is **direction-agnostic** — all inversion happens at execution boundaries.

Non-obvious bits:
- Stop-loss for shorts triggers **above** entry (`high >= entry * (1 + pct)`); trailing stop tracks trough not peak.
- PnL: `(entry - exit) * shares` for shorts; trade types are `"short"` / `"cover"`.
- **No OTO brackets for shorts** — Alpaca OTO doesn't cleanly support stops above entry, so all short stops managed via polling. Same-symbol guard allows one long + one short bot simultaneously.
- `TrailingStopConfig.activate_pct` — when `activate_on_profit` is true, trailing starts only once `source_price >= entry * (1 + activate_pct/100)`. Gives positions room to breathe.

## Walk-Forward Analysis (C28)

`POST /api/backtest/walk_forward` — partitions history into rolling (or anchored) IS / OOS windows, runs a grid search on each IS window, evaluates the IS winner on the held-out OOS, rolls forward, stitches the rescaled OOS equity into a continuous curve. Output: per-window IS/OOS metrics, stability tags, WFE (mean-of-Sharpes form), param CV.

Non-obvious bits:
- **Capital reset is rescaled in post-processing**, not avoided: each `run_backtest()` call inside WFA resets `capital = req.initial_capital` (see Bot System above), producing a sawtooth if naively concatenated. The route multiplies each OOS curve by `prev_final_equity / base.initial_capital` before stitching. Don't "simplify" by chaining capital through the loop — see C28 plan + tests.
- **Regime is unconditionally stripped** at the route boundary (`base = req.base.model_copy(deep=True, update={"regime": None})`). HTF lookback would silently clip yfinance intraday limits at window boundaries. Documented limitation; deferred follow-up.
- **Intraday boundary strings need datetime precision.** Daily uses `"%Y-%m-%d"`, intraday uses `"%Y-%m-%d %H:%M:%S"` via `_format_boundary(ts, interval)` so adjacent IS-end and OOS-start bars on the same calendar day don't collide on string equality (which would re-fetch the full day and leak IS into OOS).
- **YahooProvider.fetch() accepts both date and datetime strings** via `pd.Timestamp()` since C28. The earlier strict `strptime('%Y-%m-%d')` would crash on intraday WFA window boundaries.
- **5 stability tags** (`StabilityTag = Literal[...]`): `stable_plateau` / `spike` / `low_trades_is` / `no_oos_trades` / `no_is_trades`. `low_trades_is` is NOT overwritten by `no_oos_trades` — IS-side signal is more actionable.
- **Timeout drops biased windows.** If `_WFA_TIMEOUT_SECS` fires mid-IS-grid, the partial-grid window is NOT appended (would report a winner picked from a deterministic prefix of the combo product). Loop breaks instead.
- **Frontend `WalkForwardPanel` + `OptimizerPanel` persist input config** to localStorage keyed by `(ticker, interval, source)`. Survives tab switches and page reloads. Result objects are NOT persisted (MB-scale). Param paths validated against current `paramOptions` on restore.
- **Sub-panels in `Results.tsx` stay mounted across sub-tab switches** via `display: 'none'` (same pattern as App.tsx top-level tabs, F152). Conditional `&&` rendering would unmount and lose run results on every tab click.

## Bot System

- `BotManager` singleton persists to `backend/data/bots.json`, loaded at FastAPI lifespan.
- `bot_runner._tick()` async loop per bot; uses `TradingProvider` abstraction — no direct broker SDK imports anywhere.
- Allocation **compounds**: `allocated_capital + total_pnl` (matches backtest). Position size hardcoded 100%.
- Journal rows tagged with `bot_id`; `compute_realized_pnl(symbol, direction, bot_id)` scopes per-bot so delete+recreate starts clean. Legacy untagged rows excluded.
- **IBKR integration shipped** — IB Gateway must be running on `127.0.0.1:4002` and "Read-Only API" must be unchecked (Gateway → Configure → Settings → API), otherwise order submission returns Error 321.

## Key Bugs Fixed

These document **why** certain patterns exist in the code:

- **yf.download() concurrency**: `yfinance.download()` shares global state, returns wrong data under concurrent requests. All code uses `yf.Ticker(symbol).history()` via `_fetch()`.
- **Bot P&L leak across recreations**: `compute_realized_pnl` filtered journal rows by `(symbol, direction)` only, so a new bot on the same symbol inherited the old (deleted) bot's P&L and sizing. Fixed by tagging every `_log_trade` with `bot_id` and filtering by it.
- **Silent drop of bot config fields**: `AddBotRequest` in `routes/bots.py` duplicated `BotConfig` fields; any field missing from the duplicate was silently dropped by Pydantic's `extra="ignore"` default and replaced by the `BotConfig` default. Fixed by using `BotConfig` directly as the POST body schema.
- **Chart teardown race on ticker change**: when the main chart and sibling panes (MACD/RSI/Results overlay) unmount concurrently, late callbacks can hit an already-removed `IChartApi` and throw from `paneWidgets[0]` internal state, blanking the React tree. Fixed by reading `chartRef.current` dynamically in `syncWidths` (not via closure) + try/catch body, nulling refs *before* `chart.remove()` in every cleanup, and try/catch around `setVisibleLogicalRange` / `unsubscribe*` calls on siblings. Don't "clean up" these guards.
- **Fire-and-forget notifications must use `asyncio.create_task()`, not `await`**: `await notify_*()` inside `bot_runner._tick()` blocks the polling loop — a slow or down ntfy.sh causes the bot to miss ticks (up to 10s per notification call with the httpx timeout). `create_task()` schedules the coroutine without blocking. Never `await` a non-critical side-effect in a polling loop.
- **Sync callbacks need `run_coroutine_threadsafe`, not `ensure_future`**: ib_insync dispatches error callbacks on its EReader thread, which has no running asyncio event loop. `asyncio.ensure_future()` from that thread raises `RuntimeError: no running event loop` and silently drops the notification. Fix: store `self._loop = asyncio.get_running_loop()` in the async `run()` method, then use `asyncio.run_coroutine_threadsafe(coro, self._loop)` from any sync callback.
