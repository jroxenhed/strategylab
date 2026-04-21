---
date: 2026-04-21
topic: strategy-builder-indicators
focus: Adding chart indicators as strategy builder rule conditions
---

# Ideation: Strategy Builder Indicators

## Context

The A4 redesign built a clean indicator registry for chart rendering (`INDICATOR_DEFS` + `INDICATOR_REGISTRY`, generic `SubPane`), but the strategy builder still hardcodes its own indicator list in `RuleRow.tsx` (`INDICATORS`, `CONDITIONS`, `CAN_USE_PARAM`, `PARAM_OPTIONS`) and `signal_engine.py` (`series_map`, `ref_map`). Gap: 6 chart indicator types vs 8 hardcoded rule indicators (MACD, RSI, price, ema20/50/200, ma8/ma21). BB, ATR, Volume are chartable but not usable in rules. Stochastic, ADX, VWAP, OBV not yet in either system.

## Ranked Ideas

### 1. Parameterized Moving Averages
**Description:** Replace 5 hardcoded MA entries (ma8, ma21, ema20, ema50, ema200) with generic `ma(period, type)`. User picks any period + SMA/EMA/RMA. Backend computes on demand via existing `compute_ma()`.
**Rationale:** Highest user-facing impact. 5 of 8 rule indicators are just MAs with hardcoded periods. Real strategies use MA(9), MA(13), MA(34), etc. Chart already supports arbitrary periods.
**Downsides:** Saved strategy migration (ema20 → ma type). UI needs period input per MA rule.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 2. Concrete Indicator Batch — Phase A (BB, ATR, Volume)
**Description:** Wire existing computed indicators into rules. BB: upper/lower/bandwidth/%B conditions. ATR: volatility filter (normalized as % of price). Volume: above N-bar average, spike detection.
**Rationale:** Already computed by backend, just not wired to series_map/ref_map. Low effort, high value. BB unlocks mean-reversion; ATR is table-stakes for regime filtering; volume confirmation is fundamental.
**Downsides:** BB needs multi-output addressing (upper vs lower). ATR normalization adds a derived series.
**Confidence:** 80%
**Complexity:** Low-Medium
**Status:** Unexplored

### 3. Multi-Output Addressing
**Description:** Way to reference specific outputs from multi-output indicators. Pragmatic approach: flattened names (`bb_upper`, `stoch_k`, `adx_plus_di`) until 15+ indicators justify dot-notation.
**Rationale:** Required for BB (3 bands), Stochastic (%K/%D), ADX (3 outputs). Without this, the most useful conditions for these indicators are inexpressible.
**Downsides:** Flattened names are simpler but less composable. Migration path to dot-notation if needed later.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 4. Concrete Indicator Batch — Phase B (Stochastic, ADX)
**Description:** New compute functions + registry entries. Stochastic (%K/%D crossovers, overbought/oversold). ADX (trend strength filter, +DI/-DI direction). Exercises multi-output addressing.
**Rationale:** Common retail setups. Validates that the registry pattern handles multi-output cleanly.
**Downsides:** New backend compute functions needed. Careful parameter defaults required.
**Confidence:** 75%
**Complexity:** Medium
**Status:** Unexplored

### 5. Unified Indicator Registry (incremental)
**Description:** Consolidate indicator definitions as new indicators are added — don't do a big upfront refactor, but pull the pattern closer to a shared registry so each subsequent indicator is easier. Goal: 2-file touch (one backend, one frontend) instead of 4+.
**Rationale:** 6/6 ideation agents converged on this as the root cause. But full refactor before adding any indicators is backwards. Consolidate incrementally.
**Downsides:** Incremental approach may leave inconsistencies between old and new indicators.
**Confidence:** 70%
**Complexity:** Medium (spread across other work)
**Status:** Unexplored

### 6. Generalized Indicator References (deferred)
**Description:** Any indicator output usable as comparison target. Expand PARAM_OPTIONS incrementally as indicators are added rather than full generalization now.
**Rationale:** Existing CAN_USE_PARAM covers main use case. Full generalization adds UI complexity not worth it at <15 indicators.
**Downsides:** Incrementalism may need rework when generalization eventually happens.
**Confidence:** 65%
**Complexity:** Medium-High (when eventually done)
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | NaN validation guard | Bug fix, not ideation — track and fix directly |
| 2 | Confidence scoring (0-1) | Rewrites entire eval pipeline for marginal benefit |
| 3 | Signal vs filter distinction | Over-engineers existing AND/OR logic |
| 4 | Rule preview on chart | UX polish, separate ideation topic |
| 5 | OR/THEN temporal sequencing | Separate concern — rule eval semantics |
| 6 | Adaptive threshold discovery | Strategy optimization, not indicator addition |
| 7 | Default signals per indicator | Hides rule logic from user |
| 8 | Rule templates / patterns | Premature — build after indicator system expands |
| 9 | Condition archetypes / traits | Nice at 20+ indicators, overkill at 10-12 |
