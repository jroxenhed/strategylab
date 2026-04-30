# Next Run Steering

The overnight builder reads this file before picking tasks. Edit it to steer autonomous runs.

## Task Override

Leave empty to use default `[next]` tag picking from TODO.md. Or list specific IDs:

<!-- Uncomment and edit to override:
- C11
- C12
-->

## Skip

Tasks to skip even if tagged `[next]`:

<!-- Uncomment and edit:
- B20 — needs design work first
-->

## Constraints

<!-- Uncomment any:
- Don't touch bot_runner.py (bots are live)
- Don't touch Chart.tsx (mid-refactor)
- Backend only — no frontend changes this run
-->

## Notes for the builder

<!-- Free-form steering. The builder reads this before starting.
- Focus on backend performance this run
- The IBKR gateway is down, skip anything that needs it
-->

## Last Run

**Date:** 2026-04-30
**Branch:** `claude/dreamy-albattani-cDc4K`
**Commit:** `499f53e`

**Shipped:**
- **C11** Monte Carlo simulation — `POST /api/backtest/montecarlo`, percentile bands (p5–p95), probability of ruin, `MonteCarloChart.tsx` SVG component, new Results tab
- **C12** Rolling performance window — client-side rolling win rate / avg PnL / Sharpe over N-trade windows, `RollingWindowChart.tsx`, new Results tab

**Self-review fixes applied:**
- Reset `mcResult` state when backtest result changes (stale data after re-run)
- Renamed `window` → `windowSize` in RollingWindowChart (shadowed browser global)

**Deferred:**
- D21 (strategy auto-pause on drawdown) — still tagged `[next]`, pick up next run

**Review concerns flagged:** None above P2 threshold. Both P2/P3 findings were safe_auto and applied before commit.

**Next up:** D21 — strategy auto-pause on drawdown (bot safety net)
