# Session Post-Mortem: 2026-05-13 KO RSI(2) Mean-Reversion Demo

**Outcome:** End-to-end app demo from ticker pick → backtest → optimization → Monte Carlo → bot deployment attempt. **Bot was NOT deployed** — strategy fails on the only timeframe bots support. Two new TODOs filed (F187, F188).
**Duration:** Single interactive session, full chrome-devtools-mcp driven.
**Pattern:** Claude drives the entire UI; user observes and redirects. No code shipped.

---

## The brief

> "Choose a ticker, create a mean reversion strategy that you think could work on some liquid ticker (not AAPL this time). Then run backtests, look at the results, look at Monte Carlo, Rolling, Session, etc. run optimizer/WFA. Tweak and repeat until you have a strategy good enough to deploy a bot."

User constraints learned in flight:
- Alpaca data available (switched from default Yahoo)
- ~$10k capital, per-trade costs are first-order
- Don't deploy something that won't actually work

## The hypothesis

**Ticker: KO (Coca-Cola).** Large-cap consumer staple, very liquid (penny spreads), defensive, historically range-bound for stretches. Reasonable mean-reversion candidate on a non-AAPL liquid name.

**Strategy: Connors RSI(2) mean-reversion variant.** Buy when 2-period RSI is deeply oversold (< 10), sell when it pops back above 60. Classic Larry Connors setup with documented edge on dailies.

**Timeframe:** Daily on Alpaca SIP (later forced to IEX feed when SIP returned no data for KO — likely auth/subscription issue worth investigating separately).

## Iteration trail

| v | Config | Return | Sharpe | Max DD | Trades | vs B&H | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | RSI(2) <10 / >70, no regime, no stops | +26.1% | 0.37 | -18.4% | 100 | -39% | Decent diversifier |
| 2 | RSI(2) <10 / >50, 3% stop loss | +14.3% | 0.27 | -22% | 172 | -51% | Worse — looser SELL = more trades + costs |
| 3 | v2 + regime filter (MA200 above) | -4.9% | -0.06 | -22% | 98 | -70% | Stop loss kills mean reversion |
| 4 | RSI(2) <10 / >60, regime, no SL, 5-bar time stop | +13.1% | 0.31 | -18% | 108 | -52% | Time stop hurts win rate |
| **5** | **RSI(2) <10 / >60, no regime, no stops, no time stop** | **+33.5%** | **0.46** | **-17.6%** | **139** | -32% | **Best Sharpe** |
| B&H | KO 2020–2026 | +65.2% | — | — | 0 | — | Strategy fundamentally underperforms B&H |

**5 iterations distilled into 3 lessons:**

1. **Stop losses destroy mean reversion.** Connors RSI(2) specifically AVOIDS stops because RSI(2) buys at *extreme* oversold — additional move down is noise, and a 3% stop locks in losses on trades that would have reverted within 2-3 days. v3's -4.9% return with the stop loss vs v1's +26% without it is the receipt.
2. **The regime filter didn't actually filter much.** KO was above its 200-day MA for >95% of 2020-2026 (strong uptrend). Regime cut only 2 of 100 trades; the entire "insurance" was nearly inert during the test window. Would matter in a bear market.
3. **Mean reversion fights uptrends structurally.** Every variant trailed B&H. Even the best (v5) underperformed +33.5% vs +65.2% B&H. The strategy's value is *diversification* (beta 0.24, low R² to SPY, positive Sharpe in isolation), not absolute alpha.

## Monte Carlo robustness (v5)

1,000 simulations over the trade-sequence permutations:
- **Ruin probability: 0.0%**
- Worst case min equity: $7.5k (-25%)
- Median worst drawdown: -19%
- Best-5% drawdown: -12%
- Worst-5% drawdown: -30%

Genuinely robust at the strategy-distribution level. Not curve-fit to a single lucky path.

## The 1-hour collapse

Bot infrastructure constraint: live bots only support intraday intervals (1m, 5m, 15m, 30m, 1h). Hardcoded in `frontend/src/features/trading/AddBotBar.tsx:8`. Daily strategies can be backtested but not deployed.

Re-tested v5 on 1h bars to validate the intraday version:

| Metric | Daily | 1-Hour |
|---|---|---|
| Return | +33.5% | +5.04% |
| Sharpe | **0.46** | 0.053 |
| Trades | 139 | **824** |
| Cost drag | 6.5% | **34.5%** |
| EV/trade | $107.87 | $0.61 |
| Kelly | 9% | 1.1% |

824 round-trips × ~4 bps slippage = $3,451 in costs on $10k capital. The mean-reversion edge per trade ($0.61) is annihilated by transaction costs at 1h frequency. The strategy only works on daily timeframe where costs amortize across multi-day holds.

## Decision

**Did not deploy the bot.** Shipping a known-losing strategy would have been a worse outcome than the demo "failing." Failure is also a result.

Saved the strategy to localStorage as "KO RSI2 Reversion" so the work isn't lost — anyone can re-load it, eyeball the daily backtest, and decide what to do.

## Bugs / constraints surfaced

### F187 — Optimizer param-substitution is broken for non-slippage params

Ran a 16-combination sweep of (buy threshold 5-20, sell threshold 50-80). Every single combination returned **identical** metrics: Sharpe 0.309, 108 trades, -17.99% DD. User confirmed this is a known pattern — only `slippage_bps` sweeps actually vary the backtest.

Likely cause: the optimizer route's param-path resolver doesn't traverse into the rules array. Top-level fields like `slippage_bps` get overridden; nested paths like `buy_rules[0].value` and `buy_rules[0].params.period` silently pass through without substitution.

**The Optimizer is effectively unusable for any non-slippage parameter today.** This makes a core feature of the app misleading — users will think they've found "the best params" when actually they got 16 copies of their starting config.

### F188 — Bots are intraday-only; daily strategies can't deploy

Architectural gap. The bot polling loop assumes intraday cadence — there's no daily/EOD scheduler. Most documented edges (Connors RSI(2), MACD crossovers on daily, regime trading) live on daily timeframes where bots can't reach.

Two paths forward:
- **(a) Architecture:** add daily/EOD scheduling to bot_runner. Real work — needs cron-like daily tick instead of polling loop. Probably 1-2 days of careful work.
- **(b) UX:** add a clear "Daily strategies — backtest only, not deployable as bot" warning in the strategy save flow. 10-minute fix that closes the discoverability gap immediately.

(a) is the right long-term answer. (b) is what I'd ship next session to stop the demo trap.

## What worked in the demo workflow

- **chrome-devtools-mcp + the live-browser verification protocol** (CLAUDE.md, added earlier today) carried the entire demo. Every state change was inspected, every metric was read directly from the DOM, every UI transition was verified visually before continuing.
- **Reading metrics via `document.body.innerText` + regex** turned out to be more reliable than relying on a11y snapshots, which exceeded token limits multiple times. For numbers-on-a-dashboard the regex approach is the right tool.
- **React fiber `onBlur` trick** (also from CLAUDE.md, "Known traps" section) for committing controlled inputs that don't respond to `dispatchEvent`. Used 4+ times in the session.

## What I'd do differently next time

1. **Confirm the deployment constraint BEFORE iterating.** I spent 5 iterations tuning on daily bars without first checking the bot interval support. The whole demo could have been on 1h from the start, which would have shifted strategy choice (Connors RSI(2) is wrong for 1h; would have picked something with longer hold times).
2. **Pick a ticker matched to the strategy, not to the demo narrative.** KO trended +65% over the test window, which is the worst environment for mean reversion. Range-bound names (XLU, TLT) or volatile names with whipsaw (INTC, KHC) would have been a fairer test. Picked KO for "demo storytelling" reasons rather than strategy fit.
3. **Don't trust the optimizer until F187 is fixed.** I ran the optimizer and treated the "identical-metrics" result as a strategy-sensitivity finding ("the rules aren't sensitive to thresholds"). It was actually a binding bug. Lost ~10 minutes chasing the wrong rabbit hole. Now we know.
4. **Set the date range BEFORE the first backtest.** The default 1-year window (Jan→Dec 2026, partly future) produced 2 trades and a 100% win rate — useless. Bumped to 2020-2026 for 6 years of data. Should have done that first.
5. **The "Strategy good enough to deploy" goal needs a precondition check.** Specifically: does the bot support this strategy's timeframe? If not, no amount of optimization helps. F188 is the structural fix; until then, the demo prompt should include "and confirm it's bot-deployable" as a first step, not the last.

## Demo workflow validation

The user's earlier framing: *"this workflow is so cool... it's what I imagined it COULD be when I first started vibe coding."* This session was the test of that claim under failure conditions.

Verdict: **the workflow held up.** I drove the entire app for ~45 minutes via chrome-devtools-mcp, made 5 strategic decisions visible to the user, hit two real bugs (one a previously-noticed-by-user pattern, one a structural constraint), and chose the disciplined "don't deploy" outcome over the demo-narrative "let's ship it" temptation. The user observed without needing to intervene on tool use.

The honest "failure is a result" outcome would be unreachable from a "make the demo look good" prompt. The autonomous loop produced a more useful answer than a scripted demo would have.

## Related work
- Earlier in same session: Live-Browser UI Verification protocol added to CLAUDE.md
- Filed today: F185 (loadSavedStrategy crashes on partial fixtures), F186 (F160 abort-on-resubmit dead code), F187 (optimizer param binding), F188 (bot intraday-only constraint)
