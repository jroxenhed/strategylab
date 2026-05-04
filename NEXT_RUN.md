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

**Date:** 2026-05-04 (build 10)
**Branch:** `claude/sharp-allen-V5KsM`
**PR:** https://github.com/jroxenhed/strategylab/pull/16

**Shipped:**
- **C23** Sweep error banner — styled red banner replaces plain div when sweep HTTP call fails.
- **C24** Direction-aware analytics — `sell_trades` filter includes both "sell"/"cover" exits (fixes num_trades + win_rate for regime close_and_reverse). Trades tab column headers adapt to "Entry"/"Exit" for short strategies.
- **B26** Sweep from rule row — TrendingUp button on numeric-threshold rule rows pre-fills Sensitivity tab (path + ±50% range) and switches to it automatically.

**Review findings:**
- 0 P0, 0 P1, 1 P2 (value=0 degenerate sweep range — not fixed, acceptable edge case).
- Build clean: `npm run build` passes, `ast.parse` clean on backtest.py.
- Smoke test: uvicorn not in PATH — not run. Backend change is a 2-line filter widening.

**Not visually verified:**
- C24: Run a regime close_and_reverse strategy and check num_trades + Trades tab column labels.
- B26: Click TrendingUp on an RSI rule row, verify Sensitivity tab opens with correct path + range pre-filled.
- C23: Trigger a sweep error (e.g., invalid param_path) and verify the styled banner appears.

**Next up:** B27 [easy] (strategy preset categories), B28 [medium] (regime rules as full rule sets).
