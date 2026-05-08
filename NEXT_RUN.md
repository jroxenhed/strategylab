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

**Date:** 2026-05-08 (builds 13+14)
**Branch:** `claude/sharp-allen-igMUK`

**Shipped:**
- **B5** Borrow cost for live shorts — `borrow_rate_annual` on `BotConfig`, `entry_time` on `BotState`, borrow cost computed at exit using backtest formula, stored in journal.
- **B8** Spread-derived slippage — live spread display + spread-derived modeled default. `decide_modeled_bps()` uses half-spread when <20 fills (floor 2 bps, cap 50 bps, market-hours only). Skipped for Alpaca free tier (IEX-only, not NBBO). IBKR only.

**Review findings (post-merge human review):**
- PR #19: 7 findings fixed (2 P1, 4 P2, 1 P3). Borrow cost now in external-close path, entry_time cleared in all cleanup paths, falsy guard fixed, manual_buy sets entry_time, negative rate validated, IEX spread display hidden.
- PR #20: 3 fixes (cap 50 bps, market-hours guard, Alpaca IEX skip). Duplicated block extracted to _spread_derived_bps() helper.

**Next up:** A8 viewport-only rendering [medium], D24b regime bot visual verification (manual QA), B5a borrow cost in TradeJournal UI [easy], B8a "Use live spread" button [easy].
