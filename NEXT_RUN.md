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

**Date:** 2026-05-08 (build 19 — overnight)
**Branch:** `claude/kind-shannon-SeZIp`

**Shipped:**
- **F29** Batch `/api/quotes` ticker validation — strip/upper/length guard before `get_quote()` in loop.
- **F30** `fetch_ohlcv_async` dedup tests — `TestFetchOhlcvAsyncDedup` with concurrent + sequential tests.
- **F28d** `StrategyRequest.direction` validator — `@field_validator` restricting to `'long' | 'short'`.
- **F31** `eval_rules()` `Literal` annotation — `logic: Literal['AND', 'OR']` in `signal_engine.py`.
- **F32** BotCard.tsx unsafe optional chains — all 8 `detail?.state.X` → `detail?.state?.X`.

**Review:** 0 findings (P0: 0, P1: 0, P2: 0). Build: pass.

**Deferred (added to TODO):** F28e (BotConfig.direction validator), F33 (TestTickStateTransitions fetch-path audit), C25a moved to [next].

**Concerns for human review:**
- F33: existing TestTickStateTransitions tests patch `bot_runner._fetch` but `_tick()` now calls `fetch_ohlcv_async()` → `shared._fetch`. The `bot_runner._fetch` patch may not intercept the actual fetch path — tests may be silently not exercising the mock data path. Needs a `patch("shared.fetch_ohlcv_async")` in `_base_patches` to be sure.

**Previous run:** 2026-05-08 (build 18 + PR #25 review fixes).

**Next up:** A14a SubPane loading state [easy][next], C25a Optimizer NaN guard improvements [easy][next], F28e BotConfig.direction validator [easy], F33 fetch-path test audit [easy], D27a status tooltip popover [medium], A8 off-screen downsampling [medium].
