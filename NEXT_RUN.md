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

**Date:** 2026-05-02
**Branch:** `claude/overnight-2026-05-02`

**Shipped:**
- **A13a** Multi-TF data foundation — `fetch_higher_tf()`, `align_htf_to_ltf()`, `htf_lookback_days()` in `backend/shared.py`. Anti-lookahead via `shift(1)` + UTC normalization + `merge_asof(backward)`. 6/6 tests in `test_htf_alignment.py`. Prereq for A13b, B21, D24.
- **C15** Win/loss streak analysis panel — `streakUtils.ts` + `StreakPanel.tsx`, inserted in Summary tab. Max consecutive wins/losses, avg streak, mini distribution charts.
- **D22** Verified already shipped in D13 (exportCsv at TradeJournal.tsx:140). Checked off.

**Self-review findings: 0 (P0: 0, P1: 0, P2: 0), 0 auto-fixed, 0 iterations**

**Deferred:** None. All 3 tasks shipped (A13a was the [next] tag, C15 + D22 were the NEXT_RUN suggestions).

**Review concerns flagged:** None.

**Next up:** A13b (multi-TF indicator overlay, prereq A13a now done), C16 (Kelly criterion sizing), C17 (SPY correlation/beta) — suggest tagging A13b [next] since A13a just landed
