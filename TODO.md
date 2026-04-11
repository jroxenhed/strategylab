# StrategyLab TODO

## Critical (live trading accuracy)
- [x] Verify allocation logic — was position_size=10.0, added validator to clamp 0.01–1.0
- [x] Verify SL fill detection — code verified, will confirm on next live SL trigger
- [x] Does algo wait for candle close? — No, and that's fine. OTO SL is server-side (instant), trailing stop benefits from frequent checks
- [x] Buying amount now compounds P&L (allocated_capital + total_pnl), matching backtest behavior

## Important (UX/correctness)
- [ ] Bot log timezone: shows ET, user is in Sweden — use browser local time
- [x] Refresh button on journal
- [x] Increase UI update frequency (5s → 2s for bot list + detail polling)
- [x] Track actual slippage — poll Alpaca fill price, log expected vs actual, show in journal

## Features
- [x] Position size: removed slider, hardcoded to 100%
- [x] Manual buy on bot to start a position
- [x] Make allocation and strategy editable in-place on bot card (click when stopped)
- [ ] Pre-market / extended hours option
- [ ] Portfolio equity chart (combined P&L across bots)
- [ ] Clean up bot page, move signal scanner to new page

## Bot P&L ground-truth refactor (follow-ups)
- [ ] Bot lifecycle vs journal: decide whether deleting/recreating a bot should reset its displayed P&L (filter journal by `bot.created_at`)
- [ ] Clean up dead `BotState.total_pnl` field once migration is safe (currently kept for legacy `bots.json` deserialization)
- [ ] Journal helper call frequency — runs on bot tick (for sizing) and on summary fetch. Journal is JSON-parsed each time. Fine for now, but if it gets slow (thousands of entries) add an mtime-based cache.
- [ ] Bot card sparkline scale — currently local-per-card (each card fits its own first→last trade window). Consider a global/aligned x-axis so cards share a common time window and you can scan timing across bots at a glance. Tradeoff: alignment helps cross-bot coordination but squashes bots with few or recent-only trades. Revisit once the portfolio equity chart exists.

## Architecture
- [x] Implement shorting — direction field, backtest + bot runner, chart markers, bot card refresh
- [x] Structural refactoring — extract models/journal/bot_runner, split BotControlCenter, shared utils, centralize API client
- [ ] Borrow cost estimation (for live short positions on real accounts)
- [ ] Multiple bots same ticker — grouping UI (long+short pairs run fine, visual grouping deferred)
- [ ] Rename "Paper Trading" to something cool

## Planned Features
- [ ] More indicators — ATR, Stochastic, VWAP (chart display + backtest rules)
- [ ] More strategy rules — expand rule engine conditions
- [ ] Chart timeframe buttons — 1W / 1M / 3M / 1Y quick selectors
- [ ] Watchlist — save/switch between tickers quickly
