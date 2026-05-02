# Future Vision — The Autonomous Trading Pipeline

Late-night brainstorm from the 2026-05-02 planning session. Not a spec, not a plan — just the north star.

## The Pipeline

```
Market data in
    → Strategies discovered (scan for edge)
    → Backtested (full cost model, realistic slippage)
    → Sized (Kelly criterion, account risk budget)
    → Deployed (spawn bot, regime-gated)
    → Monitored (drawdown auto-pause, Slack alerts)
    → Self-adjusting (regime flips direction, pauses on bleed)
    → You review the weekly P&L
```

## What Already Exists

| Piece | Status | TODO ID |
|-------|--------|---------|
| Data fetching (Yahoo, Alpaca, IBKR) | Shipped | — |
| Backtester with full cost model | Shipped | B6, B7 |
| Short selling + direction switching | Shipped (single direction) | B19 |
| Kelly criterion sizing | Shipped | C16 |
| Monte Carlo confidence intervals | Shipped | C11 |
| Bot deployment + management | Shipped | D11 |
| Auto-pause on drawdown | Shipped | D21 |
| Notifications (Pushover) | Shipped | D20 |
| Overnight builder (self-directing) | Shipped | — |
| Multi-TF data foundation | Shipped | A13a |
| Multi-TF indicator overlay | Tonight's build | A13b |
| Regime filter (sit-flat gate) | Planned | B21 |
| Regime direction switching | Planned | B22, B23 |
| Regime live bot integration | Planned | D24 |

## What's Missing — The Discovery Pipeline

This is Section E in TODO.md, mostly untouched. The gap between "I have a strategy" and "the system finds strategies for me."

### E1 — Scan for candidates
What makes a good StrategyLab candidate? Criteria undefined. Possible filters:
- Sufficient liquidity (avg volume, spread)
- Trending behavior (ADX > 25 on daily? Hurst exponent?)
- Not too correlated with existing bots (diversification)
- Enough history for meaningful backtest

### E2 — Batch backtesting
Run the same strategy across N symbols. Already have `POST /api/backtest/quick/batch` (shipped in E5). The missing piece is a UI/workflow for "test this strategy on these 50 symbols, rank by Sharpe/Kelly/drawdown."

### E3 — Parameter optimization
Given a strategy template, sweep parameters (MA period, RSI threshold, stop-loss %, etc.) and find the best performer. Parameter sensitivity (C18) is related — it answers "how fragile is this edge?" which is the guard against overfitting.

### E4 — Auto-deploy
Take the top N strategies from the sweep, size them via Kelly, and spawn bots. This is the last mile — and the scariest, because it puts real money behind automated decisions.

## The Glue

The pieces exist in isolation. The glue is:
1. **Strategy templates** — parameterized strategies that can be swept (B12 parameterized MAs already ship this pattern)
2. **Ranking engine** — given N backtest results, rank by composite score (Sharpe * sqrt(trades) * Kelly fraction, penalized by max drawdown)
3. **Portfolio-level risk budget** — Kelly sizes individual bots, but total allocated across all bots needs a cap (e.g., never deploy more than 80% of capital across all bots)
4. **Regime filter as the safety layer** — every deployed bot gets a regime filter. The bot can find its own edge, but the regime prevents it from trading in hostile conditions.

## Why This Is Scary (In a Good Way)

Every step from here reduces human involvement:
- Today: user designs strategy, user deploys bot, user watches it
- After regime filter: user designs strategy, bot manages itself
- After discovery: system finds strategies, user approves deployment
- After auto-deploy: system finds, sizes, deploys, manages. User reviews weekly.

The overnight builder already suggests its own TODO items. The discovery pipeline would let the system suggest its own *strategies*. At some point the user's role shifts from "builder" to "board of directors" — setting risk limits, reviewing performance, approving major changes.

That's either the endgame or the starting line. Depends on how far you want to go.

## How We'd Get There

Same pattern that's worked for everything else:
1. Start with a messy conversation about what discovery means
2. Let it get shaped through adversarial review
3. Decompose into incremental stages the overnight builder can ship
4. Each stage delivers standalone value
5. The full pipeline emerges from the pieces

No big bang. No grand architecture. Just small steps with a clear direction.

## Open Questions

- What's the minimum viable discovery? Probably: pick 10 symbols, run one strategy template, rank by Sharpe. That's a single session of work.
- How do you prevent overfitting in automated parameter sweeps? Walk-forward validation? Out-of-sample hold-out? Monte Carlo on the top results?
- What's the risk budget model? Fixed-fractional of total capital? Kelly on the portfolio level?
- When does a bot get killed? Drawdown auto-pause is temporary. When does the system decide a strategy is dead and deallocate?
- How much autonomy is too much? The overnight builder suggests TODOs. The discovery pipeline would suggest strategies. Where's the line between "helpful automation" and "I have no idea what my bots are doing"?

## Critical Pushback (from brainstorm review)

**The overfitting trap.** Automated parameter sweeps will *always* find something that backtests well. That's not alpha — it's overfitting dressed up as alpha. Without a rigorous overfitting gate, the auto-deploy pipeline becomes an automated way to lose money confidently. The walk-forward / out-of-sample / Monte Carlo question above isn't optional — it's the most important technical decision in the entire discovery pipeline.

**Bot correlation is the hidden portfolio risk.** Five bots that all go long on momentum stocks aren't diversified — they're the same bet five times. The ranking engine (Sharpe * sqrt(trades) * Kelly, penalized by drawdown) rewards individual strategy quality, but the portfolio-level risk budget needs a correlation check. If new bot candidate correlates >0.7 with existing deployed bots, it adds concentration risk, not diversification. Need a correlation gate before auto-deploy.

**The "board of directors" role, made concrete.** A board doesn't write code or pick trades. The weekly check-in is: review P&L, review regime states, kill decayed strategies, approve new deployments from the discovery pipeline. Everything else is delegated. The human stays in the loop at the approval and risk-limit layer, not the execution layer.

**Context from lived experience:** Portfolio peaked +95% then halved in the chop. The discretionary version of "can't manage positions in a hostile regime" is exactly what the regime filter solves. The discovery pipeline needs the same discipline — knowing when an edge has decayed and cutting it, automatically.

## The Confidence Layers

You can never prove a strategy will work in the future. But you can build layers of confidence, each reducing the odds you're fooling yourself.

**Layer 1: Out-of-sample testing.** Split the data — optimize on 2021–2024, test on 2025–2026. If it only works on the tuning window, it's curve-fitted garbage. Walk-forward validation automates this: optimize on window 1, test on window 2, slide forward, repeat. A strategy that survives walk-forward across multiple windows is meaningfully more trustworthy.

**Layer 2: Monte Carlo.** Already shipped (C11). Doesn't test whether the edge is real, but tests whether the returns are robust to sequencing. A strategy that backtests +90% but has 30% probability of ruin under Monte Carlo isn't deployable regardless of how real the edge is.

**Layer 3: Cross-symbol validation.** If a stochastic strategy on RIVN also works on LCID, TSLA, and F — different companies, same sector — the edge is probably structural (how momentum works in EV stocks), not idiosyncratic (RIVN's order flow in Q3 2024). If it *only* works on one symbol, be suspicious.

**Layer 4: Paper trading with live data.** Where StrategyLab is now. Tests things backtesting literally cannot: real-time data gaps, API latency, fill quality, slippage divergence from modeled assumptions, behavior around earnings and halt events. Paper trading isn't backtesting on new data — it's testing the *infrastructure* as much as the strategy.

**Layer 5: Micro-live.** Deploy with the smallest position size that still generates meaningful data. You're not trying to get rich on the first deployment — you're buying information. Does the live Sharpe match the backtest Sharpe within some tolerance band? Does real slippage match the model? Does the win rate converge toward the backtest expectation as trade count grows? If after 50 live trades the metrics are within one standard deviation of the backtest, you've got something. If they're wildly off, you learned that cheaply.

**Layer 6: The meta-question — does the edge make sense?** This is the one most people skip, and it's the most important. Every strategy that works is exploiting *someone else's mistake or structural constraint*. Can you articulate whose? If you can't explain *why* the edge exists — if the backtest just "found something" through parameter sweeping — that's when you should be most worried. Markets change. Regimes shift. The specific pattern your optimizer found in 2022–2024 data might have been a liquidity artifact from the Fed hiking cycle. When the macro regime changes, poof.

**Layer 6 is the one that can't be automated.** The system can find candidates, but only the human can answer "whose mistake is this strategy exploiting, and will that mistake persist?" This is where the "board of directors" role is permanent.

## The Discovery Funnel

The pipeline shouldn't just find strategies that *performed well*. It should find strategies that performed well AND survived walk-forward AND work across multiple symbols AND have a plausible reason for existing. Each filter kills most candidates. What survives is worth deploying at micro-live scale. What proves itself at micro-live gets sized up.

```
1000 parameter combinations tested
  → 50 pass Sharpe/drawdown thresholds
    → 12 survive walk-forward validation
      → 5 work on 3+ symbols
        → 2 you can explain WHY they work
          → 1 deployed at micro-live
            → confirmed or killed by real data
```

That last step — real market data generating real fills — is the only thing that actually answers the question "will this work going forward?" Everything before it is just increasingly sophisticated ways of *not fooling yourself*. The paper trading and micro-live phases are where backtesting ends and evidence begins.

## What's Buildable Now

Most of this funnel maps to existing or planned infrastructure:

| Funnel stage | StrategyLab feature | Status |
|---|---|---|
| Parameter sweep | C18 (parameter sensitivity) | TODO |
| Sharpe/drawdown threshold | Backtest summary stats | Shipped |
| Walk-forward validation | New — split backtest into windows | Not started |
| Cross-symbol | E2 batch backtesting | TODO, endpoint exists |
| Edge explanation | Human judgment | Permanent |
| Micro-live deployment | Bot system + Kelly sizing | Shipped |
| Live confirmation | Bot P&L tracking + drawdown pause | Shipped |

The minimum viable discovery: 10 symbols, one strategy template, rank by Sharpe. One session of work. Immediately surfaces whether automated ranking produces anything worth trusting with real money.
