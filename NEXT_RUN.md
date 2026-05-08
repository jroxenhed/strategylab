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

**Date:** 2026-05-08 (build 15)
**Branch:** `claude/wizardly-newton-AAkXw`

**Shipped:**
- **B5a** Borrow cost in TradeJournal UI — conditional Borrow column (shows when any visible row has borrow_cost > 0). Dollar amount in red, CSV export included. TS type updated.
- **B8a** "Use live spread" button — appears in Capital & Fees when IBKR live spread available. Pre-fills slippageBps with half_spread_bps, sets source label to "live spread".

**Review findings:** 0 findings (P0: 0, P1: 0, P2: 0), 0 auto-fixed.

**Previous run:** 2026-05-08 (builds 13+14), branch `claude/sharp-allen-igMUK` — B5 (borrow cost live bots), B8 (spread-derived slippage). Post-merge review found 10 total findings across 2 PRs, all fixed.

**Next up:** A8 viewport-only rendering [next][medium], F26 shared OHLCV cache [next][medium], B5b total borrow in summary row [easy], B8b slippage auto-reset bug [easy], F27 concurrent fetch dedup [easy].
