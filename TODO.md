# StrategyLab TODO

## Critical (live trading accuracy)
- [ ] Verify allocation logic (AAPL $1000 vs $10000 buys)
- [ ] Verify SL fill detection works in practice
- [ ] Does algo wait for candle close before buy/sell?
- [ ] Does buying amount grow with P&L or stay at allocated? (currently static)

## Important (UX/correctness)
- [ ] Bot log timezone: shows ET, user is in Sweden — use browser local time
- [ ] Refresh button on journal
- [ ] Increase update frequency + display interval on bot card
- [ ] Calculate actual slippage/commissions on paper trades

## Features
- [ ] Position size: default 100%, hide slider
- [ ] Show effective trade size on bot card when < 100%
- [ ] Manual buy on bot to start a position
- [ ] Make allocation, position size, strategy editable in-place on bot card
- [ ] Pre-market / extended hours option
- [ ] Portfolio equity chart (combined P&L across bots)
- [ ] Clean up bot page, move signal scanner to new page

## Architecture
- [ ] Implement shorting + borrow cost estimation
- [ ] Multiple bots same ticker (long/short/different TFs), grouping
- [ ] Rename "Paper Trading" to something cool
