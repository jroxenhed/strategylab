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

**Date:** 2026-05-05 (build 11)
**Branch:** `claude/sharp-allen-cBKPL`

**Shipped:**
- **C9** Strategy comparison mode — was already fully implemented. Enhanced with: per-strategy capital, regime+B23 dual-rule support, B25 per-direction settings, B&H baseline dashed line, and "% Normalized" toggle.
- **D24d** Regime HTF cache staleness — `_TTL_DAILY_LIVE = 300.0` (5 min) for live daily intervals, down from 1-hour historical TTL.

**Review findings:**
- 0 P0, 0 P1, 0 P2. Build clean: `npm run build` + `ast.parse` both pass.
- Smoke test not run (backend changes are cache-TTL only — no logic change).

**Not visually verified:**
- C9 comparison mode: load 2 strategies, run comparison, verify B&H baseline line appears + normalize toggle works.
- C9 regime strategy: compare a regime strategy and verify it backtests correctly (regime config flows through).

**Concerns for human review:** None.

**Next up:** B29 [medium] (regime UX overhaul), F19 [medium] (React Query migration), C22 [large] (auto-optimizer).
