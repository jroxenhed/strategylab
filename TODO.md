# StrategyLab TODO

Themed roadmap. Each section lists active work first, then a **Shipped** block preserving history.

## Charts & Indicators

- [ ] Portfolio equity chart (combined P&L across bots)
- [ ] Equity curve macro mode for long timescales / thousands of trades
- [ ] Equity curve trend analysis (open-ended — define "trend" first)
- [ ] More indicators — ATR, Stochastic, VWAP (chart display + backtest rules)
- [ ] Chart timeframe buttons — 1W / 1M / 3M / 1Y quick selectors
- [ ] Watchlist — save/switch between tickers quickly

### Shipped
- [x] Backtest equity curve: baseline (buy & hold) overlay toggle

## Strategy Engine & Rules

- [ ] Skip N trades after SL (+ dynamic sizing scale-back to 100% after N trades; open question: unify into one setting?)
- [ ] Pre-market / extended hours option
- [ ] More strategy rules — expand rule engine conditions
- [ ] Borrow cost estimation (for live short positions on real accounts)

### Shipped
- [x] Implement shorting — direction field, backtest + bot runner, chart markers, bot card refresh

## Strategy Summary & Analytics

- [ ] Expected value / trade + profit factor — fixes "avg loss > avg win looks like losing" misread; waterfall or inline number beside distribution histogram

### Shipped
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
