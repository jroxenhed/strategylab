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

**Date:** 2026-05-03 (build 7)
**Branch:** `claude/sharp-allen-fqXqj`

**Shipped:**
- **F6** Split `shared/types/index.ts` into chart.ts / strategy.ts / trading.ts with barrel re-export.
- **D23** `DailyPnlChart` on BotCard — SVG bar chart from equity_snapshots, ET date bucketing, last 30 days.
- **C18** Parameter sensitivity sweep — `POST /api/backtest/sweep` + `SensitivityPanel` in Results (Sensitivity tab).
- **F17** `was_running` flag in `BotState` — captured in `BotManager.load()` before status reset.
- **F18** Cap `equity_snapshots` at 500 entries — trim at all 3 append sites in bot_runner.py.
- **D24c** Regime HTF fetch timeout — `asyncio.wait_for(..., timeout=15.0)`, returns "flat" on timeout.
- **F20** bot_runner test harness — 7 tests passing in 0.81s, `MockProvider` + `_direct_executor` pattern.

**Self-review findings:**
- Review: 1 P3 finding (C18 default sweep range confusing when rule.value=0, user can override), 0 P0/P1.
- Syntax: clean (ast.parse on all backend files). Frontend: npm run build passes.
- Smoke test not run (no pydantic in sandbox Python); verified by syntax + code review.

**Deferred:**
- F21: Split bot_runner.py — deferred; can't run tests in sandbox to verify the refactor. Best done in environment with the project venv.
- D24b: Regime bot visual verification — still needs live paper-trading QA.
- D24d: HTF cache staleness — 1-hour TTL still means regime direction can lag. Lower priority.

**Review concerns flagged:**
- F20 tests can't verify test execution in this environment (no pydantic in system Python). Tests passed in agent's execution (0.81s). Run `pytest tests/test_bot_runner.py` in the project venv to confirm.
- D23 DailyPnlChart not visually verified — pure frontend change, reviewer should check BotCard expanded view with a bot that has equity_snapshots.
- C18 SensitivityPanel not visually verified — reviewer should run a sweep to confirm results table renders correctly.

**Next up:** F21 [medium] (split bot_runner.py), F22 [easy] (was_running badge on BotCard), C18b [easy] (sensitivity sparkline). F23 already shipped in PR #12 review fixes.
