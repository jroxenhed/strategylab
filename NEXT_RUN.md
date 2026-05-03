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

- B24 — needs design discussion (dual strategy import)
- B25 — needs design discussion (per-direction settings)

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

**Date:** 2026-05-03 (build 9)
**Branch:** `claude/sharp-allen-6THKO`

**Shipped:**
- **C21** Sensitivity sweep param bug — fixed error swallowing (propagate HTTPException instead of zero rows), added `rule.params` sweep support for MA/RSI/Stochastic/ADX/BB periods in `_apply_param` (backend) and `buildParamOptions` (frontend). 3 P2s auto-fixed: max_drawdown color direction, integer rounding for period linspace, stale selectedPath reset.
- **C20** Equity curve blank — fixed. `bucket && macroData` ternary had a blind spot during macro data loading. Two-level ternary ensures chartRef div only mounts when `bucket === null`.

**Review findings:**
- Self-review pass on C21: 3 P2 findings, 0 P0/P1. All P2s auto-fixed.
- Build clean: `npm run build` passes, `ast.parse` clean on backtest_sweep.py.

**Deferred:**
- D24b: Regime bot visual verification — needs live paper-trading QA.
- D24d: HTF cache staleness — 1-hour TTL can lag regime direction.
- F22-badge for error-status bots — P3, product decision.

**Review concerns flagged:**
- C21 changes not visually verified — test sensitivity sweep with MA rule: `period` should appear as sweep option, values should be integers.

**Next up:** C22 [large] (auto-optimizer), B26 [medium] (sweep from rule row), or any [easy] items in section order.
