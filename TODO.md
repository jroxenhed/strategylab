# StrategyLab TODO

Themed roadmap. Each section lists active work first, then a **Shipped** block preserving history. Items are indexed **Section Letter + Number** (e.g. B3) for easy reference.

| Section | Topic |
|---------|-------|
| **A** | Charts & Indicators |
| **B** | Strategy Engine & Rules |
| **C** | Strategy Summary & Analytics |
| **D** | Bots (live trading) |
| **E** | Discovery |
| **F** | Architecture & housekeeping |

---

## A — Charts & Indicators

- [ ] **A1** Portfolio equity chart (combined P&L across bots)
- [ ] **A3** Equity curve trend analysis (open-ended — define "trend" first)
- [ ] **A4** More indicators — ATR, Stochastic, VWAP (chart display + backtest rules)
- [ ] **A6** Watchlist — save/switch between tickers quickly

### Shipped
- [x] Equity curve macro mode for long timescales / thousands of trades — resampled equity chart (D/W/M/Q/Y) via `MacroEquityChart.tsx` + `/api/backtest/macro`
- [x] Date range presets (D/W/M/Q/Y) + period stepping arrows (‹ › single, « » 5x skip)
- [x] Equity curve: normalised B&H comparison toggle + log scale toggle
- [x] MA8 / MA21 with SMA/EMA/RMA type selector + S-G smoothed variants (independent window/poly per MA, raw curve toggles, dashed S-G lines)
- [x] Backtest equity curve: baseline (buy & hold) overlay toggle

## B — Strategy Engine & Rules

- [ ] **B1** Skip N trades after SL (+ dynamic sizing scale-back to 100% after N trades; open question: unify into one setting?)
- [ ] **B2** Pre-market / extended hours option
- [ ] **B3** New rule conditions: "MA21 turns up" (slope neg→pos), "MA8 turns down" (slope pos→neg), "MA8 decelerating" (S-G smoothed first derivative decreasing, i.e. second derivative negative; uses same S-G params as chart indicator). Target strategy: BUY when MA21 turns up AND NOT MA8 decelerating, SELL when MA8 turns down. Ensure crypto tickers work (BTC-USD via yfinance), test against BTC 2h.
- [ ] **B4** Per-rule signal visualization toggles — eye icon on each rule row in strategy builder; when enabled, that rule's signals show as markers on the main chart during/after backtest. Replaces current hardcoded signal marker behavior. State stored with rule fields, persists with save/load. No global master toggle.
- [ ] **B5** Borrow cost estimation (for live short positions on real accounts)
- [ ] Cost model v2 (deferred from B6):
  - Debit-balance-aware margin interest for shorts (charge margin rate only on days net cash is negative)
  - IBKR Tiered pricing (exchange fees, SEC fee, FINRA TAF, clearing pass-throughs)
  - Hard-to-borrow dynamic rate feed
  - FX conversion cost

### Shipped
- [x] Realistic cost model in backtester — IBKR Fixed per-share commission (`per_share_rate` + `min_per_order`), empirical per-symbol slippage via `GET /api/slippage/{symbol}` + `useEmpiricalSlippage` hook, short borrow cost (`borrow_rate_annual`). Results shows Borrow column + Cost Breakdown block.
- [x] Implement shorting — direction field, backtest + bot runner, chart markers, bot card refresh

## C — Strategy Summary & Analytics

- [ ] **C1** Merge stat column into waterfall (replace left min/avg/max column with inline min↔max range indicator under each waterfall row)
- [ ] **C2** Show B&H as alpha (single "Alpha vs B&H" metric instead of parallel Return / B&H Return)
- [ ] **C3** Sharpe orange band for 0.5–1; dampen Max DD color when <10%
- [ ] **C4** Histogram zero line + min/max tick labels

### Shipped
- [x] Summary readability pass — dropped `(mean)` suffix on EV/PF, renamed Max/Min gain/loss → Biggest/Smallest win/loss, added size hierarchy to top metrics row (Return + Final Value 22px primary, rest 13px secondary), removed median secondary values from avg rows
- [x] Expected value / trade + profit factor — EV + PF headline numbers, 3-row decomposition waterfall (Wins / Losses / Net) inline with StatRows + histogram, mean/median toggle dropped in favor of inline dual values
- [x] Strategy summary: min/max/avg gain and loss

## D — Bots (live trading)

- [ ] **D1** Bot log timezone: shows ET, user is in Sweden — use browser local time
- [ ] **D2** Bot reordering/grouping (drag vs explicit groups vs tags)
- [ ] **D3** Bot lifecycle vs journal: decide whether deleting/recreating a bot should reset its displayed P&L (filter journal by `bot.created_at`)
- [ ] **D4** Clean up dead `BotState.total_pnl` field once migration is safe (currently kept for legacy `bots.json` deserialization)
- [ ] **D5** Journal helper call frequency — runs on bot tick (for sizing) and on summary fetch. Journal is JSON-parsed each time. Fine for now, but if it gets slow (thousands of entries) add an mtime-based cache.
- [ ] **D6** Active IBKR broker heartbeat — background task pings `reqCurrentTimeAsync` every ~30s, stores `last_broker_ok` timestamp, exposes via `/api/broker` so UI can show a colored dot next to the "via IBKR" badge. Complements the passive reconnect-on-stale-session already in `IBKRTradingProvider._ensure_connected` by giving pre-click visibility of connection health.

### Shipped
- [x] IBKR broker integration — full data + trading provider via `ib_insync`. TradingProvider protocol abstracts Alpaca/IBKR behind unified interface. Global broker selector (data source stays per-request). Enables simultaneous long+short on same symbol. [spec](docs/superpowers/specs/2026-04-13-ibkr-broker-integration-design.md)
- [x] Paper trading polish — Journal: reason colors fixed, Expected/Gain% columns, summary row, filter relocated, auto-refresh 5s, CSV export. Bot cards: heartbeat dot. Positions: 5s poll, Opened/Side columns.
- [x] Verify allocation logic — was position_size=10.0, added validator to clamp 0.01–1.0
- [x] Verify SL fill detection — code verified, will confirm on next live SL trigger
- [x] Does algo wait for candle close? — No, and that's fine. OTO SL is server-side (instant), trailing stop benefits from frequent checks
- [x] Buying amount compounds P&L (allocated_capital + total_pnl), matching backtest behavior
- [x] Refresh button on journal
- [x] Increase UI update frequency (5s → 2s for bot list + detail polling)
- [x] Track actual slippage — poll Alpaca fill price, log expected vs actual, show in journal
- [x] Position size: removed slider, hardcoded to 100%
- [x] Manual buy on bot to start a position
- [x] Make allocation and strategy editable in-place on bot card (click when stopped)
- [x] Global start/stop all bots
- [x] Bot sparkline: global toggle for local vs aligned timescale

## E — Discovery

Own multi-session research project. Needs its own design work before implementation.

- [ ] **E1** Scan for good StrategyLab candidates (criteria TBD)
- [ ] **E2** Batch backtesting (efficiency-critical)
- [ ] **E3** AI/ML assisted parameter tweaking
- [ ] **E4** Pipeline: present candidates → spawn bot army

### Shipped
- [x] Clean up bot page, move signal scanner to new Discovery page

## F — Architecture & housekeeping

- [ ] **F1** Rename "Paper Trading" to something cool

### Shipped
- [x] Structural refactoring — extract models/journal/bot_runner, split BotControlCenter, shared utils, centralize API client
