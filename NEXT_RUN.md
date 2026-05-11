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

**Date:** 2026-05-11 (build 25 — overnight)
**Branch:** `claude/jolly-babbage-naH4F`

**Shipped:**
- **F102** [next][easy] `Field(max_length=100)` on `buy_rules`/`sell_rules` for both `QuickBacktestRequest` and `BatchQuickBacktestRequest` in `backend/routes/backtest_quick.py`. Bounds O(n_rules × n_bars) per backtest. 7 new tests in `test_backtest_quick.py` (reject-101 × 4 paths, accept-exactly-100 × 2 boundaries: independent Field declarations on independent Pydantic models warrant independent boundary tests).
- **F104** [easy] `WatchlistRequest._validate_symbols` raises 422 on empty-after-strip — matches `BatchQuickBacktestRequest._validate_symbols` (F91). Side-effect: closes **F87** silent-wipe of `watchlist.json`. Parametrized `test_watchlist_rejects_all_empty_symbols` covers 4 empty-list shapes and asserts on-disk file unchanged.
- **F121** + **F123** New `backend/tests/test_bot_state.py` (6 tests) pinning `BotState.append_slippage_bps` (cap-1000, 2dp rounding, below-cap passthrough) and `BotState.append_equity_snapshot` (cap-500, ISO-8601 UTC, 2dp `value`, order preservation). Non-vacuous assertions distinguish wrong-boundary from no-op-cap regressions.

**Review:** 5 personas round 1 (correctness / testing / adversarial / security / kieran-python) via manual `general-purpose` dispatch with persona-prompt-injection prefix — dedicated `compound-engineering:review:*-reviewer` agents still unresolved in routine env (5th run; F80 unchanged). Round 2 (correctness + adversarial) re-verified the in-PR fixes — clean, no new P0/P1/P2.

**Findings → fix loop applied (6 in-PR):**
- Testing + kieran converged P2 (0.92 + 0.90) → added `test_batch_accepts_exactly_100_rules` for the batch boundary (independent Field declaration on `BatchQuickBacktestRequest`).
- Testing + kieran converged P2 (0.95 + 0.90) → docstring on `test_batch_rejects_more_than_100_sell_rules`.
- Kieran P3 (0.80) → `_stub_rule()` function → `_STUB_RULE` module constant (round-2 adversarial 0.72 disagreed with mutable-global concern; kept the constant since round-2 correctness 0.97 confirmed Pydantic v2 doesn't mutate input dicts).
- Kieran P2 (0.85) → replaced "see comment above" with per-class one-line WHY.
- Kieran P2 (0.75) → trimmed F102 comment to one-line WHY (CLAUDE.md hygiene).
- Testing P3 (0.78) → `@pytest.mark.parametrize` on F104 test for per-case node IDs.

**Deferred → TODO (8 new items, F127–F134):**
- **F127** [medium] Batch quick-backtest request-level deadline (adversarial P1 0.88). Architectural, out of F102 scope; cap-100 + body-cap F86 already mitigate worst-case 100×.
- **F128** [next][medium] Apply `Field(max_length=100)` to remaining 5 sibling models — `StrategyRequest`/`BotConfig` (×6 lists each), `ScanRequest`, `PerformanceRequest`, `RegimeConfig.rules`. (adversarial 0.92 + security 0.90 converged).
- **F129** `Rule.value` unbounded lookback in `signal_engine.py` — pairs with F106. (adversarial 0.80).
- **F130** `compute_indicators` set-dedup gap — varied params bypass dedup. (adversarial 0.75).
- **F131** [easy] Comment on `BotState.append_*` single-coroutine invariant. (adversarial P3 0.70).
- **F132** [easy] Extract `BotState.append_activity_log()` helper — last unbounded-growth gap in BotState. (adversarial P3 0.72).
- **F133** [easy] `scan_signals` raw `str(e)` leak — F115 follow-up. (security P3 0.72).
- **F134** [easy] F104 no-pre-existing-file test variant. (testing P3 0.72).

**Build:** frontend `npm run build` pass (sanity; no FE changes). **Smoke:** AST + import-time substitute (`backend/venv/` still missing in routine container — F97 unchanged 5th run).

**Visual verification:** N/A — backend-only PR.

**Builder env notes:**
1. Dedicated `compound-engineering:review:*-reviewer` agents unresolved (5th run in a row, builds 21/22/23/24/25). `Agent` tool's `subagent_type` field rejects every persona name from the F80 roster; fallback to `general-purpose` with persona-injection prefix continues to work but loses any persona-specific system-prompt scaffolding.
2. `backend/venv/` still missing — F97 unchanged 5th run.
3. **Reviewer disagreement noted:** kieran-python (0.80, R1) wanted `_STUB_RULE` constant; adversarial (0.72, R2) flagged mutable-module-global hazard. Kept the constant on confidence + round-2 correctness's 0.97 confirmation that Pydantic v2 doesn't mutate inputs. If a future `mode='before'` validator mutates the input dict, the `[_STUB_RULE] * N` aliasing would corrupt test inputs — flagged as residual risk for morning review.

**Next up:**
- **F128** [next] Rule-list caps on the 5 sibling models — mechanically identical to F102, medium because of touch-count not complexity.
- **F95** [next] Remaining `SymbolField` rollout on ticker/symbol fields — still blocked on F100 migration prereq.
- **F127** [medium] Batch endpoint request-level deadline — closes the residual unbounded-walltime vector that F102 doesn't address.
- **F129** + **F130** Rule.value lookback cap + compute_indicators dedup gap — both extend F106/F102 hardening surface.

**Previous run:** 2026-05-11 PR #32 (build 24 — F86 + F91 + F94).

## Build 24 Run

**Date:** 2026-05-10 (build 24 — overnight)
**Branch:** `claude/jolly-babbage-4Rfjg`

**Shipped:**
- **F86** [P1] HTTP body size limit middleware — `backend/middleware.py` `BodySizeLimitMiddleware` (pure ASGI). 1 MB default, env-overrideable via `STRATEGYLAB_MAX_BODY_BYTES`. Wired into `main.py` after CORS so it's outermost. Adversarial-hardened in-PR: rejects duplicate Content-Length, forces slow byte-counting when Transfer-Encoding is present (closes CL=0+chunked and TE+CL smuggling), replay-receive returns synthetic empty body on second call to prevent downstream hangs.
- **F91** [P1] BatchQuickBacktestRequest cap parity — 500-entry list cap + `SymbolField` per-element on `BatchQuickBacktestRequest.symbols`; `SymbolField` on `QuickBacktestRequest.ticker`. Direction validators collapsed to `DirectionField`. Closes OOM vector on /api/backtest/quick/batch.
- **F94** Source allowlist on data routes — extracted `require_valid_source(source)` helper to `shared.py` (lowercase + 400). 5 sites refactored: data.py, indicators.py, backtest.py, quote.py ×2. Closes the case-sensitivity gap (`source=YAHOO` now uniformly 400s with "Invalid source").

**Review:** 6 personas round 1 (correctness/testing/adversarial/security/kieran-python/reliability) — dedicated `compound-engineering:review:*` agents still not resolvable in routine env (4th run with this gap; F80 codified). Used `general-purpose` with persona-injected prompts. Round 2 (correctness + adversarial) re-verified smuggling/helper fixes — clean. Findings: 3 P1 + 7 P2 + 13 P3 round 1, all P1s fixed in-PR, ~8 P2s applied, 12 deferred → F102–F113.

**Findings → fix loop applied:**
- **P1 ×3 (adversarial):** middleware request-smuggling. Duplicate Content-Length headers now 400; Transfer-Encoding presence forces slow path overruling declared CL; both fixes covered by new tests.
- **P2 replay-receive double-call hang** (reliability + adversarial converged): synthetic empty-body terminal on second receive call so downstream re-reads can't block on a never-arriving `http.disconnect`.
- **P2 source-check duplication + case sensitivity** (kieran + adversarial converged): extracted `require_valid_source` to `shared.py` (5 sites refactored, lowercases input).
- **P2 DirectionField swap** (kieran 0.95): `direction: str = "long"` + hand-rolled validator → `direction: DirectionField = "long"` on both Quick/Batch request classes; deletes 8 lines.
- **P2 testing — vacuous + boundary gaps** (testing 0.85–0.92): added `received`-list assertion on the 500-symbol acceptance test, exactly-20-char SymbolField test, exactly-at-cap and one-byte-over body-size tests, positive-allowlist tests for /api/indicators and /api/backtest, duplicate-CL test, TE+CL coexistence test, replay-empty-second-call test.

**Deferred → TODO (F102–F113):**
- **F102** [next] cap `buy_rules`/`sell_rules` list length on quick-backtest models (security P2 0.90).
- **F103** `trailing_stop: Optional[dict]` → `Optional[TrailingStopConfig]` (security P2 0.85).
- **F104** harmonize empty-list-after-strip between F69 (silent `[]`) and F91 (422) — reliability P2 0.88.
- **F105** `interval` field allowlist via `Literal[...]` across 5 models (security P3 0.75).
- **F106** allowlist `Rule.indicator` / `Rule.condition` in `signal_engine.py` (security P3 0.65).
- **F107** middleware HEAD/OPTIONS/DELETE method tests (testing P3 0.78).
- **F108** `STRATEGYLAB_MAX_BODY_BYTES` env-var fallback test (testing residual).
- **F109** trim middleware docstring overlap (kieran P3 0.7).
- **F110** ASGI type annotations on `__call__` via `starlette.types` (kieran P3 0.6).
- **F111** reject Content-Length with leading whitespace (adversarial P3 0.65).
- **F112** consider excluding `Transfer-Encoding: identity` from slow-path forcing (round-2 adv P3 0.75).
- **F113** quick-backtest endpoints have no `source` field — yahoo-only by default; add `source` + `require_valid_source` if multi-provider intended (round-2 correctness P3).

**Build:** frontend `npm run build` pass. **Smoke test:** AST + standalone middleware end-to-end smoke via `asyncio.run` (duplicate-CL rejection, TE+CL slow path, replay-empty contract). `backend/venv/` still missing in routine container — F97 pending.

**Visual verification:** N/A — backend-only PR.

**Builder env notes:**
1. **Dedicated `compound-engineering:review:*-reviewer` agents still unavailable** in the routine env — 4th overnight in a row (builds 21 / 22 / 23 / 24). The `Agent` tool's `subagent_type` field rejects every persona name from the F80 roster. Falling back to `general-purpose` with persona role injected at the prompt head; this preserves the persona's *behavioral framing* but loses any persona-specific tool restrictions or system-prompt scaffolding the dedicated agents carry. Worth filing a container-image issue to install / publish the compound-engineering plugin alongside the routine builder so the F80 roster is faithfully reproducible.
2. **`backend/venv/` still missing** — F97 unchanged for the 4th build.

**Next up:**
- **F102** [next] rule-list cap on quick-backtest models — easy, blocked by nothing
- **F95** [next] remaining `SymbolField` rollout (StrategyRequest, BotConfig, ScanRequest, PerformanceRequest) — medium, F100 migration pre-req
- **F104** [easy] harmonize empty-list contract F69 vs F91
- **F107** [easy] middleware HEAD/OPTIONS/DELETE explicit tests

**Previous run:** 2026-05-10 PR #31 (build 23 — F37 + F38 + F70 + F76 + F81 + F85).

## Build 23 Run

**Date:** 2026-05-10 (build 23 — overnight)
**Branch:** `claude/jolly-babbage-KxUMl`

**Shipped:**
- **F81** + **F85** + **F38** Shared `normalize_symbol` + `SymbolField` in `models.py` (regex `^[A-Z0-9][A-Z0-9.\-]{0,19}$`) wired into `routes/quote.py` (path + per-symbol) and `routes/trading.py` (`WatchlistRequest`, `BuyRequest`, `SellRequest` — Buy/Sell adjacencies as P2 reviewer convergence). Tightened over the original spec to require an alphanumeric leading char.
- **F37** Provider allowlist on both `GET /api/quote/{ticker}` and `POST /api/quotes` via `get_available_providers()`. Closes the silent-swallow provider-enumeration vector.
- **F70** `_persist_env` module-level `threading.Lock` wraps the read-modify-write block. New 50-iter concurrent test with `threading.Barrier` + `join(timeout=5)`.
- **F76** `_persist_env` TOCTOU closed via `try: read_text() except FileNotFoundError`; `existed` flag now gates `shutil.copymode` so first-time-write and external-chmod-fail aren't conflated.
- **F45 side-effect close:** invalid-entry echo in `get_quotes` truncated + regex-sanitized.
- **F43 partial close:** Buy/Sell now use `SymbolField`; remaining ticker/symbol fields tracked under F95.

**Review:** 6 manual personas (correctness, testing, adversarial, security, kieran-python, reliability) — first build on the post-F80 4-6 roster (project-standards + maintainability dropped as diff is mostly reuse). 5 P2 + 13 P3 surfaced; all P2s either fixed in-PR or filed as follow-ups (F94/F95/F96). No P0/P1.

**Findings → fix loop applied:**
- Regex tightened (security P2): `^[A-Z0-9][A-Z0-9.\-]{0,19}$` rejects `..`/`.env`/`-A`.
- `_normalize_symbol` renamed → `normalize_symbol` (kieran P2): public name once it's imported across modules.
- Display echo sanitized via `_DISPLAY_CLEAN` regex (3 reviewers): null-byte / control-char strip on invalid-entry echo path.
- F38 path-param test gained a clarifying comment about Starlette's `%3B`→`;` decoding (testing P1 — passes for the right reason, just under-documented).
- F70 concurrent test loops 50× with `Barrier`+`join(timeout=5)` (testing P2): converts probabilistic → deterministic.
- F76 TOCTOU test now explicitly asserts `EXISTING=v not in final` so the crash-safety-over-preservation trade-off is documented (testing P2).
- New `test_watchlist_validation_rejects_invalid_chars` pins the strict-reject contract (testing+reliability P2 — was the "behavior shift, no test" gap).
- `from None` exception suppression in `get_quote`'s `ValueError → HTTPException` (kieran P2).
- `WatchlistRequest._validate_symbols` simplified `isinstance` branch — `normalize_symbol` already type-checks (kieran P3 + correctness P3).
- `_persist_env` cross-process limitation documented in the function docstring (reliability P3).
- `_persist_env` `copymode` split via `existed` bool so first-time-write and concurrent-unlink don't share an exception (reliability + adversarial P3).
- Restored ellipsis on the overlong-symbol error message (correctness P3 cosmetic).

**Deferred → TODO:**
- **F94** [next] F37-style allowlist on `/api/backtest`, `/api/indicators`, `/api/ohlcv` — same enumeration vector class as F37 but separate routes. (security P2)
- **F95** [next] Apply `SymbolField` to `StrategyRequest.ticker`, `BotConfig.symbol`, `QuickBacktestRequest.ticker`, `BatchQuickBacktestRequest.symbols`, `ScanRequest.symbols` — closes F38's coverage on backtest/bot/scan entry points. Subsumes F82. (security + adversarial P2)
- **F96** `get_quotes` calls `get_quote` per-symbol → O(N) redundant `get_available_providers()` lookups + inconsistent error shape if a provider is ever deregistered mid-batch. Refactor: extract a private `_fetch_quote` helper. (kieran + reliability + adversarial P3 convergence)

**Build:** frontend `npm run build` pass. **Smoke test:** AST checks + standalone `normalize_symbol` smoke (verifies `BRK.B`, `BF-B`, 20-char accept; `..`, `.env`, `-A`, `\n`, `;`, `>20`, `''` reject). **`backend/venv/` still missing** in the routine container — same as builds 21 + 22.

**Visual verification:** N/A — backend-only PR.

**Builder env notes:**
1. `ce:review` skill remains unavailable in the routine env (now codified in F80, no probe ceremony this run).
2. `backend/venv/` still missing — F50/F51 still pending.

**Next up:**
- **F94** [next] data routes source allowlist (extends F37) — easy
- **F95** [next] SymbolField on ticker fields — medium, completes F38 coverage
- **F86** [P1] HTTP body size middleware — platform-wide
- **F91** [P1] BatchQuickBacktestRequest cap parity — easy, blocked by F81 (now unblocked)
- **A8** off-screen downsampling — chart perf at wide zoom

**Previous run:** 2026-05-10 PR #30 (build 22 — F69 + F68 + F41).

## Build 22 Run (previously "Last Run")

**Date:** 2026-05-10 (build 22 — overnight)
**Branch:** `claude/jolly-babbage-RosJY`

**Shipped:**
- **F69** `routes/trading.py` `WatchlistRequest` Pydantic length caps (P1 security) — `Field(max_length=500)` + explicit `if len(v) > 500` first-line guard inside `@field_validator` (belt-and-suspenders for Pydantic v2 minor-version drift), per-symbol 20-char cap, strip + uppercase + drop-empties.
- **F68** F52/F53 round-trip + crash-recovery tests — new `tests/test_trading.py` (7 tests) and `tests/test_routes_providers.py` (4 tests). Cleanup tests pre-create the original file and assert it survives the failed `os.replace`. `_persist_env(key, value, env_path=None)` accepts an injected path for testability.
- **F41** `BotDetail.state` type aligned to runtime defensive chains — `state?: BotState` in `frontend/src/shared/types/trading.ts`. Build clean.

**Review:** 9 manual personas (correctness, maintainability, project-standards, reliability, testing, security, adversarial, kieran-python, kieran-typescript). All 3 `ce:review` skill name candidates from the prompt still unavailable — same as builds 20 + 21. F80 stays [next].

**Findings:** ~16 actionable findings across the 9 reviewers, ~6 fixed in-PR (Pydantic belt-and-suspenders, vacuous cleanup test, missing boundary tests, `raise_server_exceptions=False` scope, `default_factory=list` regression, fd.close() leak in both atomic-write sites, style consistency, error-message ordering); 9 deferred → F81–F89.

**Build:** frontend `npm run build` pass. **Smoke test:** N/A — `backend/venv/` still missing in the routine container (build 21 already flagged for human investigation).

**Visual verification:** N/A — type-only frontend change.

**Builder env notes:**
1. **`ce:review` skill names still unavailable** (3rd run in a row) — `compound-engineering:ce-review`, `ce-review`, `ce:review` all fail to resolve. F80 documents the action items; the routine container needs a plugin sync to match the interactive session.
2. **`backend/venv/` missing** — Section 3.5 smoke test in `docs/overnight-builder-prompt-patch.md` requires `backend/venv/bin/uvicorn` and pytest. Every backend-touching run hits this. Either provision the venv in the container or refactor the smoke step to use system python + a constrained import test.

**Next up:**
- **F81** [easy] [next] Shared `SymbolField` type alias — unblocks F82 + F85, dedupes 3 inline call sites.
- **F70** [easy] `_persist_env` lost-update lock — sitting unstarted for two builds.
- **F86** [medium] [P1] HTTP body size middleware — platform-wide; F69's caps are post-parse so multi-GB JSON still hits the parser.
- **A8** [medium] [next] Off-screen downsampling — chart perf at wide zoom.
- **F80** [medium] [next] Two-tier review architecture / debug ce:review skill.

**Previous run:** 2026-05-10 PR #29 (build 21 — F52/F53 atomic writes + F39 quote error field).

## Build 21 Run

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
