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

**Date:** 2026-05-07 (build 13)
**Branch:** `claude/sharp-allen-lpvXd`

**Shipped:**
- **B5** Borrow cost for live shorts — `borrow_rate_annual` on `BotConfig`, `entry_time` on `BotState`, borrow cost computed at exit using backtest formula, stored in journal.
- **B8** Live spread display — `/api/slippage/{symbol}` returns `live_spread_bps`/`half_spread_bps`, shown in StrategyBuilder next to modeled slippage.

**Review findings:**
- 2 findings (P0: 0, P1: 0, P2: 1, P3: 1). 0 auto-fixed.
- P2: B8 live spread is informational only (not auto-applied to modeled_bps). Deliberate design choice — auto-apply would make backtests non-deterministic across runs.
- P3: `entry_time` not explicitly cleared in exit cleanup paths. Harmless (guarded by `entry_price` null check). Cosmetic.
- Build: `npm run build` passes. `ast.parse` passes on all 6 changed backend files.

**Not visually verified:**
- B8: Live spread display in StrategyBuilder — not visually verified (no browser). Verify: open StrategyBuilder with Alpaca broker configured, observe "live spread: X bps (½: Y)" next to slippage input.

**Concerns for human review:**
- B8 auto-apply: if the intent is to auto-default `modeled_bps` to `half_spread_bps`, a follow-up could add a "Use live spread" button that pre-fills slippage from the live quote. Discussed as B8-follow-up in TODO suggestions.

**Deferred:**
- A8 viewport-only rendering — not attempted. Complex risk for an overnight run (many `setData()` call sites across Chart.tsx + SubPane.tsx).
- D24b — manual QA only, requires browser.
- B20 multi-TF confirmation — large, needs design.

**Suggested new TODO items (add if relevant):**
- B8-follow-up: "Use live spread" button in Capital & Fees that pre-fills slippage_bps from half-spread [easy]
- B5-follow-up: Show borrow_cost in TradeJournal UI as a column (currently stored in JSON but not displayed) [easy]
- B9 partial: start with margin interest only (simplest sub-item of B9) [medium]

**Next up:** C22 visual verification (manual QA), D24b (regime bot visual verification), A8 viewport-only rendering [medium], or B5-follow-up [easy].
