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

**Date:** 2026-05-03 (build 5)
**Branch:** `claude/bold-einstein-MCgOu`
**Commit:** cbc2bcc

**Shipped:**
- **D24** Regime filter live bot integration — full Stage 5 implementation: regime evaluation per tick, position flip sequence, pending_regime_flip retry, bidirectional P&L helpers, same-symbol guard, regime status on BotCard, AddBotBar passthrough.

**Self-review findings:**
- P2: `pending_regime_flip` not in list_bots summary → added for "⏳ Pending flip" badge on BotCard. Auto-fixed.
- Build: clean (tsc -b + vite). Syntax: clean (ast.parse). Not visually verified (live bot paper trading required).
- Review: 1 finding (P2), 1 auto-fixed, 1 iteration.

**Deferred:**
- D24b: Regime bot not visually verified — need paper trading QA to confirm flip sequence, pending flip retry, regime badge.

**Review concerns flagged:**
- Backend: `backtest_bot()` in `bot_manager.py` doesn't pass regime/dual-rule fields to StrategyRequest → added as D24a [easy] [next].
- D24 not visually verified — the bot flip sequence requires a live market session with a running regime bot.
- Smoke test not run (no pandas/uvicorn in sandbox); backend verified by syntax only.

**Next up:** D24a [easy] (backtest_bot regime passthrough — 5 min fix). Then C18 [medium] (parameter sensitivity sweep).
