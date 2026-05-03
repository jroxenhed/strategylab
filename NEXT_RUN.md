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

**Date:** 2026-05-03 (build 8)
**Branch:** `claude/sharp-allen-HWr4u`

**Shipped:**
- **F22** `was_running` badge on BotCard — amber "⚡ Was running" badge in compact + expanded layouts; also fixes stale-badge bug by resetting `was_running=False` in `BotRunner.run()`.
- **C18b** Sensitivity sweep sparkline — SVG mini line chart above SensitivityPanel results table, `total_return_pct` vs `param_value`, teal/red dots, zero-baseline, `preserveAspectRatio="none"`.
- **A8c-htf** HTF overlay line type fix — `LineType.WithSteps` only when `viewInterval !== htfInterval`; `viewInterval` added to overlay series effect deps.

**Review findings:**
- 3 parallel reviewers (correctness, integration, standards+robustness)
- Review: 2 P2 findings, 4 P3 findings. Both P2s auto-fixed.
- P2 (F22): `was_running` never reset on start → fixed in `bot_runner.py:run()`.
- P2 (C18b): Missing `preserveAspectRatio="none"` on SVG → fixed.
- P3s deferred: badge doesn't show for `status=error` bots (product decision); case-sensitivity note on interval comparison (currently safe); flat-line sparkline renders at bottom not center (cosmetic).
- Syntax: `ast.parse` clean on all backend files. `npm run build` passes.

**Deferred:**
- F21: Split bot_runner.py — still deferred; needs project venv to run the test harness for refactor verification.
- D24b: Regime bot visual verification — needs live paper-trading QA.
- D24d: HTF cache staleness — 1-hour TTL can lag regime direction.
- F22-badge for error-status bots — P3, product decision whether errored+was_running bots should show badge.

**Review concerns flagged:**
- F22 badge not visually verified — check BotCard compact+expanded with a bot that has `was_running=true` in bots.json.
- C18b sparkline not visually verified — run a sweep to confirm chart renders above table.
- A8c-htf not visually verified — toggle a 1D HTF MA overlay with `viewInterval=1d` to confirm smooth line.

**Next up:** C20 [easy] (equity curve regression — priority fix), C21 [easy] (sweep param bug).
