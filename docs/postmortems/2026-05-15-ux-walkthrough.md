# UX Walkthrough — Fresh-User Quant Run-Through

**Date:** 2026-05-15
**Method:** Live-browser session (chrome-devtools MCP). Acted as an experienced quant builder using StrategyLab for the first time. Full end-to-end flow on Chart tab.

## Flow exercised

1. Loaded `http://localhost:5173` — landed on Live Trading (default tab), switched to Chart.
2. Changed ticker KO → NVDA, interval 1H → Daily, range 2020-01-01 → 2026-12-31, Alpaca/IEX source.
3. Ran baseline backtest: RSI(14) below 30 / above 70 → +412.3% / Sharpe 1.067 / 17 trades / WR 88.2% / MaxDD −35.66%. B&H +2162.76% (Alpha −1750.5%).
4. Opened Trades tab (sortable columns, Exit reason = "Signal").
5. Optimizer: Param1 = Buy RSI threshold [15–40, 6 steps], Param2 = Sell RSI threshold [60–85, 6 steps] → 36 BTs in 1s. Winner Buy=35 / Sell=65, Sharpe 1.240, Return +659.74%.
6. Manually applied 35/65 to BUY/SELL rules (no apply-from-optimizer affordance).
7. Walk-Forward: same params, IS=252, OOS=63, anchored OFF → 19 windows × 16 combos = 304 IS + 19 OOS in 3s. All 19 windows flagged **Thin IS**; tool warned "*the optimizer was selecting parameters from coin-flip data*". WFE 2.057 nominally but unreliable.
8. Regime: added 1 rule `MACD Is above 0`, timeframe 1d, On-flip close-wait, min-bars 3 → 15 trades, +16.14%, Sharpe 0.305, MaxDD −16.28%. Trend filter destroyed the mean-reversion edge (expected outcome; surfaced instantly).

## What works — strengths to protect

1. **WFA honesty.** Per-window verdict column ("Thin IS"), the explicit min-trades-IS diagnostic, and the parameter CV table at the bottom turn WFA from cosmetics into actual decision support. Single biggest differentiator vs retail tools.
2. **Optimizer / WFA pre-flight math.** "36 combinations estimated", "~408 backtests (~3s)", "1d: 1 bar/trading day — IS=252 spans ≈50 weeks". Tells me what I'm committing to before I click Run.
3. **Empirical slippage loop.** Slippage input auto-updated from `2 (default)` to `3.09 (empirical: 23 fills)`. The "favorable empirical never makes the backtest cheaper" policy is exactly the right asymmetry for honest backtests.
4. **B&H + Alpha in the same eyeline as Return.** Most retail tools hide this. Here +412% looks great until +2163% B&H sits next to it. Crucial truth-teller.
5. **Regime panel composition.** Multi-TF, On-flip semantics (close-wait / close-immediate / hold), min-bars hysteresis, per-direction risk overrides, and "*goes flat when regime inactive · ⚠ Add a stop-loss…*" prompt are all quant-aware. The collapsed summary chip ("1 rule · 1d · 3b · close·wait") is great.
6. **Summary tab density.** EV decomposition with the actual arithmetic on screen, Kelly / ½K / ¼K side-by-side, beta/R² to SPY, win/loss streak stats. Saves me copying numbers into a notebook.
7. **3-pane chart sync.** Price/MACD/RSI pan and crosshair-sync cleanly.

## Issues found — ordered by impact

### Tier 1 — quant footguns / silent wrong answers

**U1. Optimizer and WFA default Param 1 to "Slippage (bps)".**
You don't optimize slippage; it's a cost assumption. A first-time user runs it and "finds" 0 bps is best. Fix: default to *Buy Rule 1 Threshold (RSI)* or unset placeholder + disabled Run button.
*Effort: 1 line.*

**U2. Zero-trade backtests are silent.**
Switched MACD regime condition to "Is above" without filling the comparison Value → ran with 0 trades, no warning, just a clean empty Trades tab. Should surface a banner ("Regime rule has no threshold — never evaluates"). Generalises: any rule with NaN / empty / unparseable threshold should fail loudly.
*Effort: validation pass in rule serializer + banner component.*

**U3. Default interval 1H on 6-year range silently clamps.**
Default range 2020-01-01 → 2026-12-31 with `1 Hour`. yfinance/Alpaca intraday capped at ~730d. Header still shows full range; user thinks they backtested 6 years. Either auto-correct to Daily when range exceeds clamp, or chip near date range: "⚠ Intraday clamped to last 730d — actual range used: 2024-05 → 2026-12".
*Effort: small. There's already a `_fetch()` clamp in shared.py — surface the clamped dates to the UI.*

### Tier 2 — workflow friction

**U4. No "Apply best to rules" button in Optimizer results.**
After finding Buy=35/Sell=65, I retyped them into the rule rows. Clicking a result row did nothing. Top row should have "Apply to rules" + ideally "Apply & Re-run Backtest". This is the single biggest workflow win.
*Effort: ~hour. Result row already has the param dict; rules are mutable via the same path as Save As.*

**U5. MACD condition list is incomplete for state-based regimes.**
Options: Crosses-above-signal, Crosses-below-signal, Crosses-above/below (value), Is-above/below (value). Missing: **Is-above-signal / Is-below-signal**. Crossover events are sparse; you almost always want the *state* "MACD currently above its signal line" for a regime filter. Worked around with `MACD Is above 0`, fine but not the canonical reach.
*Effort: extend `MACD` condition list in `signal_engine.py` + UI dropdown.*

**U6. Layout doesn't show the data you just asked for.**
Equity Curve, Optimizer results, and WFA tables all fell below the fold of an internal sub-scroller at the default window size. Results panel scrolls internally with no scrollbar hint. Three times I had to resize the viewport or scrollIntoView programmatically to see the headline I'd just generated. Fix: on backtest/optimizer/WFA completion, scroll the results panel to top of the active tab; show a subtle scrollbar; consider remembering split-pane ratio.
*Effort: small — one `scrollIntoView` per tab-mount hook + CSS scrollbar polish.*

**U7. Chart auto-zoom doesn't fit data after ticker/interval changes.**
Switched KO 1H → NVDA Daily; candle pane stayed zoomed into a ~1-month window on the right edge while equity curve covered 2020–2026. No obvious "fit data" button. `↻` does fetch, not zoom-fit. The sidebar Y/Q/M/W/D presets are for *date range*, not chart zoom — confusing overload of the same letters.
*Effort: call `timeScale().fitContent()` on data refresh; rename one of the two D/W/M/Q/Y axes.*

### Tier 3 — first-impression polish

**U8. Default viewport (1024-ish) starves the results panel.**
At common laptop widths the right-rail Settings + left-rail sidebar squeeze the centre to a point where results are barely visible. Either narrow the default rails or break to single-column below a breakpoint.

**U9. ~~Date inputs commit on blur, not on change.~~ (Not a bug — deliberate.)**
Typing year/month/day spinbuttons doesn't update the chart until tab-out. **Deliberate design choice** so editing multi-part dates doesn't re-fetch the chart on every keystroke. Leaving the note here only so future reviewers don't re-flag it. The trap on the agent side is documented in CLAUDE.md → "Live-Browser UI Verification → Known traps".

**U10. Param picker is positional and verbose.**
"Buy Rule 1 Threshold (RSI)" / "Sell Rule 1 period (RSI)" doesn't scale past 2 rules. Two-step picker (rule first → field) or `<optgroup>` per rule.

**U11. Strategy lifecycle is the weakest part of the tool.**
Save-As + flat dropdown only. No project notion, no fork-from-this, no diff, no commit note. For a quant tool the strategy is the unit of work; the lifecycle deserves more weight. The `⇄ Compare` button is the seed of this — extend it.

**U12. Optimizer needs a heatmap for 2-param sweeps.**
A 6×6 Sharpe heatmap (cold→hot) communicates *plateau vs spike* — the only thing that actually matters for whether the peak is robust. The table tells you the peak; the heatmap tells you whether the peak is real.

**U13. "Enable Signal Trace tab" checkbox has no affordance.**
Sitting next to Run Backtest with no tooltip or description. Either a tooltip ("Records every rule evaluation per bar — slower, useful for debugging missed signals") or move it under an advanced disclosure.

**U14. No sticky "current strategy metrics" strip.**
Once four tabs deep in WFA, you can't see the plain backtest baseline. A one-line strip above the tab bar — "RSI 35/65 daily · 17 trades · +412% · Sharpe 1.07 · MaxDD −36%" — would anchor orientation.

## Smaller wins observed (don't lose these)

- "EDT" header toggle for timezone clarity
- "Sweep this value in Sensitivity tab" mini-button on every threshold input
- Regime summary chip ("1 rule · 1d · 3b · close·wait") — collapse default for rules too?
- Trades table sortable headers
- Watchlist `+` quick-add hint

## Suggested TODO items (for triage into TODO.md)

- **F-UX1** Optimizer / WFA Param 1 default → "Buy Rule 1 Threshold" (or unset). [easy] [polish]
- **F-UX2** Zero-trade / empty-threshold rule validation banner. [easy] [hardening]
- **F-UX3** Surface clamped date range when intraday + multi-year. [easy] [polish]
- **F-UX4** "Apply best to rules" + "Apply & Re-run" in Optimizer top row. [medium] [polish]
- **F-UX5** Add `Is above signal` / `Is below signal` conditions for MACD (and any other oscillator with a signal line). [medium] [arch]
- **F-UX6** Auto-scroll results panel to active tab headline on completion. [easy] [polish]
- **F-UX7** `chart.timeScale().fitContent()` on ticker/interval/range change. [easy] [polish]
- **F-UX8** Two-param Optimizer heatmap visualization. [medium] [polish]
- **F-UX9** Sticky current-strategy metrics strip above tab bar. [easy] [polish]
- **F-UX10** Strategy lifecycle: fork, diff, note, version tag. [hard] [arch]

## Net take

The analytics layer (WFA stability tags, optimizer pre-flight, empirical slippage, B&H honesty, Kelly side-by-side, regime composition) is stronger than any retail backtester I've used. The build flow on top of it has small dull edges — the optimizer → rules → re-test loop has one too many manual steps, "default Param 1 = Slippage" is wrong on principle, and the layout doesn't show the data you just asked for without intervention. None are hard fixes; all are first-five-minutes-of-use issues, so they punch above their weight on first impressions.
