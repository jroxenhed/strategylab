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

**Date:** 2026-05-01
**Branch:** `claude/overnight-2026-05-01`

**Shipped:**
- **C13** Monte Carlo bug fix — `min_equity` percentile stats replace misleading `final_value` (identical across shuffles); MonteCarloChart color semantics corrected (p5=worst/red, p95=best/green)
- **C14** Trade duration histogram — `TradeHoldDurationHistogram.tsx`, "Hold Time" tab in Results, win/loss bucket coloring, avg hold time split
- **D21** Strategy auto-pause on drawdown — `BotConfig.drawdown_threshold_pct`, `_tick()` check at both exit paths, state cleanup on pause, `notify_error` fire-and-forget; AddBotBar + BotCard UI

**Self-review findings: 2 (P0: 0, P1: 2, P2: 0), 2 auto-fixed, 1 iteration**
- P1: MonteCarloChart min_equity color labels inverted — auto-fixed
- P1: First auto-pause branch missing state cleanup before return — auto-fixed

**Deferred:** None. All 3 `[next]` items shipped.

**Review concerns flagged:** None.

**Next up:** C15 (win/loss streak analysis), C16 (Kelly sizing), D22 (trade journal CSV export) — all tagged [easy]
