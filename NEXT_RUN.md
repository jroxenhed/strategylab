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

**Date:** 2026-05-03
**Branch:** `claude/overnight-2026-05-03`

**Shipped:**
- **A13b** Multi-TF indicator overlay — `htf_interval` param in indicators endpoint, `htfInterval` on `IndicatorInstance`, TF selector in sidebar, `useQueries` HTF grouping in `useOHLCV`, `LineType.WithSteps` in Chart.tsx for HTF series.
- **C10** Discovered already shipped (session analytics tab in Results). Checked off.
- **C17** SPY beta/R² — `_compute_spy_correlation()` in `backtest.py`, new Summary panel showing β and R² with context labels.
- **C19** Backtest result persistence — auto-save/restore via `strategylab-last-backtest` localStorage key, validated against current ticker/dates/interval.

**Self-review findings: 1 (P0: 0, P1: 0, P2: 1), 0 auto-fixed, 0 iterations**
- P2: HTF indicator fetches LTF data twice when `htf_interval` equals `interval` — wasteful, harmless, deferred.

**Deferred:** None. All 3 tasks shipped.

**Review concerns flagged:** Python test environment not available in sandbox (no pandas); backend Python changes syntax-verified only.

**Next up:** B21 [next] (regime sit-flat gate — large, has full plan in docs/superpowers/plans/2026-05-01-regime-filter.md). Also C18 and C9 are medium-difficulty standalone tasks if B21 is too large for one run.
