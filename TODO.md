# StrategyLab TODO

Themed roadmap. Each section lists active work first, then a **Shipped** block preserving history.

## Charts & Indicators

- [ ] Portfolio equity chart (combined P&L across bots)
- [ ] Equity curve macro mode for long timescales / thousands of trades
- [ ] Equity curve trend analysis (open-ended — define "trend" first)
- [ ] MA8 / MA21 indicators with SMA/EMA/RMA type selector, toggleable in sidebar alongside existing EMAs
- [ ] S-G Smoothed MA8 — savgol_filter on MA8 values (inherits MA type), separate toggleable chart line (lighter/dashed), configurable window length + polynomial order; always visible when on (not just during backtest). Requires scipy.
- [ ] More indicators — ATR, Stochastic, VWAP (chart display + backtest rules)
- [ ] Chart timeframe buttons — 1W / 1M / 3M / 1Y quick selectors
- [ ] Watchlist — save/switch between tickers quickly

### Shipped
- [x] Backtest equity curve: baseline (buy & hold) overlay toggle

## Strategy Engine & Rules

- [ ] Skip N trades after SL (+ dynamic sizing scale-back to 100% after N trades; open question: unify into one setting?)
- [ ] Pre-market / extended hours option
- [ ] New rule conditions: "MA21 turns up" (slope neg→pos), "MA8 turns down" (slope pos→neg), "MA8 decelerating" (S-G smoothed first derivative decreasing, i.e. second derivative negative; uses same S-G params as chart indicator). Target strategy: BUY when MA21 turns up AND NOT MA8 decelerating, SELL when MA8 turns down. Ensure crypto tickers work (BTC-USD via yfinance), test against BTC 2h.
- [ ] Per-rule signal visualization toggles — eye icon on each rule row in strategy builder; when enabled, that rule's signals show as markers on the main chart during/after backtest. Replaces current hardcoded signal marker behavior. State stored with rule fields, persists with save/load. No global master toggle.
- [ ] Borrow cost estimation (for live short positions on real accounts)

### Shipped
- [x] Implement shorting — direction field, backtest + bot runner, chart markers, bot card refresh

## Strategy Summary & Analytics

- [ ] Merge stat column into waterfall (replace left min/avg/max column with inline min↔max range indicator under each waterfall row)
- [ ] Show B&H as alpha (single "Alpha vs B&H" metric instead of parallel Return / B&H Return)
- [ ] Sharpe orange band for 0.5–1; dampen Max DD color when <10%
- [ ] Histogram zero line + min/max tick labels

### Shipped
- [x] Summary readability pass — dropped `(mean)` suffix on EV/PF, renamed Max/Min gain/loss → Biggest/Smallest win/loss, added size hierarchy to top metrics row (Return + Final Value 22px primary, rest 13px secondary), removed median secondary values from avg rows
- [x] Expected value / trade + profit factor — EV + PF headline numbers, 3-row decomposition waterfall (Wins / Losses / Net) inline with StatRows + histogram, mean/median toggle dropped in favor of inline dual values
- [x] Strategy summary: min/max/avg gain and loss

## Bots (live trading)

- [ ] Bot log timezone: shows ET, user is in Sweden — use browser local time
- [ ] Bot reordering/grouping (drag vs explicit groups vs tags)
- [ ] Bot lifecycle vs journal: decide whether deleting/recreating a bot should reset its displayed P&L (filter journal by `bot.created_at`)
- [ ] Clean up dead `BotState.total_pnl` field once migration is safe (currently kept for legacy `bots.json` deserialization)
- [ ] Journal helper call frequency — runs on bot tick (for sizing) and on summary fetch. Journal is JSON-parsed each time. Fine for now, but if it gets slow (thousands of entries) add an mtime-based cache.

### Shipped
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

## Discovery (research project)

Own multi-session research project. Needs its own design work before implementation.

- [ ] Scan for good StrategyLab candidates (criteria TBD)
- [ ] Batch backtesting (efficiency-critical)
- [ ] AI/ML assisted parameter tweaking
- [ ] Pipeline: present candidates → spawn bot army

### Shipped
- [x] Clean up bot page, move signal scanner to new Discovery page

## Architecture & housekeeping

- [ ] Rename "Paper Trading" to something cool

### Shipped
- [x] Structural refactoring — extract models/journal/bot_runner, split BotControlCenter, shared utils, centralize API client
