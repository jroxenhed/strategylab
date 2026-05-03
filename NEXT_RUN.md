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

<!-- Uncomment and edit:
- B20 — needs design work first
-->

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

**Date:** 2026-05-03
**Branch:** `claude/overnight-2026-05-03`

**Shipped:**
- **B21a** Regime config not restored on refresh — localStorage persistence effect missing regime fields; added `regime` to JSON and dep array.
- **A8c** (partial) "View as" 1D regime histogram axis fix — `snapRegimeTime` converts intraday unix timestamps to YYYY-MM-DD when displaying at daily scale; fixes axis confusion and thin candles.
- **B23** Regime dual rule sets — backend `b23_mode` + 8 schema fields; frontend Single/Long/Short tab UI in StrategyBuilder; dual rules sent in backtest request when both long and short buy rules populated.

**Self-review findings:**
- P1: `[emptyRule()]` initial state for dual rule arrays would always trigger b23_mode — fixed to `[]`.
- P2: HTF overlay smooth-vs-stepped when `viewInterval === htfInterval` — deferred as `A8c-htf`, cosmetic.
- P3: b23_mode + `on_flip = "hold"` doesn't force position exit on regime flip — documented, by-design.
- Build: clean (tsc -b + vite). Syntax: clean (python3 ast.parse). Not visually verified.
- Review: 1 P1 auto-fixed, 1 P2 deferred, 1 iteration.

**Deferred:**
- A8c-htf: HTF overlay stepped-vs-smooth when viewInterval matches htfInterval (cosmetic, added to TODO).

**Review concerns flagged:**
- Python smoke test not run (no pandas/uvicorn in sandbox); backend verified by syntax only.
- B23 UI **not visually verified** — Long/Short tabs, regime tab switching, dual-rule backtest flow need human browser review.
- b23_mode + `on_flip = "hold"` behavior: existing position stays open when regime flips; new entries after close use new rule set. This is intentional but worth verifying in browser.

**Next up:** D24 [next] (regime live bot integration — prereq B23 now shipped). Also C18 and C9 are standalone medium tasks.
