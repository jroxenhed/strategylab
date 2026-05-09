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

**Date:** 2026-05-09 (build 19)
**Branch:** `claude/kind-shannon-99Iw3`

**Shipped:**
- **F29** `POST /api/quotes` per-symbol validation — strip/uppercase/length guard before calling `get_quote()`. Invalid symbols return null entry and continue.
- **F30** `fetch_ohlcv_async` dedup coverage — `TestFetchOhlcvDedup` (2 tests) in `test_bot_runner.py`. Fixed stale `bot_runner._fetch` mock → `bot_runner.fetch_ohlcv_async` AsyncMock. All 9 tests pass.

**Review:** 0 findings (P0: 0, P1: 0, P2: 0), 0 auto-fixed, 0 iterations.

**New TODO items added:** F37 (error entry symbol truncation), F38 (TestFetchOhlcvDedup teardown isolation).

**Previous run:** 2026-05-08 (build 18 + PR #25 review fixes), branch `claude/overnight-2026-05-08`.

**Next up:** F28d StrategyRequest direction validation [next][easy], F31 eval_rules defense-in-depth [next][easy], F32 BotCard unsafe optional chain [easy], A14a SubPane loading state [easy], B30 RuleRow overflow [easy], F37 error entry truncation [easy].
