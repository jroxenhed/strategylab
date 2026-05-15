# UX Walkthrough — New Quant User on a 1440×900 Laptop

**Date:** 2026-05-15
**Persona:** Experienced quant / strategy builder, first session
**Viewport:** 1440 × 900 (MBP 14" default)
**Session shape:** SPY · 1h · 2025-01-01 → 2026-05-15 · BB+ADX+Stoch added · long+short RSI mean-reversion · 1d MACD regime gate · 2 % stop · backtest + all results sub-tabs + Discovery + Live Trading

Companion run to `2026-05-15-ux-walkthrough.md`. That doc was a different session; this one is the quant-laptop pass with concrete reproduction steps and a prioritised fix list.

---

## Scope exercised

| Area | Touched |
| --- | --- |
| Ticker / data source / interval / range | ✅ NVDA→SPY, 1D→1h, 6y→16mo |
| Indicators sidebar (+ Add flow) | ✅ MACD, RSI, BB, ADX, Stochastic |
| Regime rules (HTF gate) | ✅ pre-seeded 1d MACD>0, close·wait |
| Long rules | ✅ RSI<35 entry / RSI>65 exit |
| Short rules | ✅ RSI>70 entry / RSI<50 exit |
| Risk settings | ✅ 2 % global stop |
| Backtest run | ✅ 41 trades, +12.96 %, Sharpe 0.52, MDD ‑5.09 % |
| Results sub-tabs | ✅ Summary, Equity, Trades, Session, Monte Carlo, Rolling, Hold Time, Sensitivity, Optimizer, Walk-Forward, Detail |
| Top-level tabs | ✅ Chart, Live Trading, Discovery |

---

## What works well (don't regress)

These are the things that made the tool feel grown-up to a quant on first contact. Document them so future "simplifications" don't accidentally strip them out.

1. **Rule editor is dense but readable.** NOT toggle, mute, AND/OR groups, indicator param chips inline on each rule (RSI 14 + Wilder selector), per-direction stops. Rare in retail-grade tooling.
2. **Adaptive header copy.** `Direction: long entry · goes flat on flip, re-enters on signal` updates as rules change. Same for the regime chip (`1 rule · 1d · 3b · close·wait`).
3. **Context-sensitive warnings.**
   - `⚠ Add a stop-loss to limit open-position risk during flat periods` — appears with `close·wait` regime, disappears once stop is set.
   - `Single tab rules are inactive — Long/Short rules take precedence.` — exactly the kind of footgun-prevention copy that earns trust.
   - `No short entry rules — no short positions will open. Exits/stops still run on existing positions.` — perfect empty-state.
4. **Cost honesty.** Sharpe, PF, EV/trade, cost drag %, slippage in bps with empirical/default annotation, Kelly with ½/¼ recommendation, beta + R² vs SPY, win/loss streak histograms.
5. **Walk-forward is real**, not a token feature. IS/OOS windows, anchored toggle, rescaled stitching, ~5 minutes per 15-bar window.
6. **Live Trading bot cards** with mini equity + per-bot PnL — the most polished surface in the app.

---

## P1 issues (block daily-driver adoption on laptop)

### P1.1 — Indicator panes collapse the price chart to unusability
**Repro:** add BB, MACD, RSI, ADX, Stoch (all five) on any ticker / interval.
**Observed:** main candle pane shrinks to ~50 px tall — only BB lines + the right-side price ribbon are visible, no candles. Each sub-pane is also ~50 px and clipped.
**Why this matters:** "BB + 2 oscillators" is a routine setup. The current equal-share allocation punishes the most common workflow.
**Fix options:**
- Give the main candle pane a non-collapsible `min-height: 40 %` of total chart area; sub-panes share the remainder.
- Or collapse non-active sub-panes into a thin (16 px) header strip that expands on click.
- Or expose pane heights as drag-resize handles with persisted localStorage state per indicator.

### P1.2 — Results pane content area is squeezed below the rule editor
**Repro:** Run backtest. Note the Equity / MC / Rolling / Hold Time / Detail / WFA charts render into ~120–180 px of vertical space.
**Why this matters:** the analytics are the *point* of the bottom panel; the rule editor is a means to get there. After a run, the user wants to read results, not re-author the rules.
**Fix:** when a result is loaded, collapse the rule editor into a single summary chip with an "Edit rules ▾" disclosure. The dynamic header line already half-implements this — extend it.

Example collapsed state:
```
[▾ Edit] Long: RSI<35 → RSI>65 · Short: RSI>70 → RSI<50 · Stop 2 % · Regime: 1d MACD>0
```

### P1.3 — Intraday date-range silently clamps with no UI feedback
**Repro:** select 1h interval while From/To = 2020-01-01 → 2026-12-31.
**Observed:** request goes through, chart shows ~730 days of bars trimmed from the right, no warning. Backtester then reports "trades from 2024-05-15" with no explanation why 2020-2024 was dropped.
**Why this matters:** silent data clamping is a top-tier integrity issue for a backtest tool. A user comparing strategies across intervals will reach wrong conclusions.
**Fix:**
- On interval change, recompute the effective range and badge From/To with the clamp (`From 2020-01-01 → clamped to 2024-05-15 (730 d limit on 1h, Yahoo)`).
- Offer a "Use max window" button.
- Surface the limit in `_fetch()` errors so the frontend can render a proper banner rather than just trimmed data.

---

## P2 issues (felt rough)

### P2.1 — Slippage input breaks on comma-decimal locales
**Repro:** Swedish (or any `,` decimal) locale. On first paint of an empirical-override slippage value, the input shows `3,09` with `invalid="true"` and `valuemax="0"`.
**Risk:** silently sends `NaN` to the backtester on submit.
**Fix:** format with `Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })` (not `toLocaleString`); parse with `Number.parseFloat` after stripping non-`[0-9.]` characters.

### P2.2 — IBKR disabled-source tooltip is wrong
**Observed:** disabled IBKR data-source button has `description="Set ALPACA_API_KEY in .env to enable"`.
**Fix:** copy-only — `Set IBKR_HOST + IBKR_PORT in backend/.env and start IB Gateway`. Reference `project_ibkr.md` memory for canonical operator setup.

### P2.3 — Chart display-interval combobox is redundant / confusing
The header has `Aggregate: 1h ▾ (View 1D / 1W / 1M)` next to the sidebar `Interval: 1 Hour`. Two distinct concepts (data fetch interval vs visual aggregation) with overlapping labels.
**Fix:** hide the header control when it matches the data interval. Show only when meaningful (e.g. 5 m data viewed as 1 D bars). Rename the visible option to `Aggregate ▾`.

### P2.4 — Direction toggle is implicit (no hard switch)
Direction is inferred from which rule tabs are populated. There's no `Long / Short / Both` segmented control.
**Fix:** small segmented control above the LONG/SHORT tabs. Clicking `Long-only` mutes short entry rules visually; `Both` shows both. Saves new users a minute of hunting.

### P2.5 — Right-side `Settings` panel duplicates per-direction controls
Stop-Loss / Time Stop / Trailing appear both in the right panel (global) and inside the LONG/SHORT tabs (per-direction). Per-direction fields use `"global"` placeholder to indicate inheritance, but the precedence isn't visible.
**Fix:** show the *effective* value next to the per-direction field (`Effective: 2 % (from global)`). Or collapse right-side into an "Advanced" drawer.

---

## P3 issues (polish)

### P3.1 — Spinbutton `aria-valuemax="0"` everywhere
A11y reads "max 0" on many numeric inputs that accept arbitrary positive values. Reflect the true bound or omit the attribute.

### P3.2 — Console warnings from `react-resizable-panels`
`autoSaveId` (lowercase needed) and unrecognised `onLayout` handler. Cosmetic but masks real errors during dev.

### P3.3 — Trades table contrast
Green/red P&L colour is the only win/loss signal. On dim laptop screens (battery saver, low brightness) reds and greens are close in luminance. Add a 3 px left-border accent per row and bold the P&L column.

### P3.4 — Discovery tab is mostly empty space
Two widgets at top, then ~800 px of black background.
**Fix:** either explicitly stub ("Coming soon — scanner is preview only") or restructure into a 2-col layout so the white-space reads as intentional.

### P3.5 — Equity Curve and similar charts get the same squeezed-frame treatment
Same root cause as P1.2; fixing the rule-editor collapse will help here too.

### P3.6 — Sensitivity / Optimizer / WFA control rows could share a layout
Same conceptual shape (param picker + min/max/step + run button) but slightly different alignment per tab. Standardise to a single component.

---

## Suggested fix order

1. **P1.1** — chart pane min-height (smallest diff, biggest visible payoff)
2. **P1.3** — intraday clamp banner (integrity)
3. **P2.1** — slippage locale parse (data-integrity for non-US users)
4. **P1.2** — collapse rule editor after run (changes interaction model; do once #1 lands so you can see the actual results frame)
5. **P2.2 / P2.3 / P2.4** — copy + small components, batch into a hardening sweep
6. **P3.x** — polish bundle

## Out of scope for this doc

- MACD pane toggle resetting on ticker change — confirmed in session to be user-triggered, not a bug.
- Live Trading and Discovery deep-dives — only smoke-tested.
- Mobile / smaller-than-1280 viewports.

## Linked memories

- `feedback_browser_verification.md` — this report is the canonical example of live-browser verification surfacing UX issues a static review would miss.
- `project_date_input_blur.md` — From/To inputs commit on blur (relevant for the P1.3 clamp UX fix).
- `project_ibkr.md` — canonical IBKR operator setup, drives the P2.2 copy.
