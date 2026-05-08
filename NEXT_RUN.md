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

**Date:** 2026-05-08 (build 16)
**Branch:** `claude/wizardly-newton-xwoKZ`

**Shipped:**
- **B5b** Total borrow cost in TradeJournal summary row — null-safe `totalBorrowCost` in `summaryStats`; summary Borrow cell shows `$X.XXXX` in red when > 0.
- **B5c** Bot runner borrow cost on position resume — `entry_time` was never set when bot resumed tracking an externally-opened position, causing `_compute_borrow_cost()` to silently return 0.0. Fixed by setting `state.entry_time = now` in the resume block.
- **F27** Concurrent `_fetch()` dedup — threading.Lock per `(symbol, interval)` in `shared.py`. Second concurrent caller blocks and hits the freshly-populated cache instead of firing a duplicate HTTP request.

**Review findings:** 0 findings (P0: 0, P1: 0, P2: 0), 0 auto-fixed. Build clean.

**Deferred:**
- **A8** viewport-only rendering — estimated 500-700 lines across 5-6 files, 6-8 hours, high regression risk on pan/zoom and multi-pane sync. Needs multi-session approach with feature flag.
- **F26** shared OHLCV cache — architectural follow-up to F27. F27 covers the critical concurrent-miss problem; F26 eliminates per-bot calls entirely. Lower urgency now.

**Previous run:** 2026-05-08 (build 15), branch `claude/wizardly-newton-AAkXw` — B5a (borrow column UI), B8a (use live spread button). Post-merge review found 2 P1s, both fixed.

**Next up:** F26 shared OHLCV cache [next][medium], A8 off-screen downsampling [next][medium].
