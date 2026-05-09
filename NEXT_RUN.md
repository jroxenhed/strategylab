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

**Date:** 2026-05-09 (build 20 — overnight)
**Branch:** `claude/jolly-babbage-hh8n4`

**Shipped:**
- **A14a** SubPane loading state — `useInstanceIndicators` exposes `isLoading` aggregated across all active queries (regular + htf groups), threaded `App → Chart → SubPanelEntry → SubPane`. Overlay uses a constant-opacity scrim with the animated `<span>` only (backdrop pulse caused chart-content flicker).
- **C25a** Optimizer NaN guard improvements — split `runOptimizer()` validation into per-field checks; `isNaN(stepsN)` guard fires before `< 2`; "system default ... is missing" message when blank field meets a NaN default.

**Review:** 4 manual personas (correctness, maintainability, project-standards, kieran-typescript). The `compound-engineering:ce-review` skill name from `docs/overnight-builder-prompt-patch.md` is not in the available-skills list for this environment — fell back to manual persona dispatch. Plain `review` skill IS listed but its description ("Review a pull request") suggests it expects an existing PR; was not used. **Builder env note for human:** debug the ce-review skill alias / install state so future runs can use the consolidated Skill call.

**Findings summary:** 2 P2 + 6 P3 across both tasks.
- Fixed: P2 `useOHLCV.ts` `isLoading` semantics (only-first-query bug + empty-success lock); P2 `SubPane.tsx` animation flicker (moved animation off backdrop); P3 inline rgba/hex colors in SubPane (now use existing `CHART_BG_SCRIM`/`TEXT` constants).
- Deferred (added to TODO): **C25b** (OptimizerPanel submission `parseInt(p.steps) || 5` diverges from validation), **A14d** (loading flag is per-Chart not per-pane — fine today, revisit if HTF groups split), **F47** (section-header comment noise in SubPane.tsx + OptimizerPanel.tsx — pre-existing, CLAUDE.md "no comments" violation), **F48** (`steps`/`stepsN` naming inconsistency across 3 scopes in OptimizerPanel.tsx).
- Skipped: P3 helper extraction in OptimizerPanel (advisory, current form is clear enough); P3 system-default NaN dead-code claim (TS reviewer; defensive code aligns with original C25a spec — disagree with finding); P3 inline animation string CSS coupling (advisory).

**Build:** pass. **Smoke test:** N/A (frontend-only changes).

**Visual verification:** Not visually verified — flagged in PR description.

**Previous run:** 2026-05-09 PR #27 (build 19 — F33/F30/F29 dedup tests + quote endpoint coverage). Before that: 2026-05-09 PR #26 review fixes (F28e + dead import).

**Next up:** F39 (batch quote silent null) [easy][next], F41 (BotDetail.state type mismatch) [easy][next], F40/F42/F43 housekeeping batch [easy], C25b (Optimizer submission divergence) [easy], A14d (per-pane loading map) [medium], D27a (status tooltip popover) [medium], A8 off-screen downsampling [medium].
