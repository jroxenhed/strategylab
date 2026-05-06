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

**Date:** 2026-05-06 (build 12)
**Branch:** `claude/sharp-allen-RUcHz`

**Shipped:**
- **F19** React Query migration — 5 shared query hooks replace 12 manual setInterval timers. Journal deduplicated between PositionsTable + TradeJournal. Bots list deduplicated between BotControlCenter + TradeJournal.
- **C22** Auto-optimizer — `POST /api/backtest/optimize` endpoint + `OptimizerPanel.tsx` "Optimizer" tab. Multi-param grid search (up to 3 params × 10 values, max 200 combos). Ranked table by Sharpe/Return/WinRate.

**Review findings:**
- 1 finding (P0: 0, P1: 0, P2: 1 — bot API connection error not surfaced after F19; fixed before commit), 1 auto-fixed, 1 iteration.
- Build: `npm run build` passes. `ast.parse` passes on all backend files.
- Smoke test: uvicorn not available in sandbox; C22 backend is a thin wrapper over tested `_apply_param` + `run_backtest`.

**Not visually verified:**
- F19: Bot polling, journal deduplication — not visually verified (no browser).
- C22: Optimizer tab UI — not visually verified. Verify: run a backtest, click Optimizer tab, add 2 params, run, see ranked table.

**Concerns for human review:**
- F19: `onStale` prop in PositionsTable/OrderHistory is not memoized at the call site — may cause unnecessary effect re-fires. P3, no correctness issue.
- C22: No timeout on optimizer endpoint — 200 backtests on a slow machine could take 30–60s. Consider adding `asyncio.wait_for` or a streaming response in a future pass.

**Next up:** C22 visual verification (manual QA), D24b (regime bot visual verification), A8 viewport-only rendering [medium].
