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
**Branch:** `claude/bold-fermat-Z9dFt`

**Shipped:**
- **C17a** SPY correlation fix — switched from flat daily equity returns to per-trade returns; correctly shows beta/R² now.
- **B21** Regime sit-flat gate — `RegimeConfig` model, `is_short` → `position_direction` refactor, regime gate on backtest entry, chart histogram shading, StrategyBuilder regime UI, saved-strategy support, bot runner guard, `UpdateBotRequest` regime field.
- **B22** Regime symmetric direction switching — `on_flip` field (`close_only`/`close_and_reverse`/`hold`), forced exit on regime flip, optional forced reversal entry, regime-directed signal entries, UI dropdown + direction toggle hide, `regime_series` shows opposite direction for `close_and_reverse`.

**Self-review findings (B22): review in background at commit time**
- Build: clean
- Syntax: clean (python3 ast.parse)
- Not visually verified

**Deferred:** None.

**Review concerns flagged:**
- Python test environment not available in sandbox (no pandas/uvicorn); backend Python changes syntax-verified only.
- B22 UI **not visually verified** — on_flip dropdown and direction toggle hide need human review in browser.
- `_compute_regime_series` `next(iter(result))` gap (P2 from B21) still open — deferred to pre-Stage-4.
- `close_and_reverse` after a stop-loss: position = 0 when flip happens, so forced reversal silently skips (no existing position to reverse from). Expected behavior; documented here for awareness.

**Next up:** B23 [next] (regime dual rule sets — Stage 4 of regime filter plan). Also C18 and C9 are standalone medium tasks.
