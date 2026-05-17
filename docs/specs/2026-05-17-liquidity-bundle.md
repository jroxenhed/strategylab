# Liquidity bundle — spec

**Status:** draft (2026-05-17) — pre-ideation; not yet broken into F-items.
**Owner:** john
**Purpose:** scope the full landscape of liquidity-aware features for StrategyLab, tier them by value-per-effort, and make explicit which require data infra changes vs. plug into the existing indicator pipeline.

---

## TL;DR

Five tiers, ordered by value-per-effort:

| Tier | Theme | Items | Build cost | Edge per item |
|------|-------|-------|------------|---------------|
| **T1** | Existing-pipeline upgrades | Session VWAP (fix), VWAP bands, prior session H/L/C carryforward, RVOL (same-time-of-day) | days | high — table stakes, dual-use |
| **T2** | New visual primitives | Volume Profile (POC/VAH/VAL), Anchored VWAP | 1–2 wks | mid — visual edge, weakly mechanizable |
| **T3** | Cross-sectional liquidity filters | Amihud illiquidity, dollar-volume regime | days | mid — screener/regime use |
| **T4** | Order-flow analytics | CVD, footprint, bid-ask spread history | 3–6 wks + tick data ingestion | high if data is real, low if approximated |
| **T5** | Microstructure academia | Kyle's lambda, Roll's spread estimator | days (Roll), weeks (Kyle) | low — mostly subsumed by Amihud for retail use |

Recommendation: **ship T1 fully**, then T2 + T3 in parallel, then evaluate whether T4 is worth the data-infra cost based on whether T1 strategies meaningfully outperform baselines on WFA OOS.

---

## Background

### Why liquidity matters

Two practitioner intuitions both point at liquidity:

1. **"Price moves where liquidity is thin"** — the move-amplification observation. Stops cluster at prior session high/low (PDH/PDL); breaking them triggers the cascade. Low-volume nodes in the volume profile let price slide quickly through to the next high-volume node.
2. **"VWAP is the institutional anchor"** — algorithmic execution programs mean-revert to VWAP intraday, producing a reflexive tendency for price to return to it. Zarattini & Aziz (SSRN 4631351) report a long-/short-VWAP system on QQQ with Sharpe ~2.1 (single instrument, likely overfit; useful as proof-of-concept not gospel).

For a ~$10k retail account, liquidity is **not** primarily about cost management (you're not paying real impact). It's about **who else is trading**:
- Surges → institutional interest, signal worth confirming;
- Fade-outs → losing thesis support;
- Levels (PDH, VAH, POC) → predictable counter-party reactions.

### What's already in the codebase

- `backend/indicators.py:105–110` — `compute_vwap()` exists but is **naive cumulative** across the entire range, not session-anchored. Useless for intraday session-VWAP semantics. Listed in `INDICATOR_REGISTRY:154`.
- `backend/indicators.py:89–90` — `compute_volume()` exists, returns raw volume series.
- `backend/signal_engine.py:11–16` — `RuleIndicator` Literal allowlist: `vwap` is **NOT** in the rule allowlist. VWAP can only be drawn on the chart; it cannot drive an entry/exit rule today.
- `backend/shared.py:169` — RTH session boundary (09:30–16:00 ET) is already computed for the Alpaca path. The pattern is reusable for session-anchored indicators.
- `frontend/src/shared/types/indicators.ts:131–136` — VWAP frontend def has zero params (no anchor, no bands).
- `frontend/src/features/chart/Chart.tsx:498–503` — VWAP main-pane overlay rendering already wired.
- No prior session H/L carryforward, no volume profile, no RVOL, no Amihud, no CVD, no footprint.

### Data feeds available

- **yfinance** — bars OHLCV, no tick / quote / depth. Free fallback.
- **Alpaca SIP** (paid) — bars + historical trades (tick) + historical quotes (NBBO). No depth-of-book. Used today.
- **Alpaca IEX** (free) — IEX-routed bars only; narrower coverage; trades & quotes available but only IEX-routed.
- **IBKR via ib_insync** — bars + tick-by-tick (`reqTickByTickData`, rate-limited) + Level 2 (`reqMktDepth`, real-time only, paid market data). No historical depth.

Practical implication: **tick + aggressor side is available on Alpaca SIP and IBKR live**, but you'd need a new pipeline to fetch + store + replay it for backtesting. None of that exists today. T4 features hinge on building this pipeline.

---

## T1 — Existing-pipeline upgrades (highest value-per-effort)

These plug into the existing indicator framework with minimal new machinery. Each is dual-use: chart overlay AND rule indicator. Ship first.

### T1.1 — Session VWAP (fix + bands)

**What:** Replace `compute_vwap()` with a session-anchored implementation that resets `cumsum` at each session boundary (09:30 ET for intraday; daily bars have no intra-day session, so degrade to a simple weekly anchor or "since start of range"). Add ±1σ and ±2σ deviation bands.

**Formula:**
```
vwap_t = Σ(typical_price × volume) / Σ(volume)   [grouped by session]
σ_t    = √(Σ((typical - vwap)² × volume) / Σ(volume))   [grouped by session]
upper_t = vwap_t + k × σ_t
```

**Code paths:**
- `backend/indicators.py:105` — rewrite `compute_vwap()`. Group by ET trading date (use pattern from `shared.py:169`).
- Add `bands` param: `{ "stddev": 1.0 }`. Returns `vwap`, `upper`, `lower`.
- `backend/signal_engine.py:11` — add `"vwap"` to `RuleIndicator` Literal.
- `backend/signal_engine.py:425+` (`eval_rule`) — VWAP works with existing `above/below/crosses_above/crosses_below`. No new condition needed.
- `backend/signal_engine.py:311+` (`resolve_series`) — wire VWAP series lookup.
- `frontend/src/shared/types/indicators.ts:131–136` — add `stddev` param field.
- `frontend/src/features/strategy/RuleRow.tsx` — add VWAP to rule-indicator dropdown.
- `frontend/src/features/chart/Chart.tsx:498` — render upper/lower bands as line series at lower opacity.

**Dependencies:** `pandas` group-by-date is sufficient; no new libs.

**Example rules:**
- "Buy: `PRICE crosses_above VWAP` AND `RVOL > 1.5`"
- "Sell: `PRICE > VWAP + 2σ`" (fade extreme deviation)

**Cost:** 1–2 days.

### T1.2 — Prior session H/L/C (PDH, PDL, PDC, PWH, PWL, PWC, PMH, PML, PMC)

**What:** Carryforward step-line indicators of prior session's high, low, and close. Three windows: previous day, previous week, previous month. Render as horizontal step lines on the main chart; expose as rule indicators.

**Formula (PDH):**
```
session_date = bar's ET date
prior_session_high(t) = max(high) over the calendar day before bar t's session
# carryforward — value remains constant within today's session
```

**Code paths:**
- `backend/indicators.py` — new `compute_prior_session_levels(ohlcv, params)` returning `{pdh, pdl, pdc, pwh, pwl, pwc, pmh, pml, pmc}`. Resample → shift → reindex with forward-fill.
- `backend/indicators.py:145` — register as `"prior_session"` in `INDICATOR_REGISTRY`.
- `backend/signal_engine.py:11` — add `"prior_session"` to `RuleIndicator`. Param distinguishes which level (`"pdh"`, `"pwh"`, etc).
- `frontend/src/shared/types/indicators.ts` — new indicator def with `window: 'day' | 'week' | 'month'` and `level: 'high' | 'low' | 'close'`.
- `frontend/src/features/chart/Chart.tsx` — render as `addLineSeries({ lineStyle: LineStyle.Dashed })` with step interpolation (lightweight-charts v5 supports `priceLineSource` for step-style; otherwise manually emit one point per bar).

**Special case — daily/weekly base TF:** When the base interval IS daily, PDH/PDL are just `high.shift(1)` / `low.shift(1)`. Trivial.

**Example rules:**
- "Buy: `PRICE crosses_above PDH` AND `RVOL > 2.0`" (classic ORB breakout)
- "Sell stop: `PRICE crosses_below PDL`"
- "Filter: only fire entries between PDL and PDH" (intraday-range condition)

**Cost:** 2 days, mostly UI for the level/window picker and chart step-line rendering.

### T1.3 — Relative Volume (RVOL), same-time-of-day variant

**What:** Current bar volume normalized by median (or mean) volume at the same bar-of-day across the prior N sessions. RVOL=1 = typical, RVOL=2 = double, RVOL>2 = surge.

**Why same-time-of-day not simple ratio:** the intraday volume curve is U-shaped — first/last 30 min are 5–10× midday. A simple ratio of today's volume to a 20-day daily average is meaningless at 09:35.

**Formula:**
```
bucket = bar's (hour, minute) in ET
typical_vol(bucket) = median(volume) at same bucket over prior N sessions
rvol_t = volume_t / typical_vol(bucket_t)
```

**Code paths:**
- `backend/indicators.py` — new `compute_rvol(ohlcv, params)`. Params: `lookback_sessions` (default 20), `aggregator` ("median" | "mean"). Returns `{rvol}`.
- Daily bars: degrade to simple `volume / SMA(volume, N)`.
- `backend/signal_engine.py:11` — add `"rvol"` to `RuleIndicator`.
- Frontend def + rule wiring as above.
- Chart: optional sub-pane row (like volume histogram).

**Lookback constraint:** Needs ≥ 20 prior trading days at the same intraday granularity. yfinance limits 5m/15m/30m to 60 days, 1m to 7 days — workable. Alpaca SIP gives 2+ years of intraday. Document the constraint in the param-validation error.

**Example rules:**
- "Filter: only enter when `RVOL > 1.5`"
- "Signal: `RVOL > 3.0 AND RSI < 30`" (capitulation buy)

**Cost:** 2 days.

### T1.4 — VWMA (Volume-Weighted Moving Average)

**What:** `Σ(close × volume, n) / Σ(volume, n)` over rolling n-bar window. Behaves like an EMA in high-volume periods, like an SMA in low-volume periods — a natural false-signal filter.

**Edge:** modest. Academic backtests show ~equivalence to EMA, sometimes better in trending markets. Including it because it's a one-function add, and it composes well with T1.1–T1.3.

**Code paths:**
- `backend/indicators.py:68` — extend `compute_ma()` to accept `type: "vwma"` OR add new `compute_vwma()`. Either works.
- Frontend: add `vwma` to MA type dropdown.

**Cost:** 0.5 day.

**T1 totals:** ~5–6 days for the full tier. Largest single payoff bundle on the list.

---

## T2 — New visual primitives (visual + weak-mechanical)

These need new rendering machinery beyond line series. Higher build cost; primarily intuition-building tools, partially mechanizable.

### T2.1 — Anchored VWAP (user-picked anchor)

**What:** Same VWAP formula but cumsum starts at a user-picked anchor bar (earnings date, swing high, breakout bar). Visualization-first; mechanizable only if anchor is auto-detected (e.g. anchor at prior-session high, anchor at largest volume bar in window).

**Code paths:**
- `backend/indicators.py` — `compute_anchored_vwap(ohlcv, params)`. Param: `anchor_index` (int) or `anchor_time` (ISO timestamp).
- `frontend/src/features/chart/Chart.tsx` — needs a chart-click affordance to pick the anchor. Right-click → "Anchor VWAP here" context menu, draws line from that bar forward.
- Persistence: anchors saved with the strategy.

**Auto-anchor variants** (mechanizable as rules):
- Anchor at last week's high → tracks AVWAP from last swing.
- Anchor at last day's open → daily AVWAP, conceptually equivalent to session VWAP but session-boundary-snapped.

**Cost:** 3–5 days. UI is the bulk (chart click handler, anchor persistence, multi-anchor UI).

### T2.2 — Volume Profile (POC, VAH, VAL)

**What:** Horizontal histogram of volume traded at each price level over a window. Three variants:
- **Session profile** — resets each trading session.
- **Fixed-range profile** — user-defined window (e.g. last 20 days).
- **Visible-range profile** — recomputed as user pans/zooms.

Key levels:
- **POC** (Point of Control) — price with highest volume.
- **VAH/VAL** (Value Area High/Low) — bounds containing 70% of volume.

**Formula (approximate, from OHLC bars):**
```
price_bins = linspace(min_low, max_high, N)
for each bar:
    bar_range = max(high - low, 1e-9)
    distribute(volume) uniformly across bins overlapping [low, high]
poc = bin with max volume
value_area = expand from POC until cum(volume) ≥ 0.7 × total
```

**Approximation note:** From OHLC bars we don't know the exact trade-volume-at-price; uniform distribution across the bar's range is the standard approximation. Tick-data implementations are exact. Document this caveat clearly in the UI.

**Code paths:**
- `backend/routes/indicators.py` — new endpoint `POST /api/volume_profile/{ticker}` returning `{bins: [{price, volume}], poc, vah, val}`. (Volume profile doesn't fit the per-bar-series shape of `INDICATOR_REGISTRY`; it's a snapshot.)
- `frontend/src/features/chart/Chart.tsx` — new custom canvas overlay on the right edge of the main pane drawing horizontal bars. lightweight-charts v5 doesn't natively support horizontal histograms; needs a sibling absolute-positioned `<canvas>` synced via `chart.priceScale().priceToCoordinate()`.

**Mechanizing as rules** — requires exposing POC/VAH/VAL as per-bar series:
- For session profile: POC at each bar = POC of the session up to that bar. Trivially serial.
- Rules: `PRICE crosses_above prior_session_VAH`, `PRICE within ±0.5% of POC`.

**Cost:** 1–2 wks. Custom canvas overlay + sync logic is the hard part. Rule-side exposure is another 2–3 days.

### T2.3 — Liquidity heatmap (volume-by-time)

**What:** Side panel showing volume bucketed by (day-of-week × time-of-day), highlighting when the symbol typically trades heavy. Pure visualization, not a rule input.

**Cost:** 2–3 days. Backend-light (aggregate query), frontend rendering is a standard heatmap.

**T2 totals:** ~2.5–3 wks for full tier.

---

## T3 — Cross-sectional liquidity filters (regime / screener)

These work at the **portfolio-level** rather than per-bar — useful for the Discovery tab and as universe filters.

### T3.1 — Amihud illiquidity

**Formula:** `Amihud_t = |return_t| / dollar_volume_t`, smoothed over 20-day rolling mean.

**Use:**
- **Screen-out** illiquid names from Discovery candidate list.
- **Regime indicator** — rising Amihud across the watchlist = stress, mean-reversion strategies tend to fail.

**Academic pedigree:** Amihud 2002 (JFM, 8000+ citations). Strong.

**Code paths:**
- `backend/indicators.py` — new `compute_amihud(ohlcv, params)` returning `{amihud, amihud_rolling_20d}`.
- `backend/routes/discovery.py` (assuming it exists; grep first) — add Amihud filter to candidate screening.
- Frontend Discovery panel — add "max Amihud" slider.

**Cost:** 2 days.

### T3.2 — Dollar-volume regime

**What:** `close × volume`, smoothed. Use existing **regime** infrastructure (`backend/regime.py`) — feed dollar-volume as a regime indicator on 1d/1wk HTF. Trend-follow when regime is "high dollar volume rising", mean-revert when "low / falling".

**Code paths:**
- `backend/indicators.py` — extend `compute_volume()` to return `dollar_volume` series, OR add `compute_dollar_volume()`.
- Plug into existing regime rule pipeline.

**Cost:** 1 day.

**T3 totals:** ~3 days. Smallest tier, decent value as a Discovery filter.

---

## T4 — Order-flow analytics (high-value, high-build)

**These require a tick-data ingestion pipeline that doesn't exist today.**

### T4.1 — Cumulative Volume Delta (CVD)

**Formula:** `CVD = cumsum(ask_volume - bid_volume)` where each trade is classified by aggressor side.

**Aggressor classification:** ⚠️ **No retail feed (yfinance / Alpaca SIP / Alpaca IEX / IBKR) carries a native aggressor flag on individual trade records.** Must be inferred by combining the trades stream with contemporaneous NBBO quotes:
- Trade price ≥ ask → aggressor = buy
- Trade price ≤ bid → aggressor = sell
- Inside the spread → Lee-Ready rule (tick test).

Implication: every CVD computation needs **both** trades and quotes at sub-second resolution, doubling the storage footprint and adding a quote-snapshot lookup per trade. Alpaca SIP provides both (`Trades API` + `Quotes API`, 7+ years). IBKR provides both via `reqHistoricalTicks(whatToShow=ALL_LAST)` + `whatToShow=BID_ASK`, but BID_ASK requests count double against pacing limits — bulk historical CVD is painful.

**Signal:** CVD/price divergence is the primary signal — price makes new high but CVD doesn't → distribution.

**Data infra cost (the real cost):**
- New tick-data fetch + cache layer parallel to `_fetch()`. Different shape (per-trade rows, not OHLCV bars).
- Storage: ~100k–500k trades/symbol/day for liquid names. Parquet on disk; ~50 MB/symbol/year.
- Aggressor-side computation needs paired NBBO quotes; storing both doubles the data footprint.
- New ingestion endpoint, new TTL strategy, new cache eviction rules.

**Code paths (new):**
- `backend/tick_provider.py` — new abstraction parallel to `TradingProvider`.
- `backend/alpaca_tick.py` — Alpaca SIP Trades + Quotes ingestion + on-disk parquet cache.
- `backend/ibkr_tick.py` — `reqHistoricalTicks` ingestion. Slower (rate-limited).
- `backend/cvd.py` — aggressor classification + cumulative.
- New rule indicator `cvd` with `divergence_lookback` param. Condition: `cvd_divergence_bearish` (new condition type).
- Sub-pane in Chart.tsx for CVD line.

**Cost:** 3–4 weeks. The data infrastructure is most of it. Make this a multi-stage plan: (a) tick ingestion only, (b) aggressor classification, (c) CVD compute + display, (d) rule integration.

### T4.2 — Footprint / order flow charts

**What:** Per-bar histogram showing bid-volume vs ask-volume at each price within the bar. Visualization-first; no published systematic strategy derives clean alpha from raw footprint columns. Used for discretionary tape-reading.

**Verdict for SL:** Skip unless the user explicitly wants the visualization. Build effort is comparable to T4.1, with weaker mechanizability.

**Cost:** 2 weeks on top of the T4.1 tick pipeline.

### T4.3 — Bid-ask spread history

**What:** Quote-time-weighted average spread per bar. Useful as a true liquidity measure and as cost-model input (drop-in replacement for the empirical slippage estimator in `backend/slippage.py`).

**Cost:** 1 week on top of the T4.1 quote pipeline (the quote storage is the precondition).

**T4 totals:** **3–6 wks**. The decision is whether the tick-data infra is worth building. Recommendation: gate on T1 results — if liquidity-naïve strategies show clear edge after T1 ships, justify T4 as the precision upgrade. Otherwise defer.

---

## T5 — Microstructure academia (low retail value)

### T5.1 — Roll's spread estimator

**Formula:** `spread = 2 × √(-Cov(ΔP_t, ΔP_{t-1}))` over rolling window.

**Limitation:** returns NaN whenever the autocovariance is positive (regularly happens in trending / news regimes). Brittle.

**Cost:** 0.5 day. **Recommendation: skip** unless you want a fallback when bid-ask history is unavailable.

### T5.2 — Kyle's lambda

**Formula:** OLS regression of `ΔP` on signed trade volume.

**Limitation:** requires aggressor-classified trades — same data dependency as T4.1, weaker payoff than Amihud for retail use.

**Cost:** 1 week (after T4.1). **Recommendation: skip** for retail.

---

## Cross-cutting concerns

### Rule engine extensions needed

T1 indicators all work with existing `above / below / crosses_above / crosses_below` conditions. No new conditions needed.

T2.2 (volume profile) and T4.1 (CVD) introduce new condition shapes:
- `PRICE within ±X% of POC` — new condition `near` or reuse `above/below` with explicit upper/lower bounds via two rules.
- `CVD divergence over N bars` — new condition `divergence_bearish` / `divergence_bullish`. Reasonable to add once.

### Frontend rule-editor UI

Need to design how multi-output indicators (VWAP returns `vwap`, `upper`, `lower`; prior_session returns `pdh`, `pdl`, etc) surface in the `param` dropdown. Existing pattern (MACD `signal` param, BB `upper`/`lower` param) extends naturally.

### Chart overlay rendering

T1.1–T1.3 reuse existing line-series machinery in `Chart.tsx`. T2.1 needs anchor-picking UI. T2.2 needs a sibling canvas overlay synced to the price scale. T4.x need a new sub-pane row.

### Data-feed feasibility per indicator

| Indicator | yfinance | Alpaca IEX | Alpaca SIP | IBKR |
|-----------|----------|------------|------------|------|
| Session VWAP | ✅ | ✅ | ✅ | ✅ |
| Prior session H/L | ✅ | ✅ | ✅ | ✅ |
| RVOL same-TOD | ✅ (≤ 60d intraday) | ✅ (narrow coverage) | ✅ | ✅ |
| VWMA | ✅ | ✅ | ✅ | ✅ |
| Anchored VWAP | ✅ | ✅ | ✅ | ✅ |
| Volume Profile (bar-approx) | ✅ | ✅ | ✅ | ✅ |
| Volume Profile (tick-exact) | ❌ | ⚠️ IEX-only ticks | ✅ | ✅ |
| Amihud / dollar-volume | ✅ | ✅ | ✅ | ✅ |
| CVD (aggressor inferred from trades+quotes) | ❌ | ⚠️ IEX-only volume, unreliable | ✅ (paired trades+quotes, 7+yr) | ⚠️ (bulk download slow — pacing limits) |
| Footprint (needs ticks + aggressor) | ❌ | ❌ | ⚠️ derivable via inference | ⚠️ derivable, painful |
| Bid-ask spread history | ❌ | ❌ (IEX quotes not NBBO) | ✅ (Quotes API, 7+yr NBBO) | ✅ (BID_ASK ticks, pacing-limited) |

### Performance budget

- **Per-bar indicators (T1)** — drop straight into the existing cache. Compute cost negligible (<5ms on a 60d×5m series).
- **Volume profile (T2.2)** — O(bars × bins). 60d × 1m bars × 200 bins ≈ 5M ops. <50ms. Render is the concern, not compute.
- **CVD (T4.1)** — O(trades). 100k–500k trades/symbol/day. Per-symbol-day compute ~1–2s; cache aggressively.

### Test plan stub

For T1, follow the existing pattern (`tests/test_signal_engine.py`):
- Unit tests per `compute_X` for warmup, NaN handling, session boundary correctness.
- Rule-eval tests for `crosses_above VWAP`, `above PDH`, `RVOL > 1.5`.
- Property tests: session VWAP at session-end > current bar VWAP iff price > VWAP; PDH for bar B = max(high) over prior calendar day.
- Integration test: a deliberately VWAP-anchored fake series produces the rule fires we'd predict.

T2.2 needs a property test: profile sums to total volume; POC is the argmax bin; value area spans exactly 70%.

T4.x: aggressor classification correctness test against a hand-built fixture of trades+quotes.

### Migration / rollout

- T1 features layer on cleanly with no migration. New indicators are additive.
- Existing `compute_vwap()` is naive cumulative; if anything depends on that behavior in saved strategies, the fix breaks them. **Action:** grep `bots.json` and `saved-strategies` localStorage entries for `"vwap"` references before merging T1.1. Add a feature flag `vwap_session_reset` if needed for compatibility.

---

## Recommended sequence

**Wave 1 (1 week)** — T1.1 Session VWAP + bands, T1.2 Prior session H/L (day window only), T1.4 VWMA. Lowest cost, highest signal per line.

**Wave 2 (1 week)** — T1.3 RVOL same-TOD, T1.2 weekly/monthly variants, T3.1 Amihud, T3.2 Dollar-volume regime. Composes with Wave 1.

**Wave 3 (2 wks)** — T2.2 Volume profile (session + fixed-range), with mechanical POC/VAH/VAL rule exposure. T2.1 Anchored VWAP if time.

**Decision point** — Evaluate WFA OOS Sharpe of liquidity-confirmed variants of your saved strategies vs. baselines. If clear edge: build T4. If marginal: defer T4 and accept that retail-feasible liquidity tooling stops at T2/T3.

**Wave 4 (3–6 wks)** — T4.1 Tick + aggressor pipeline + CVD. Multi-stage; revisit scope after T1–T3 results.

**Skip** — T2.3 (heatmap, low value), T4.2 (footprint, no systematic signal), T5 (academic, retail-noise).

---

## Open questions

1. **Daily-bar VWAP semantics** — what does "session VWAP" mean when base interval is `1d`? Options: (a) anchor at start of date range, (b) anchor weekly, (c) hide the indicator on daily bars and show error. Recommend (c) with clear UI message.
2. **Multi-anchor AVWAP** — should we support multiple simultaneous anchored VWAPs on the same chart? (Most platforms allow it.) Affects UI persistence.
3. **Volume profile session vs. fixed-range default** — which variant should be the default in the UI? Session is more common but fixed-range is more useful for swing traders. Probably both, with a tab/toggle.
4. **Tick storage budget** — if T4 is greenlit, where does parquet live? `backend/data/ticks/` mirroring `backend/data/bots.json` pattern. Disk usage cap needed (TTL on stored ticks?).
5. **Should RVOL drive optimizer/sweep parameter paths?** RVOL filter threshold is a natural parameter to sweep — confirm the optimizer machinery (`routes/optimize.py`) doesn't choke on indicator-rule-threshold params.

---

## Related research

- Practitioner survey (2026-05-17) — full literature review across 16 indicator classes. Key takeaways: VWAP + PDH/PDL + RVOL + Amihud are the four with strongest combined practitioner + academic backing for retail. OBV, MFI, VZO are cargo-cult; SMC/liquidity-pool concepts are dressed-up swing high/low.
- See also: `docs/specs/2026-05-08-configurable-poll-interval-design.md` for prior spec format.
