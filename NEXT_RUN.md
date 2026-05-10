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

**Date:** 2026-05-10 (build 21 — overnight)
**Branch:** `claude/jolly-babbage-2gq8x`

**Shipped:**
- **F52** `routes/trading.py` `save_watchlist` atomic write — tempfile + `os.replace` + cleanup, mirroring `bot_manager.save()`.
- **F53** `routes/providers.py` `_persist_env` atomic write — same pattern, module-level imports.
- **F39** `POST /api/quotes` per-symbol `error: str | None` — `"invalid symbol"` for validation rejection; `e.detail` for HTTPException paths (strips the leaky `"500: "` status prefix); `str(e).strip() or "no data"` for generic Exception. Frontend `Quote` interface gained `error?: string` and the loading placeholder uses it as a tooltip.

**Review:** 8 manual personas (correctness, maintainability, project-standards, reliability, testing, security, adversarial, kieran-python). All 3 `ce:review` skill name candidates from the prompt still unavailable in this environment — same as build 20. **Builder env note for human:** the `compound-engineering:ce-review` / `ce-review` / `ce:review` skills are not in the available-skills list here; consolidated review is going to keep falling back to manual dispatch until that's debugged.

**Findings summary:** **1 P1 + ~9 P2/P3 actioned**, 11 deferred (filed as F68–F78). All P1s and the high-value P2s fixed before commit.

- **P1 actioned (5/8 reviewers — correctness, security, kieran-python, reliability, adversarial):** `get_quotes` was catching `HTTPException` as `Exception` and calling `str(e)`, which on Starlette returns `"500: upstream timeout"` — leaking the HTTP status prefix into the public `error` field. Added a dedicated `except HTTPException as e: ... e.detail` clause. Without the fix, the new `test_fetch_exception_returns_error_field` test would have failed at runtime (asserted `"upstream timeout"` but actual would be `"500: upstream timeout"`).
- **P2 actioned:** function-local `os/pathlib/tempfile` imports in `providers.py` hoisted to module top (4 reviewers flagged); `str(e) if str(e) else "no data"` simplified to `str(e).strip() or "no data"` (also handles whitespace-only exception messages); frontend `Quote` interface picked up `error?: string` and `title` tooltip on the `...` placeholder; added `test_fetch_empty_exception_message_falls_back` and `test_no_data_dataframe_uses_404_detail` to lock in the new error-field semantics; added `assert "error" not in body[0]` on the success path.
- **Deferred → TODO (F68–F78):** F68 round-trip+crash tests for atomic writes, F69 `WatchlistRequest` disk-fill DoS (Pydantic length cap), F70 `_persist_env` lost-update lock, **F71 atomic_write_text shared helper across the now-4 sites (umbrella for the OSError-guard divergence + bare-except in `bot_manager.py`)**, F72 Pydantic `response_model` for `/api/quotes`, F73 orphan `.tmp` cleanup on startup, F74 missing `fsync` before `os.replace`, F75 sanitize internal exception messages reflected via the new `error` field, F76 `_persist_env` `.env` exists/read TOCTOU, F77 newline guard on `_persist_env(key,value)`, F78 watchlist UI red-tint indicator for permanently-failed quotes (tooltip is wired but no visual distinction yet).
- **Reviewers' "fix journal.py / bot_manager.py to match the new code" directives:** declined for this PR — the new code is strictly more defensive (OSError guard around `os.unlink`, `except Exception:` over bare `except:`). Updating the old sites is the right call but it's exactly what F71's helper extraction will do atomically across all four sites; landing it piecemeal here would temporarily diverge the patterns again. Tracked under F71.

**Build:** frontend `npm run build` pass. **Smoke test:** N/A — no Python venv in this environment, AST checks only. The runtime smoke-test step in `docs/overnight-builder-prompt-patch.md` Section 3.5 assumes `backend/venv/bin/uvicorn` exists; flagging because every overnight run that touches backend code is going to hit this until the venv is provisioned in the routine container.

**Visual verification:** Not visually verified — the only frontend change is one type field + one `title` attribute. Flagged in PR description.

**Previous run:** 2026-05-09 PR #28 (build 20 — A14a SubPane loading + C25a optimizer NaN guard).

**Next up:** F68 (round-trip tests for F52/F53) [easy][next], F41 (BotDetail.state type mismatch) [easy][next], F69 (WatchlistRequest disk-fill cap) [easy], F70 (_persist_env lock) [easy], F71 (atomic_write_text helper) [medium], A8 (off-screen downsampling) [medium].

## Build 20 Run

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
