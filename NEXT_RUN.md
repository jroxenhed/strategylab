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

**Date:** 2026-05-08 (build 18 + PR #25 review fixes)
**Branch:** `claude/overnight-2026-05-08`

**Shipped (build 18):**
- **A14** Chart loading skeleton — pulsing grey bars skeleton while OHLCV loads; "No data for {ticker}" when load completes empty. CSS animation in index.css. Not visually verified.
- **D27** Bot status tooltip — native `title` on status badge (compact + expanded) with status, P&L, position flag, last tick.
- **F28b** `buy_logic`/`sell_logic` validation — `@field_validator` restricting to `"AND" | "OR"` across 5 models (QuickBacktestRequest, BatchQuickBacktestRequest, StrategyRequest, BotConfig, UpdateBotRequest).
- **F28c** `cache_info()` crash fixed — 5-tuple unpack corrected to 6-tuple. `GET /api/cache` now works.
- **C25** Optimizer NaN guard — `isNaN(minN) || isNaN(maxN)` added before min > max check.

**Review fixes (PR #25, 1 P1 + 8 P2 + 8 P3):**
- P1: `LogicField` type alias + `BeforeValidator` across all 8 models (startup cascade fix)
- P2: 3 missed models validated (ScanRequest, RegimeConfig, PerformanceRequest)
- P2: 5 duplicate `validate_logic` methods eliminated
- P2: App.tsx isError branch shows "Failed to load" on network failure
- P2: cache_info star-unpack for future key growth resilience
- P3: cache_info +ext flag, BotCard lastTickStr dedup (3 IIFEs removed)

**Deferred (added to TODO):** A14b (skeleton stale-cache), A14c (ChartSkeleton location), C25a (NaN guard gaps), F31 (eval_rules defense-in-depth), F32 (BotCard unsafe optional chain).

**Previous run:** 2026-05-08 (build 17), branch `claude/overnight-2026-05-08` — B10 (CSV quoting), D26 (FundBar error), C23 (optimizer validation), F28 (backend validation), F26 (shared OHLCV cache).

**Next up:** F30 fetch_ohlcv_async test coverage [next][easy], F29 watchlist ticker validation [next][easy], F28d StrategyRequest direction validation [easy], F31 eval_rules defense-in-depth [easy], F32 BotCard unsafe optional chain [easy], A14a SubPane loading state [easy], A8 off-screen downsampling [medium].
