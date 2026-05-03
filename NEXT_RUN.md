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

**Date:** 2026-05-03 (build 6)
**Branch:** `claude/bold-einstein-XBXNO`
**Commits:** 5a3076b (F14/F15/F16), 25492c3 (D24a), 6771f01 (D25)

**Shipped:**
- **F14** Atomic bots.json: `save()` now writes to `.tmp` then `os.replace()` — crash-safe.
- **F15** Log journal errors: 5 `except Exception: pass` blocks around `_log_trade()` now log via `self._log("ERROR", ...)`.
- **F16** Journal write lock: `threading.Lock` wraps read-modify-write in `_log_trade()`.
- **D24a** `backtest_bot()` regime passthrough: added 9 missing fields (`regime`, dual-rule sets, logics) to `StrategyRequest` constructor.
- **D25** Entry guard: opposite-direction position check now skips entry on exception instead of proceeding.

**Self-review findings:**
- Review: 0 findings across all 5 tasks, 0 auto-fixed, 1 iteration each.
- Syntax: clean (ast.parse). Backend-only changes — no frontend build needed.
- Smoke test not run (no pandas/uvicorn in sandbox); verified by syntax + code review.

**Deferred:**
- D24b: Regime bot visual verification — still needs live paper-trading QA.

**Review concerns flagged:**
- None. All changes were mechanical/safe.

**Next up:** F6 [easy] (split shared/types/index.ts), C18 [medium] (parameter sensitivity sweep), D23 [medium] (bot daily P&L), F20 [medium] (bot_runner test harness), F21 [medium] (split bot_runner.py — prereq: F20). D24c [easy] (regime HTF timeout) and D24d [medium] (HTF cache staleness) also worth picking up.
