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

**Date:** 2026-05-08 (build 17)
**Branch:** `claude/overnight-2026-05-08`

**Shipped:**
- **B10** TradeJournal CSV quoting — RFC 4180 `csvField()` helper wraps comma/quote-containing fields. Fixes column misalignment on timestamp fields like "May 8, 2026 10:30 AM".
- **D26** FundBar invalid input feedback — inline red error message for empty/NaN/negative fund amounts. Previously silent no-op.
- **C23** Optimizer validation — pre-submit guard for `min > max` and `steps < 2` in `runOptimizer()`. Error shown before loading spinner starts.
- **F28** Backend input validation — Pydantic `Field` + `field_validator` across 6 request models (backtest_quick, bots, trading, quote). Rejects negative capitals, invalid directions, zero qty.
- **F26** Shared OHLCV cache — `fetch_ohlcv_async()` in shared.py deduplicates concurrent asyncio coroutines at Future level. Multiple bots on same symbol share one executor call.

**Review findings:** 0 findings (P0: 0, P1: 0, P2: 0), 0 auto-fixed, 1 build iteration. All 109 frontend tests pass.

**Deferred:**
- **A8** viewport-only rendering — large multi-session task, deferred.
- Nothing else deferred — all 5 [next] easy/medium tasks shipped.

**Previous run:** 2026-05-08 (build 16), branch `claude/wizardly-newton-xwoKZ` — B5b (borrow total), B5c (bot resume borrow), F27 (concurrent _fetch dedup).

**Next up:** A14 chart loading skeleton [next][easy], D27 bot status tooltip [next][easy], F29 watchlist ticker validation [easy], C25 optimizer NaN handling [easy], F30 fetch_ohlcv_async test coverage [easy], A8 off-screen downsampling [medium].
