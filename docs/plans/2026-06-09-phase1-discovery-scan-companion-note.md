# Phase 1 — Desk Discovery Mode (companion note, design captured)

**Status:** DESIGN APPROVED 2026-06-09, BUILD DEFERRED until Phase 0 (F399–F403) lands + probes green. Tracked as **F404**. This note preserves the brainstormed design so it isn't lost; it gets a full spec when Phase 0 is done.

## One-line

A second mode on the premise workbench: instead of John writing a premise, a scan mines the Phase-0 data panel for data points that **lead the forward return**, and auto-mints the survivors as premise cards into the existing F397 flow. A premise *generator* feeding the premise *tester*. "Full regard" (fully autonomous through confirmation) is a deferred opt-in toggle.

## Load-bearing decisions (all settled in the 2026-06-09 brainstorm)

- **What it hunts: B → C, never A.** B = lead-lag predictors: "data point X at time *t* predicts the *k*-bar forward return." The candidate unit is **(X, k)** — horizon is swept. C = conditional/regime ("when X is in state Z, Y behaves differently") layers on later. A = anything-vs-anything correlation: rejected (spurious *and* untradeable).
- **Honesty spine = two windows + attempt-counting.** Mine the discovery window freely, logging *every* candidate tried. OOS does NOT save you from breadth — keeping survivors out of 10,000 tries moves the dredging into the OOS set. Fix: **deflated Sharpe ratio** (Bailey–López de Prado) — the bar to be believed rises with the number of candidates *screened*. The attempt tax counts **candidates (X,k), not metrics**.
- **The funnel:**
  1. **Cheap composite screen** on the discovery window — rank every (X,k) by a blended score: **linear IC + rank IC (Spearman) + sign/hit-rate + sub-period stability**, equal-weight v1, **weights pre-stated and never tuned on discovery data** (tuning the screen = fitting it, F338).
  2. **Real backtest** only the top survivors → actual long/short rule → real Sharpe.
  3. **Deflate** that Sharpe by the *total candidates screened* → only what clears the deflated bar mints a premise card. Survivors then go through the existing **WFA/OOS** confirmation machine.
- **Universe = a scan knob, not the inherited carve.** Default first run may use UNIVERSE_V2, but the liquidity floor and SEC-filer flag are *parameters* (Phase-0 labels) the scan can push *down* to look where F369 says the signal lives. Don't hardcode the carve; expose it.
- **Human-in-loop = auto-mint, John drives confirmation.** Every survivor becomes a pre-populated premise card (reuses F397 idea-history + dispositions wholesale). Fully-autonomous-through-confirmation = "middle + auto-confirm toggle," deferred.
- **Survivorship is stamped on every discovered premise** (Phase-0 free data is survivors-only; not fixable without the rejected Sharadar spend).

## Benchmarking: drift + sector co-movement (John, 2026-06-09)

The forward return a candidate "predicts" is contaminated by two structural movers that have nothing to do with the candidate:

1. **Markets move up and to the right.** The unconditional equity drift / risk premium means *any* long-biased predictor looks good vs zero. → the scan's target is **excess return over the market**, never raw return.
2. **Sectors move together.** A "predictor" can just be riding sector beta (same-SIC co-movement). → also benchmark **peer-relative** (same-SIC siblings), reusing the program's existing peer-excess lens (F349). A candidate that only beats the market but not its sector herd is sector-carried, not signal.

These compound the +1.5pp baseline landmine below — all three say the same thing: **the headline metric must be relative (market- and sector-adjusted, dose-response), never an absolute positive mean.**

## The one known landmine for discovery specifically

F369/F371 found a uniform **~+1.5pp positive mean** across *unrelated* event families — a size/survivorship **baseline artifact, NOT signal** (proven structural). A naive "does X predict a positive return" scan will rediscover it thousands of times. So the screen's benchmark is **vs a proper point-in-time baseline**, and the headline test is **dose-response (Q5−Q1)** which cancels the uniform offset — never "positive mean vs zero." (This is also why John's "rate across multiple metrics" instinct helps: a real predictor must beat the baseline on *ordering*, not level.)

## Depends on (Phase 0)

The four data panels + `feature_panel.py` join layer (F399–F403). No Phase 1 code starts until those probe green on real data.
