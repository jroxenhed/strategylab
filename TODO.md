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
- [ ] Make allocation, position size, strategy editable in-place on bot card
- [ ] Pre-market / extended hours option
- [ ] Portfolio equity chart (combined P&L across bots)
- [ ] Clean up bot page, move signal scanner to new page

## Architecture
- [ ] Implement shorting + borrow cost estimation
- [ ] Multiple bots same ticker (long/short/different TFs), grouping
- [ ] Rename "Paper Trading" to something cool
