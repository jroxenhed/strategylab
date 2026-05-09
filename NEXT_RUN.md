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

**Date:** 2026-05-09 (PR #26 review fixes)
**Branch:** `claude/kind-shannon-SeZIp` → merged to main

**Shipped:**
- **F28e** `BotConfig.direction` validator — replaced `@field_validator('direction')` pattern with shared `DirectionField = Annotated[Literal['long', 'short'], BeforeValidator(str.lower)]` type alias in `models.py`. Applied to `StrategyRequest.direction` AND `BotConfig.direction`. Removed duplicate validator from `UpdateBotRequest`. Mirrors the LogicField pattern from PR #25 and closes the F28 validation pass across all models.
- **P3** Removed dead `import time` from `TestFetchOhlcvAsyncDedup` in `test_bot_runner.py`.

**Review:** 9 reviewers, 1 P1 + 4 P2 + 5 P3. P1 + 1 P3 fixed. Build: pass. Tests: 8 pass, 1 pre-existing failure (F33).

**Deferred (added to TODO):** F39 (batch quote silent null), F40 (dedup test timing gate), F41 (BotDetail.state type mismatch), F42 (eval_rules runtime guard), F43 (log injection via tickers).

**Previous run:** 2026-05-08 (build 19 — F29/F30/F28d/F31/F32).

**Next up:** A14a SubPane loading state [easy][next], C25a Optimizer NaN guard improvements [easy][next], F33 fetch-path test audit [easy], F39–F43 housekeeping batch [easy], D27a status tooltip popover [medium], A8 off-screen downsampling [medium].
