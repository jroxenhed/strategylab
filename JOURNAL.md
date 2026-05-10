# Journal

What we've actually shipped. Reverse-chronological, one section per working day. Bold IDs (e.g. **[D10](TODO.md#d--bots-live-trading)**) cross-reference [TODO.md](TODO.md).

> **Maintenance rule (Claude):** append an entry at the end of any session that produces durable work — TODO closures, features, bug fixes, discoveries. Skip routine commits (typo fixes, reformatting). Keep bullets short; link to the commit or doc if more context is worth a click. Don't re-read every TODO to write an entry — just log what happened in the session.

## 2026-05-10 (build 22 — overnight)

- **[F69](TODO.md#f--architecture--housekeeping)** `WatchlistRequest` length caps (P1 security) — `routes/trading.py` now declares `symbols: list[str] = Field(max_length=500)` plus a `@field_validator` that explicit-checks `len(v) > 500` first (belt-and-suspenders against any Pydantic v2 list-`max_length` semantic drift), strips whitespace + uppercases, drops empties, enforces 20-char per-symbol cap. Closes the unauthenticated disk-fill / OOM DoS path that was the F69 raison d'être.
- **[F68](TODO.md#f--architecture--housekeeping)** Round-trip + crash-recovery tests for F52/F53 — `backend/tests/test_trading.py` (7 tests: round-trip, 501-symbol 422, 21-char 422, boundary 500, boundary 20-char, strip/uppercase/empty-filter, cleanup-on-`os.replace`-failure that pre-creates `{"symbols": ["ORIG"]}` and asserts the original survives). `backend/tests/test_routes_providers.py` (4 tests: create / update-in-place / append / cleanup-on-failure with original `.env` preservation). `_persist_env` gained `env_path: Optional[pathlib.Path] = None` so tests can redirect without monkeypatching `__file__`.
- **[F41](TODO.md#f--architecture--housekeeping)** `BotDetail.state` type aligned to runtime — `state: BotState` → `state?: BotState` in `frontend/src/shared/types/trading.ts`. All 9 consumer sites in `BotCard.tsx` already used `detail?.state?.X` defensive chains (PR #26 / F32), so no other files needed updating. `npm run build` clean.
- **Review:** 9 manual personas (correctness/maintainability/project-standards/reliability/testing/security/adversarial/kieran-python/kieran-typescript). All 3 `ce:review` skill name candidates remain unavailable in the routine env — same as builds 20 + 21. **Builder env note:** F80 (codify two-tier review + debug skill resolution) stays [next].
- **Findings actioned (P0: 0, P1: 1 with sub-confirms, P2: 6, P3: deferred):**
  - Reliability + security (5/9 reviewers, varying severity) flagged Pydantic v2 list `max_length` reliability — pydantic docs confirm it works, but the explicit `if len(v) > 500` first-line guard in `_validate_symbols` is strictly more defensive and version-independent. Applied.
  - Testing P1 + correctness P3 + reliability P3 (3 reviewers) — `test_watchlist_cleanup_on_replace_failure` was vacuous (file never pre-existed). Now writes `{"symbols": ["ORIG"]}` first and asserts the original survives the failed `os.replace`.
  - Testing P1 — boundary tests (exactly 500 symbols, exactly 20-char) added.
  - Testing + maintainability + project-standards + kieran-python (4 reviewers) — `raise_server_exceptions=False` on the shared `client` fixture masked unexpected 500s in 5 tests. Scoped to a `local_client` inside the cleanup test only; shared fixture is now bare `TestClient(app)`.
  - Correctness P2 — `Field(default_factory=list, max_length=500)` silently made `symbols` optional (POST `{}` would have 200'd). Dropped `default_factory` to restore "field required".
  - kieran-python P2 — `pathlib.Path | None` style inconsistent with codebase's `Optional[T]`. Switched to `Optional[pathlib.Path]`.
  - kieran-python P3 — error message referenced stripped (not uppercased) symbol; reordered `sym.strip().upper()` before length check so the validator's view is consistent.
  - Adversarial P2 (both atomic-write sites) — `fd.close()` in the except branch wasn't try/except-wrapped; on NFS / disk-full where the kernel defers I/O errors to `close()`, the subsequent `os.unlink(fd.name)` would never execute → `.tmp` leak. Wrapped in both `trading.py` `save_watchlist` and `providers.py` `_persist_env`. Filed F83 to fold the same fix into `journal.py:195` + `bot_manager.py:561` via the F71 `atomic_write_text` helper rather than fixing two more sites in isolation.
- **Deferred → TODO (F81–F89):** F81 shared `SymbolField` type alias (unblocks F82+F85), F82 `ScanRequest.symbols` parity validation, F83 fd.close leak in remaining 2 atomic-write sites, F84 `BotCard.test.tsx` for `state: undefined` path, F85 symbol allowlist regex, F86 HTTP body size middleware (P1, platform-wide), F87 empty-list watchlist silent wipe, F88 GET `/watchlist` schema asymmetry, F89 TestClient lifespan side-effects.
- **Build:** frontend `npm run build` pass. **Smoke test:** N/A — `backend/venv/` still doesn't exist in the routine container (build-21 NEXT_RUN flagged this; F50 / F51 follow-ups pending).
- **Visual verification:** N/A — only frontend change is a single type-field annotation.

## 2026-05-10 (build 21 — overnight)

- **[F52](TODO.md#f--architecture--housekeeping)** Watchlist atomic write — `routes/trading.py` `save_watchlist` now uses `tempfile.NamedTemporaryFile(delete=False, dir=WATCHLIST_PATH.parent)` + `os.replace` with cleanup-on-exception, mirroring `bot_manager.save()`. Closes the F14/F16-class atomicity gap surfaced in the architectural audit.
- **[F53](TODO.md#f--architecture--housekeeping)** `.env` atomic write — `routes/providers.py` `_persist_env` switched to the same tempfile + `os.replace` pattern. Module-level `os` / `pathlib` / `tempfile` imports replaced the prior function-local imports (4 reviewers flagged the inconsistency with `journal.py` / `bot_manager.py` / `trading.py`).
- **[F39](TODO.md#f--architecture--housekeeping)** Batch quote `error` field — `POST /api/quotes` per-symbol response gained `error: str | None`. Validation rejection emits `"invalid symbol"`. The exception path now catches `HTTPException` separately and surfaces `e.detail` (without the `"500: "` status prefix that `str(HTTPException)` would leak — caught by the correctness reviewer; my pre-fix exception-path test would have failed at runtime). Generic `Exception` falls back to `str(e).strip() or "no data"`. Frontend `Quote` interface gained `error?: string` and the loading placeholder now renders the error message as a `title` tooltip.
- **Tests:** `test_quote_endpoint.py` updated for the new shape; added `test_fetch_exception_returns_error_field` (RuntimeError → 500 detail surfaced clean), `test_fetch_empty_exception_message_falls_back` (empty exception → `"no data"`), `test_no_data_dataframe_uses_404_detail` (asserts the `"404:"` prefix is stripped), and `assert "error" not in body[0]` on the success path to lock in the no-error-key invariant.
- **Review:** 8 manual personas (correctness/maintainability/project-standards/reliability/testing/security/adversarial/kieran-python). `ce-review` skill names still unavailable. Build 20's NEXT_RUN flagged this for human investigation; until resolved, manual dispatch remains the path. **1 P1 + 9 P2/P3 actioned**, 11 deferred to **F68–F78**. The P1 (HTTPException leakage + dead test) was caught by 5/8 reviewers; single-pass self-review would have shipped a broken test.
- **Morning ce:review pass (interactive, 11 personas via skill):** caught a P1 the overnight builder missed (threadpool cascade — serial `_fetch()` in 20-symbol batch endpoint + no yfinance timeout, deferred for now), reclassified F69 P2→P1 (concrete OOM/disk-fill DoS on unauthenticated POST, not generic hardening), and applied 4 safe fixes inline: `fd.flush(); os.fsync(fd.fileno())` before `os.replace` at both new atomic-write sites (closes F74 in this PR's scope), `e.detail` `isinstance(str)` guard so list/dict details from Pydantic don't leak through the new `error` field, deletion of the structurally-dead `except Exception` branch in `get_quotes` (5/11 reviewers — `get_quote` always wraps to `HTTPException` first), and removal of duplicate `import pandas as pd` inside one test. 8/8 backend tests still pass; `npm run build` clean.

## 2026-05-10 (architectural audit + pipeline calibration)

- **Architectural audit at 155/202** — first audit since 2026-05-03 (was at 91/127); 64 items shipped past the ~20-item recommended cadence. Sonnet agent ran data integrity + error handling + concurrency + test coverage + perf + security passes. Surfaced **5 P1s** (atomicity gaps in watchlist/.env writes, missing `direction` on scan `_log_trade`, sync `bot_manager.save()` blocking event loop, unbounded `slippage_bps` list) and **10 P2/P3s** (test coverage gaps in exits/regime/bot_manager, journal O(n) reads per tick, file-size overruns, silent UI error swallows, etc.). Filed as **F52–F67**. F52 + F53 tagged `[next]` — both are 5-line fixes using existing patterns.
- **Confirmed clean:** journal/bots.json atomicity, `compute_realized_pnl` bot_id scoping, no stray `yfinance.download()`, `LogicField`/`DirectionField` coverage, equity_snapshots/activity_log caps, async cleanup, fire-and-forget notification pattern. Audit doc'd these so the next pass knows what's already verified.
- **Overnight pipeline calibration** — promoted `reliability` + `testing` + `security` + `adversarial` to always-on personas in `docs/overnight-builder-prompt-patch.md` (each with evidence trail in the prompt comment). Lowered max-tasks-per-run from 5 to 3 to fit the longer review cycle. Added pre-flight checks (no parallel builder PR, fresh main, TODO freshness). Added `ce:review` skill probe with 3 candidate names + manual dispatch fallback. Routine model upgraded Sonnet 4.6 → Opus 4.7. Branch-naming dead-code removed.
- **Memory + CLAUDE.md refresh** — rewrote `project_overnight_builder_session.md` (was almost entirely stale: Sonnet 4.6, 5-pass review, max 5, etc.); softened `feedback_ce_review_only.md` from "never hand-roll" to "prefer ce:review when available, manual is a documented fallback" based on 11+ clean dispatches across this session; updated `feedback_ce_review.md` with the 7-persona always-on roster. Trimmed CLAUDE.md model-routing rule (Opus 4.7 default for orchestrator), softened the "never hand-roll" overnight-review rule, replaced the stale 2026-05-02 Slack template with a generic placeholder.
- **F50 / F51 clarification** — F50 explicit about port 4173 vs 5173 (preview vs dev) so the builder doesn't "fix" what looks like a typo.
- **Branches cleaned** — 4 merged branches deleted local + remote (PR #21, #23, #25, #26 sources). `.context/` and `.memsearch/` added to `.gitignore` after PR #27 swept them in; 37 artifact files untracked.

## 2026-05-09 (build 20 — overnight)

- **[A14a](TODO.md#a--charts--indicators)** SubPane loading state — `useInstanceIndicators` exposes `isLoading` aggregated across all active queries (`allQueries.some(q => q.isLoading)` over `regularQuery` + every `htfQueryResults` entry). Replaces the earlier hand-rolled `fetchStatus === 'fetching' && !merged-keys` condition that (a) only saw the first query branch and (b) locked at true on a successful empty response. Threaded a single `instanceLoading: boolean` from `App.tsx → Chart.tsx → SubPanelEntry → SubPane.tsx`. SubPane renders a constant-opacity scrim (`CHART_BG_SCRIM`) with the animated `<span>` only — putting `chart-skeleton-pulse` on the backdrop made the chart underneath flicker.
- **[C25a](TODO.md#c--strategy-summary--analytics)** Optimizer NaN guard improvements — `runOptimizer()` validation split into per-field checks. (a) "Min/Max/Steps is not a valid number" identifies the offending field (was: generic "Min and Max must be valid numbers"). (b) `isNaN(stepsN)` guard fires before `< 2` so non-numeric Steps no longer surfaces as the misleading "Steps must be at least 2". (c) Distinguishes user-typed NaN from system-default NaN: when user leaves the field blank and `opt.defaultMin/Max` is NaN, error reads "system default Min is missing — enter a value manually" rather than blaming the user.

**Review:** 4 manual personas (correctness/maintainability/project-standards/kieran-typescript) — `compound-engineering:ce-review` skill not in this environment's skill list, fell back to manual dispatch. 2 P2 + 6 P3 surfaced; both P2s + 1 P3 fixed (`useOHLCV.ts` isLoading semantics, SubPane animation/colors). Deferred P2 to **C25b** (OptimizerPanel submission path divergence) and 3 P3s to **A14d/F47/F48**. Build: pass.

## 2026-05-09 (build 19 — overnight, PR #27)

*This run started in parallel with PR #26's review session. F29/F30 were already shipped in PR #26, so PR #27 contributed F33 fix, improved dedup tests, and new F29 test coverage. F37/F38 items surfaced by the PR #27 builder were renumbered to F45/F46 to avoid collision with main's F37/F38.*

- **[F33](TODO.md#f--architecture--housekeeping)** `TestTickStateTransitions` fetch-path audit — patched `shared.fetch_ohlcv_async` (AsyncMock) in `_base_patches` of `test_bot_runner.py`. The old `bot_runner._fetch` patch did not intercept the actual call path since F26 moved bots to `fetch_ohlcv_async`. All 9 tests pass.

- **[F30](TODO.md#f--architecture--housekeeping)** Improved dedup test coverage — `TestFetchOhlcvAsyncDedup` replaced with a more thorough version: concurrent dedup test (two simultaneous calls share one Future) + sequential independence test. Removed stale `bot_runner._fetch` mock.

- **[F29](TODO.md#f--architecture--housekeeping)** Quote endpoint test coverage — added `test_quote_endpoint.py` with 5 tests for the `GET /api/quote/{ticker}` and `POST /api/quotes` routes (valid ticker, empty ticker, oversized ticker, batch validation, batch dedup).

## 2026-05-08 (build 19 — overnight)

- **[F29](TODO.md#f--architecture--housekeeping)** Batch `/api/quotes` ticker validation — added `sym = sym.strip().upper()` + empty/length guard at the top of the batch loop in `routes/quote.py`. Previously raw symbols (with whitespace, excessive length) passed through to `get_quote()`; now they short-circuit before hitting `_fetch()`.

- **[F30](TODO.md#f--architecture--housekeeping)** `fetch_ohlcv_async` dedup coverage — added `TestFetchOhlcvAsyncDedup` class to `test_bot_runner.py` with two tests: (1) concurrent dedup — two simultaneous `gather` coroutines on the same key share one `_fetch` Future (verified via `slow_fetch` with `time.sleep(0.05)` to keep Future pending); (2) sequential independence — two sequential awaits each invoke `_fetch` independently after the Future is cleaned up.

- **[F28d](TODO.md#f--architecture--housekeeping)** `StrategyRequest.direction` validator — added `@field_validator('direction')` to `StrategyRequest` in `models.py` restricting to `"long" | "short"`. Matches existing pattern in `QuickBacktestRequest` / `BatchQuickBacktestRequest`. Closes the F28 validation pass for the main backtest model.

- **[F31](TODO.md#f--architecture--housekeeping)** `eval_rules()` `Literal` type annotation — changed `logic: str` to `logic: Literal['AND', 'OR']` in `signal_engine.py`. Added `Literal` to the `from typing` import. No runtime change; provides type-checker enforcement at the engine sink.

- **[F32](TODO.md#f--architecture--housekeeping)** BotCard.tsx unsafe optional chains — changed all 8 occurrences of `detail?.state.X` to `detail?.state?.X` in `BotCard.tsx` (covers: `last_tick` ×2, `equity_snapshots` ×4, `activity_log` ×2, `pause_reason` ×2). Prevents `TypeError` when `detail` is truthy but `state` is transiently undefined during first detail poll.

## 2026-05-08 (PR #25 review fixes)

Multi-agent review of build 18 (A14, D27, F28b, F28c, C25) via `ce:review`. Found 1 P1 + 8 P2 + 8 P3.

- **P1 fixed — startup cascade:** `Literal['AND','OR']` with `BeforeValidator` applied across all 8 models using a shared `LogicField` type alias, preventing any non-`AND|OR` string from reaching `eval_rules()`.
- **P2 fixed — 3 missed models:** `ScanRequest`, `RegimeConfig`, `PerformanceRequest` now include `buy_logic`/`sell_logic` validation (were missed in the F28b pass).
- **P2 fixed — duplicate validators eliminated:** 5 duplicate `validate_logic` methods replaced by the `LogicField` type alias (DRY pass across models).
- **P2 fixed — App.tsx isError branch:** network failure now shows "Failed to load" instead of the misleading "No data" path.
- **P2 fixed — cache_info star-unpack:** `shared.py` cache_info now uses `*rest` unpacking for resilience against future key growth.
- **P3 fixed — cache_info +ext flag:** `GET /api/cache` display includes `+ext` flag when `extended_hours=True`.
- **P3 fixed — BotCard lastTickStr dedup:** removed 3 duplicate IIFEs, reused single `lastTickStr` variable throughout.

Deferred findings added to TODO.md: A14b (skeleton stale-cache), A14c (ChartSkeleton location), C25a (NaN guard improvements), F31 (eval_rules defense-in-depth), F32 (BotCard unsafe optional chain).

## 2026-05-08 (build 18 — overnight)

- **[A14](TODO.md#a--charts--indicators)** Chart loading skeleton — extracted `isLoading` from `useOHLCV` React Query hook in App.tsx. While loading with no cached data, shows a `ChartSkeleton` component: 20 CSS-animated pulsing grey bars mimicking candlestick columns, plus a ticker label. After loading with no data, shows "No data for {ticker}" (was always "Loading..."). CSS keyframe `chart-skeleton-pulse` added to `index.css`. Not visually verified.

- **[D27](TODO.md#d--bots-live-trading)** Bot status tooltip — added native `title` attribute to the status badge in both compact and expanded BotCard layouts. Tooltip shows: status, P&L (amount + %), in/out-of-position, and last tick time via `fmtTimeET`. Uses the same `detail?.state.last_tick ?? summary.last_tick` fallback as the heartbeat dot. Not visually verified.

- **[F28b](TODO.md#f--architecture--housekeeping)** `buy_logic`/`sell_logic` validation — added `@field_validator` restricting to `"AND" | "OR"` across all relevant models: `QuickBacktestRequest`, `BatchQuickBacktestRequest` (backtest_quick.py), `StrategyRequest` (models.py — all 6 logic fields), `BotConfig` (bot_manager.py — all 6 logic fields), and `UpdateBotRequest` (routes/bots.py — Optional fields, None-safe guard). Previously any string silently fell back to OR semantics.

- **[F28c](TODO.md#f--architecture--housekeeping)** `cache_info()` 6-tuple crash fixed — `cache_info()` in shared.py was unpacking keys as 5-tuples `(ticker, start, end, interval, source)` but keys are 6-tuples that include `extended_hours`. Every `GET /api/cache` call raised `ValueError`. Fixed by unpacking as `(ticker, start, end, interval, source, _ext)`.

- **[C25](TODO.md#c--strategy-summary--analytics)** Optimizer param NaN handling — added `isNaN(minN) || isNaN(maxN)` check before the existing `min > max` guard in `runOptimizer()` (OptimizerPanel.tsx). Catches non-numeric input like `'abc'` that `parseFloat` silently converts to NaN, which previously produced an all-NaN linspace with no visible error.

## 2026-05-08 (build 17 — overnight)

- **[B10](TODO.md#b--strategy-engine--rules)** TradeJournal CSV quoting — replaced bare `.join(',')` with a `csvField()` RFC 4180 helper (wraps fields containing commas, double-quotes, or newlines). Applied to both header row and all data rows. Prevents column misalignment for timestamps like "May 8, 2026 10:30 AM" that contain a comma.

- **[D26](TODO.md#d--bots-live-trading)** FundBar invalid input feedback — `FundBar` now validates input in `handleSet()` and shows an inline error message (red border + descriptive text) for empty, non-numeric, or negative values. Previously silently no-opped.

- **[C23](TODO.md#c--strategy-summary--analytics)** Optimizer validation — `runOptimizer()` validates `min ≤ max` and `steps ≥ 2` for each active param row before setting the loading spinner. Descriptive error includes the param label. Prevents sending nonsensical sweep grids to the backend.

- **[F28](TODO.md#f--architecture--housekeeping)** Backend input validation hardening — Pydantic `Field` constraints across six request models: `QuickBacktestRequest`/`BatchQuickBacktestRequest` (capital gt=0, lookback gt=0, stop_loss ge=0, direction enum, ticker strip+length), `SetFundRequest` (amount ge=0), `UpdateBotRequest` (capital gt=0, spread/drawdown/borrow ge=0, direction enum), `BuyRequest` (qty gt=0, stop_loss_pct ge=0 le=100), `SellRequest` (qty gt=0 when set), `get_quote` (empty/oversized ticker 400 response).

- **[F26](TODO.md#f--architecture--housekeeping)** Shared OHLCV cache — added `fetch_ohlcv_async()` to `shared.py`: async wrapper around `_fetch()` that deduplicates concurrent bot coroutines at the asyncio Future level. Multiple bots awaiting the same symbol/interval/date range share one `run_in_executor` Future. Safe under asyncio cooperative scheduling. bot_runner now calls `fetch_ohlcv_async()` instead of `_run_in_executor(_fetch, ...)`.

## 2026-05-08 (interactive review session)

- **PR #21 review + merge** (B5a + B8a + B8b) — overnight builder PR: borrow cost journal column + live spread button. 3-agent review caught 2 P1s: spread-derived slippage overwritten by 60s auto-refresh, button shown at zero spread. Fixed both + added "↩ modeled" reset button (UX gap found during visual verification). B8b (auto-reset guard) resolved as part of P1 fix. Added B5c to TODO (bot runner doesn't pass borrow_cost to log_trade).

- **PR #18 review fixes** (F19 + C22) — 12-agent multi-persona review caught 15 findings. Fixed: optimizer 500-error swallowing (P1), 60s wall-clock timeout with `timed_out` response field, 18 backend tests, drag-reorder optimistic update regression, botsError banner dismiss, paramRows reset on strategy switch, redundant `model_copy`, extracted `buildParamOptions` to shared `paramOptions.ts`, `adaptiveMs()` for all 5 React Query hooks, `useBotsQuery` AbortSignal, AccountBar error detail, `colColor` type narrowing, `win_rate_pct` precision, sorted metric validation.

- **PR #19 review fixes** (B5 + B8) — 3-agent overnight review caught 7 findings. Fixed: borrow cost missing on external-close path (P1), `entry_time` not cleared in 4 cleanup paths (P1), falsy guard suppressing `borrow_cost=0.0`, `manual_buy` missing `entry_time`, negative `borrow_rate_annual` validation, borrow cost parse failure logging, crossed-market spread guard.

- **PR #20 review fixes** (B8 spread-derived) — 3-agent review caught IBKR timeout risk + design tensions. Fixed: extracted `_spread_derived_bps()` helper (deduplication), 50 bps cap (`SLIPPAGE_MAX_SPREAD_BPS`), market-hours guard (09:30–16:00 ET weekdays only). Discovered IEX quotes are 10–50x wider than NBBO for individual stocks — gated spread-derived path on provider type (IBKR only, skip Alpaca free tier). Yahoo `info` bid/ask also unreliable (119 bps for MSFT).

- **[D25](TODO.md#d--bots-live-trading)** IBC Gateway automation — installed IBC 3.23.0 at `~/ibc/install`, configured for IBKR Gateway paper trading with auto-login, 2FA retry, auto-restart at 05:00 ET. `launchd` plist recovers hourly on weekdays. Only manual step: weekly Sunday 2FA push on IBKR Mobile.

- **IBKR spread-derived slippage wired** — `_spread_derived_bps()` and live spread display now prefer IBKR by name regardless of active trading broker. IBKR quotes use delayed market data (`reqMarketDataType(3)`) — free, no subscription needed, 15-min lag NBBO. Verified: MSFT shows 1.9 bps full spread (realistic). Alpaca IEX and Yahoo bid/ask both confirmed unreliable for individual stocks.

- **[F24](TODO.md#f--architecture--housekeeping)** Configurable poll interval + API rate counter — global `BOT_POLL_MS` (default per-bar-interval fallback), runtime `PATCH /api/broker/poll-interval`, persisted to `.env`. `RateCounter` (trading) + `DataRateCounter` (data fetches) with 60s sliding windows. AccountBar shows `T:26/200 D:15/min @100ms`. Poll input in BotControlCenter with focus-aware sync. Discovered: actual tick rate is bottlenecked by `_fetch()` network latency (~200-500ms), not the sleep interval — 10ms and 100ms produce identical throughput.

## 2026-05-07 (overnight build 13)

- **[B5](TODO.md#b--strategy-engine--rules)** Borrow cost for live short positions — `borrow_rate_annual: float = 0.5` added to `BotConfig`; `entry_time: Optional[str]` added to `BotState` (set at fill, serialised to bots.json). `exits.py _execute_exit()` computes `broker_qty × entry_price × (rate/100/365) × hold_days` at close and stores in journal as `borrow_cost` field. `_log_trade()` extended with optional `borrow_cost` param. `UpdateBotRequest` includes the field. Review: 0 findings in this change.

- **[B8](TODO.md#b--strategy-engine--rules)** Live spread display in slippage panel — `/api/slippage/{symbol}` returns `live_spread_bps` and `half_spread_bps` when a broker is configured (lazy-import `get_trading_provider()`, try/except returns null if market closed or Yahoo source). `SlippageInfo` TS type extended with optional fields. StrategyBuilder shows "live spread: X.X bps (½: Y.Y)" in accent color next to modeled slippage. Auto-apply to modeled_bps deliberately deferred — dynamic real-time defaults make backtests non-deterministic. P2 design note in NEXT_RUN.

## 2026-05-06 (overnight build 12)

- **[F19](TODO.md#f--architecture--housekeeping)** React Query migration for bot/journal polling — replaced 12 manual `setInterval` timers across 5 components (BotControlCenter, TradeJournal, PositionsTable, AccountBar, OrderHistory) with 5 shared React Query hooks in `useTradingQueries.ts`. Journal deduplicated between PositionsTable (was 60s) and TradeJournal (was 5s) — both now share `['journal', brokerFilter]` key. Bots list deduplicated between BotControlCenter and TradeJournal's `listBots()` call. Adaptive interval logic (10s when broker unhealthy) moved into hook layer via broker query cache read. `invalidateQueries` replaces post-mutation `loadBots()` calls. `refetchIntervalInBackground: false` replaces `document.hidden` guards.

- **[C22](TODO.md#c--strategy-summary--analytics)** Auto-optimizer (multi-param grid search) — `POST /api/backtest/optimize` in `backend/routes/backtest_optimizer.py`: accepts 1–3 params each with up to 10 values, generates all combinations via `itertools.product` (max 200), runs each through `run_backtest`, returns top-N ranked by chosen metric (Sharpe/Return/WinRate). `OptimizerPanel.tsx` adds "Optimizer" tab to Results: param selectors (up to 3, each with dropdown + min/max/steps), objective selector, combo-count estimator with 200-cap guard, ranked results table with color-coded metric cells, best-combo highlight panel. Reuses `_apply_param` from `backtest_sweep.py`.

- **Equity curve detail sync fix** — (logged 2026-05-06) — pixel-perfect alignment with main chart via bar-count matching (same pattern as MACD/RSI sub-panes). Passed OHLCV timestamps from App.tsx via memoized `mainTimestamps` prop. Equity chart builds data with exactly the same bar positions as the main chart (whitespace entries where no equity value exists), enabling logical-range sync. Five alignment fixes: (1) added `timeVisible: true` to match main chart's time axis geometry; (2) bar-matched baseline series data through `resolvedTimes` template; (3) snapped trade density ticks to `resolvedTimes` and filtered orphans; (4) deferred price scale width sync via `setTimeout(100)` matching Chart.tsx's `syncWidths` pattern; (5) added invisible `leftPriceScale` at construction for plot-area alignment. One-way sync (main drives equity).

- **[B29](TODO.md#b--strategy-engine--rules)** Regime setup UX overhaul — (1) status line now branches on `on_flip` + `shortBuyRules` presence: shows "goes flat on flip" for close_only, "reverses to [dir]" for close_and_reverse, distinguishes sit-flat-gate vs dual-rule configs; (2) on_flip dropdown relabeled ("Close, wait for signal" / "Close, enter immediately" / "Hold (block new entries)") + regime badge uses short labels (close·wait, close·enter, hold); (3) SINGLE tab hidden when regime enabled, auto-migrates buyRules→longBuyRules on regime toggle if LONG tab is empty; (4) warning scoped to close_and_reverse only (sit-flat gate no longer shows false warning), added coexistence notice when SINGLE+dual rules overlap; (5) review fixes: guards use `some(r => r.indicator)` matching backtest activation condition, removed dead `'single'` state value from activeRuleTab type.

## 2026-05-05 (overnight build 11)

- **[C9](TODO.md#c--strategy-summary--analytics)** Strategy comparison mode — already fully implemented (`StrategyComparison.tsx` with equity overlay + metrics table). Checked off and enhanced: now uses per-strategy `s.capital` instead of a shared default, passes regime+dual-rule-set fields (B23) and per-direction settings (B25) to the backtest API so regime strategies compare correctly. Added B&H baseline dashed line on equity chart and a "% Normalized" toggle that converts all curves to % return from starting value — enabling visual comparison when strategies use different capitals.

- **[D24d](TODO.md#d--bots-live-trading)** Regime HTF cache staleness — live daily-interval fetches (`end >= today`, interval not in `_INTRADAY_INTERVALS`) now use a 5-minute TTL (`_TTL_DAILY_LIVE = 300.0`) instead of the 1-hour historical TTL. Regime direction lag cut from up to 60 minutes to at most 5 minutes after a real daily-bar flip.

## 2026-05-04 (overnight build 10 + interactive session)

- **[C23](TODO.md#c--strategy-summary--analytics)** Sweep error banner — replaced plain `color: red` div with a styled banner (tinted background + red border + ✕ icon) when the sensitivity sweep fails. The `apiErrorDetail()` helper already extracted the error string; only the presentation changed.
- **[C24](TODO.md#c--strategy-summary--analytics)** Direction-aware analytics — `sell_trades` filter includes both "sell"/"cover" exits. Trades tab column headers adapt to "Entry"/"Exit" for short strategies.
- **[B26](TODO.md#b--backtester-engine)** Sweep from rule row — TrendingUp button on numeric-threshold rule rows pre-fills Sensitivity tab.
- **PR #16 review fixes** — stale sweepInit cleared after consumption, zero-value sweep pre-fill handled (center=0 → [-1,1]), partial sweep warning with amber banner (SweepResponse model), equity curve duplicate timestamp dedup.
- **[B28](TODO.md#b--backtester-engine)** Regime rules as full rule sets — `RegimeConfig` gains `rules[]` + `logic` fields. Dual-path in `_compute_regime_series()` and `_eval_regime_direction()`: rules-based path calls `eval_rules()` on HTF data, legacy single-indicator path preserved. UI: four-tab layout (Regime Rules / Long / Short / Single), timeframe controls above rules, timeframe badge per rule row.
- **[B24](TODO.md#b--backtester-engine)** Regime strategy import — per-tab "Import" button copies rules from saved strategies. Direction-aware fallback chain (prefers `longBuyRules` over `buyRules`). Confirm dialog, empty-rules guard, onBlur dismiss.
- **[B27](TODO.md#b--backtester-engine)** Strategy preset categories — `strategyType` field on SavedStrategy (long/short/regime), derived at save time, back-filled on load. Strategy dropdown grouped via `<optgroup>`.
- **[B25](TODO.md#b--backtester-engine)** Per-direction settings — 8 flat fields on StrategyRequest + BotConfig. Four `_dir_*` helpers in backtest loop, per-direction counters (`consec_sl_count_by_dir`, `skip_remaining_by_dir`) with `exited_direction` capture. `exits.py` resolves per-direction values in both methods. OTO bracket uses per-direction stop. UI: Per-Direction settings panel gated on regime, trailing stop exposes type+value only (source/activate stays global).

- **[C24](TODO.md#c--strategy-summary--analytics)** Direction-aware analytics — (1) `sell_trades` filter in `backtest.py` now includes both `"sell"` and `"cover"` exits (was gated on `req.direction`), fixing `num_trades` and `win_rate` for regime `close_and_reverse` strategies; (2) Trades tab column headers in `Results.tsx` adapt to "Entry"/"Exit" when short or mixed-direction trades detected. Audited all other analytics: streaks, MC, rolling, hold time, Kelly already used direction-agnostic PnL-sign logic.

- **[B26](TODO.md#b--strategy-engine--rules)** Sweep from rule row — TrendingUp icon button on rule rows with a numeric threshold (`needsValue && typeof rule.value === 'number'`). Clicking it: (a) pre-fills the Sensitivity tab with the rule's `param_path` selected and ±50% range around the current value, (b) switches the Results panel to the Sensitivity tab. Prop chain: `RuleRow.onSweep → StrategyBuilder.onSweep → App → setSweepInit + setResultsTab('sensitivity') → Results.sweepInit → SensitivityPanel` applies via `useEffect`. Button gates on `onSweep` being defined so regime rule rows (not yet sweep-supported) stay clean.

## 2026-05-04

- **[A8d](TODO.md#a--chart--data)** Same-TF indicator resample to view interval — when "View as" selects a coarser interval, indicators compute at the backtest interval and resample via backend `view_interval` field. Five follow-up fixes during visual testing: (1) removed `origin='start'` from resample — caused 30min offset vs provider clock-hour boundaries; (2) HTF queries use `viewInterval` for alignment so HTF indicators don't inject backtest-interval timestamps; (3) regime background uses `snapTimestamp` (replaces incomplete `snapRegimeTime`); (4) rule signal markers snapped+deduped when aggregated; (5) EMA overlays un-gated and timestamps snapped for coarser views. Trade tooltip SNAP tolerance ±5 candles when aggregated. [Plan](docs/superpowers/plans/2026-05-04-a8d-same-tf-indicator-resample.md)

## 2026-05-03 (review session 2)

- PR #12 (build 7) review: 3 P1 fixes (await→create_task notify_error, negative sweep guard, was_running in list_bots API).
- PR #13 (build 8) review: clean — 0 P0/P1, merged as-is.
- PR #14 (F21 bot_runner split) review: 1 P1 fix (regime.py inline imports bypassing `_br()` pattern). Tests pass 7/7 post-fix.
- PR #15 (build 9) review: 2 P1 fixes (float→int period params for BB/ATR/Stochastic/ADX sweep, skip invalid values instead of aborting sweep).
- Added C24 (regime/short direction-aware analytics), A8d (indicator resample on coarser view).

## 2026-05-03 (overnight build 9)

- **[C21](TODO.md#c--strategy-summary--analytics)** Sensitivity sweep param bug — (1) fixed error swallowing in sweep loop (HTTPException from run_backtest now propagates instead of returning silent zero-result rows); (2) added `rule.params` sweep support in backend `_apply_param` (`buy_rule_{i}_params_{key}`, `sell_rule_{i}_params_{key}`) and frontend `buildParamOptions` (sweeps MA period, RSI period, Stochastic k/d, ADX period, BB std_dev etc.); (3) three P2 review fixes: max_drawdown color `highIsGood=true`, integer rounding for period linspace values, selectedPath reset on lastRequest change.
- **[C20](TODO.md#c--strategy-summary--analytics)** Equity curve blank — fixed rendering logic in Results.tsx: `bucket && macroData ? <MacroEquityChart> : <div ref={chartRef}>` created a blank state when macro bucket selected but data still loading (chartRef div rendered but effect returned early). Two-level ternary fix: chartRef div now only renders when `bucket === null`; loading/no-data states show a placeholder.

## 2026-05-03 (overnight build 8, part 2)

- **[F21](TODO.md#f--architecture--housekeeping)** Split bot_runner.py (1030 → 511 lines). `RegimeMixin` in `regime.py` holds `_eval_regime_direction` + `_handle_regime_flip`. `ExitsMixin` in `exits.py` holds `_detect_external_close` + `_evaluate_exit_reason` + `_execute_exit`. `BotRunner` inherits from both. `_tick()` is now a thin orchestrator. Both mixin files use a `_br()` helper (`sys.modules["bot_runner"]`) so test patches apply correctly at call time. All 7 F20 tests pass post-split.

## 2026-05-03 (review sessions)

- **[D24](TODO.md#d--bots-live-trading)** PR #10 review: 6 P1 fixes (dual-rule indicators, stale trail_stop, skip_remaining bypass, consec_sl on regime flip, manual_buy PnL, stop_bot regime state).
- **[F14](TODO.md#f--architecture--housekeeping)**/**[F15](TODO.md#f--architecture--housekeeping)**/**[F16](TODO.md#f--architecture--housekeeping)** PR #11 review: 2 P1 hardening fixes (unique tempfile paths for concurrent save(), atomic journal writes so readers never see partial JSON).
- **[F23](TODO.md#f--architecture--housekeeping)** Shipped in PR #12 review: `was_running` added to `list_bots()` response + `BotSummary` TS type.
- PR #12 review: 3 P1 fixes (await→create_task for notify_error, negative sweep guard, was_running in API).
- PR #13 review: clean — 0 P0/P1, merged as-is.
- Architectural audit: added F14-F21 (safety fixes, test harness, bot_runner split). Overnight task limit raised 3→5.
- Market research: StrategyLab's 10-capability combination (no-code builder + regime filter + multi-broker + Monte Carlo + sensitivity sweep + live bot dashboard) has no retail equivalent.

## 2026-05-03 (overnight build 8)

- **[F22](TODO.md#f--architecture--housekeeping)** `was_running` badge on BotCard. Stopped bots with `was_running=True` now show an amber "⚡ Was running" badge in both compact and expanded layouts, prompting the user to restart bots that were live before a server restart. Also fixed a P2: `was_running` is now reset to `False` in `BotRunner.run()` so the badge clears once the bot is started and then manually stopped (no stale badge after the first restart cycle).

- **[C18b](TODO.md#c--strategy-summary--analytics)** Sensitivity sweep sparkline. SVG mini line chart above the results table in `SensitivityPanel` showing `total_return_pct` vs `param_value`. Dot colors teal/red by sign, dashed zero-baseline when range straddles zero, footer labels show param range. Renders when ≥2 sweep points available. Makes cliff-edge vs smooth plateau visible at a glance. `preserveAspectRatio="none"` ensures full-width fill on any container width. Not visually verified.

- **[A8c-htf](TODO.md#a--charts--indicators)** HTF overlay line type fix. `LineType.WithSteps` is no longer applied when `viewInterval === inst.htfInterval` (i.e., the chart is already at the same resolution as the HTF data). Added `viewInterval` to the overlay series effect deps so the lineType decision re-evaluates when the user changes view intervals. Not visually verified.

## 2026-05-03 (overnight build 7)

- **[F6](TODO.md#f--architecture--housekeeping)** Split `shared/types/index.ts` into domain files. Created `chart.ts` (OHLCV, TimeValue, Macro* types), `strategy.ts` (Rule, StrategyRequest, BacktestResult, etc.), `trading.ts` (BotConfig, BotState, BotSummary, etc.). `index.ts` now a barrel re-export — all 30+ external imports unchanged, no consumer updates needed.

- **[D23](TODO.md#d--bots-live-trading)** Bot daily P&L bar chart on BotCard. `DailyPnlChart` component: groups equity_snapshots by ET date, computes per-day P&L as day-over-day delta, renders as SVG bar chart (green/red, last 30 days, zero-line, date labels). Shown in BotCard expanded view when ≥2 snapshots available. Pure frontend, no new API calls.

- **[C18](TODO.md#c--strategy-summary--analytics)** Parameter sensitivity sweep. `POST /api/backtest/sweep` endpoint (`routes/backtest_sweep.py`): accepts base StrategyRequest + `param_path` + `values[]`, runs up to 25 backtest variants with one parameter varied, returns summary stats per variant. Supports `stop_loss_pct`, `trailing_stop_value`, `slippage_bps`, `buy_rule_{i}_value`, `sell_rule_{i}_value`. Frontend: `SensitivityPanel` component with param dropdown, min/max/steps inputs, color-coded results table (green=best, red=worst per column). New "Sensitivity" tab in Results, gated on `lastRequest` being available. Not visually verified.

- **[F17](TODO.md#f--architecture--housekeeping)** Bot auto-resume flag. Added `was_running: bool = False` to `BotState`. `BotManager.load()` now sets `state.was_running = state.status == "running"` before resetting all statuses to "stopped". Persisted to bots.json; `from_dict()` picks it up via the generic setattr loop. UI exposure deferred to F22.

- **[F18](TODO.md#f--architecture--housekeeping)** Cap equity_snapshots growth. All 3 `equity_snapshots.append()` sites in `bot_runner.py` now trim to `[-500:]` after each append. Prevents bots.json from growing unboundedly with active bots.

- **[D24c](TODO.md#d--bots-live-trading)** Regime HTF fetch timeout. Wrapped the `fetch_higher_tf` executor call in `asyncio.wait_for(..., timeout=15.0)`. On timeout logs WARN and returns `"flat"` (conservative gate-closed). Prevents a hanging data provider from blocking `_tick()` and stalling stop-loss checks for open positions.

- **[F20](TODO.md#f--architecture--housekeeping)** bot_runner test harness. `backend/tests/test_bot_runner.py`: 7 tests covering key `_tick()` state transitions — no-entry-outside-hours, entry-on-buy-signal, no-entry-when-positioned, stop-loss-exit (long), sell-signal-exit, time-stop-exit, skip-entry-cooldown. `MockProvider` with call-count-aware `get_positions` (models the 3-call pattern: initial check, pre-close safety check, post-close verification). `_direct_executor` patch skips thread pool in tests. All 7 pass in 0.81s.

## 2026-05-03 (overnight build 6)

- **[F14](TODO.md#f--architecture--housekeeping)** Atomic bots.json writes. `BotManager.save()` now writes to a temp file (`DATA_PATH + ".tmp"`) then calls `os.replace()` (atomic on POSIX) so a crash during write can't corrupt or truncate `bots.json`.

- **[F15](TODO.md#f--architecture--housekeeping)** Log journal write errors. Changed all 5 `except Exception: pass` blocks wrapping `_log_trade()` calls (4 in `bot_runner.py`, 1 in `bot_manager.py`) to `except Exception as e: self._log("ERROR", f"Journal write failed: {e}")`. Trade execution at the broker now always surfaces journal failures in the bot's activity log.

- **[F16](TODO.md#f--architecture--housekeeping)** Journal write lock. Added `_journal_lock = threading.Lock()` in `journal.py`. The entire read-modify-write body of `_log_trade()` is now wrapped in `with _journal_lock:`, preventing two bots closing simultaneously from overwriting each other's entries. Slippage computation moved outside the lock (no shared state).

- **[D24a](TODO.md#d--bots-live-trading)** Regime bot backtest_bot() passthrough. Added the 9 missing fields to the `StrategyRequest` constructor in `backtest_bot()`: `regime`, `long_buy_rules`, `long_sell_rules`, `long_buy_logic`, `long_sell_logic`, `short_buy_rules`, `short_sell_rules`, `short_buy_logic`, `short_sell_logic`. Regime bots now backtest with their actual regime + dual-rule config instead of silently ignoring it.

- **[D25](TODO.md#d--bots-live-trading)** Opposite-direction entry guard: skip on error. The `except Exception: pass` block in the position-check guard before entry now logs a WARN and returns (skips entry) instead of proceeding. A broker check failure during a regime bot entry previously risked opening a position that bypassed the opposite-direction guard entirely.

## 2026-05-03 (review session)

- **[D24](TODO.md#d--bots-live-trading)** PR #10 code review — 4 parallel persona agents (correctness, reliability, adversarial, API contract). Found 6 P1 issues, all fixed in `008e70e`: (1) dual-rule indicators not included in `compute_indicators` call, (2) stale `trail_stop_price`/`trail_peak`/`entry_bar_count` on pending flip resolution, (3) `skip_remaining` cooldown bypassed on `close_and_reverse` re-entry, (4) `consec_sl_count` incorrectly incremented on regime flip, (5) `manual_buy` used unidirectional PnL for regime bots, (6) `stop_bot` didn't clear `position_direction`/`pending_regime_flip`. Added D24c, D24d, D25 to TODO from deferred P2 findings.

## 2026-05-03 (overnight build 5)

- **[D24](TODO.md#d--bots-live-trading)** Regime filter live bot integration. Removed the `regime.enabled` guard that rejected regime bots. `_eval_regime_direction()` async method: fetches HTF bars with `htf_lookback_days` lookback, computes regime indicator via `compute_instance()`, applies condition + `min_bars` rolling smoothing, aligns to LTF index via `align_htf_to_ltf()`, returns "long"/"short"/"flat" (conservative "flat" on any error). `_handle_regime_flip()` method: cancels pending orders, calls `provider.close_position()`, polls fill, waits ≤3s for position clear; if not cleared sets `pending_regime_flip = True` for retry next tick; on success logs trade + equity snapshot; if `close_and_reverse` and direction not flat, calls `_enter_position()` before returning. `_enter_position()` extracted helper: handles OTO bracket / plain order for long/short, polls fill, sets `state.position_direction`. `_bot_pnl()` helper: calls `compute_bidirectional_pnl` for regime bots, `compute_realized_pnl` for others. `is_short` global variable removed from `_tick()`; replaced with `entry_is_short` (entry direction) and `pos_is_short` (position direction, re-derived when entering the has-position branch). `position_direction` tracked per entry; `regime_direction` updated each tick. Same-symbol guard in `start_bot()` updated: regime bots require exclusive symbol access (block both ways). `compute_bidirectional_pnl` + `first_bot_bidirectional_entry_time` added to `journal.py`. `list_bots()` uses bidirectional helpers for regime bots; returns `regime_direction`, `position_direction`, `pending_regime_flip`. Dual rule fields (`long_buy_rules` etc.) added to `BotConfig`, `UpdateBotRequest`, `BotConfig` TS type. `BotSummary` TS type gets `regime_direction`, `position_direction`, `pending_regime_flip`. `AddBotBar` passes regime config + dual rules from `SavedStrategy`. `BotCard` shows "Regime" stat cell: ▲ Long / ▼ Short / ⊘ Flat / ⏳ Pending flip. Not visually verified — live regime bot needs browser/paper-trading QA (D24b).

## 2026-05-03 (overnight build 4)

- **[B21a](TODO.md#b--strategy-engine--rules)** Regime config not restored on page refresh. Root cause: the `localStorage` persistence effect in `StrategyBuilder.tsx` was missing `regimeEnabled`/`regimeConfig` from both the serialized JSON and its dependency array. Fix: added `regime: { ...regimeConfig, enabled: regimeEnabled }` to the JSON and both vars to the dep array. `loadStrategy()` now returns the saved regime config so `useState(saved?.regime?.enabled)` initializes correctly on refresh.

- **[A8c](TODO.md#a--charts--indicators)** "View as" 1D axis confusion fix. Root cause: regime histogram in `Chart.tsx` used raw intraday unix timestamps (`toET()` output) while daily candle series uses YYYY-MM-DD strings — mixed timestamp formats confuse lightweight-charts' time scale, producing thin candles and mixed axis labels. Fix: local `snapRegimeTime` helper in the regime histogram effect converts unix timestamps to `YYYY-MM-DD` when `viewInterval` is non-intraday; Map-based deduplication keeps last direction per day; `viewInterval` added to effect dependency array. Remaining issue (deferred): HTF overlay renders as smooth instead of stepped when `viewInterval === htfInterval` — cosmetic, added as `A8c-htf` to TODO.

- **[B23](TODO.md#b--strategy-engine--rules)** Regime dual rule sets. Backend: 8 optional fields (`long_buy_rules`, `long_sell_rules`, `long_buy_logic`, `long_sell_logic`, + short variants) added to `StrategyRequest` in `models.py`. `b23_mode` detection in `run_backtest`: active when regime enabled + both long and short buy rules non-empty. Main loop: entry routes to `long_buy_rules` when regime active, `short_buy_rules` when inactive; `position_direction` set from regime state (always 'long' or 'short', no `req.direction` indirection); exit routes sell rules by `position_direction`. Frontend: 8 new state vars in `StrategyBuilder.tsx` initialized to `[]` (not `[emptyRule()]` — prevents accidental b23 activation); Single/▲Long/▼Short tab bar renders under regime section; dual rules spread into backtest request when regime enabled and both long+short buy rules non-empty. `SavedStrategy` and `StrategyRequest` TS types updated. Not visually verified.

## 2026-05-03 (overnight build 3)

- **[B22](TODO.md#b--strategy-engine--rules)** Regime symmetric direction switching. Added `on_flip: str = "close_only"` field to `RegimeConfig` (backend + TS type). Three behaviors: `hold` (existing gate-only), `close_only` (forced exit on regime flip, no auto re-entry), `close_and_reverse` (forced exit + immediate forced entry in opposite direction at same bar, slippage/commission on both legs). Flip detection via `curr_regime_active != prev_regime_active` each bar. With `close_and_reverse`, signal-driven entries also adopt the regime-determined direction (active=req.direction, inactive=opposite). `regime_series` output shows opposite direction for inactive bars under `close_and_reverse` (not "flat"). Frontend: `on_flip` dropdown (Close only / Close & reverse / Hold) in regime config section; direction toggle hidden when `regimeEnabled && on_flip !== 'hold'`, replaced by informational label showing entry direction and flip target. Build clean. Not visually verified. Review in background.

## 2026-05-03 (overnight build 2)

- **[C17a](TODO.md#c--strategy-summary--analytics)** Fix SPY correlation (beta/R² always 0). Root cause: daily equity-curve returns are 0 for 90%+ of bars when strategy is flat, making covariance meaningless. Fix: rewrote `_compute_spy_correlation(trades, start, end)` to use per-trade returns (pnl / entry_value) paired with SPY's return over each trade's holding period. Returns None gracefully for <3 trades or near-zero SPY variance (intraday same-day trades). P2 cosmetic: intraday strategies with same-day entries/exits will have near-zero SPY variance and correctly show None.

- **[B21](TODO.md#b--strategy-engine--rules)** Regime filter: sit-flat gate + `is_short` refactor. `RegimeConfig` Pydantic model (`indicator`, `indicator_params`, `condition`, `min_bars`) in `models.py`. `_compute_regime_series()` helper in `backtest.py`: fetches HTF data with extended lookback via `fetch_higher_tf()`, computes indicator via `compute_instance()`, evaluates condition (above/below/rising/falling), applies min_bars rolling-window smoothing, aligns to LTF index via `align_htf_to_ltf()`. Main loop: `is_short` local variable replaced by `position_direction` (set at entry, cleared at exit); regime gate added to entry condition (`regime_ok` before `eval_rules`). Trade records use `position_direction` for the `direction` field. `regime_series` (per-bar `{time, direction}`) added to backtest response. Bot runner: guard in `_tick()` raises clear error when `cfg.regime.enabled` is True (live regime not yet supported). `regime: Optional[RegimeConfig] = None` added to `BotConfig` (bot_manager.py) and `UpdateBotRequest` (bots.py — pitfall #4 fix). Frontend: `RegimeConfig` TypeScript interface + `regime?` on `StrategyRequest`, `SavedStrategy`, `BacktestResult`. `StrategyBuilder` regime section (toggle button + timeframe/type/period/condition/min_bars controls + stop-loss warning). Chart.tsx: histogram series on hidden `regime-bg` price scale (#26a64120 green = active long, #f8514920 red = active short). Save/load: snapshot captures regime config, `loadSavedStrategy` restores it. Not visually verified.

- **[A13b](TODO.md#a--charts--indicators)** Multi-TF indicator overlay. Extends `routes/indicators.py` with `htf_interval` param — when set, fetches OHLCV at the higher TF with extended lookback (via `htf_lookback_days`), computes indicator, aligns to LTF index via `align_htf_to_ltf`, returns at LTF timestamps. Frontend: `htfInterval?` field on `IndicatorInstance`; TF selector dropdown ("Same"/"1D"/"1W") in IndicatorList expanded settings for main-pane overlays; `useInstanceIndicators` updated to group HTF instances by `htfInterval` and make parallel API calls via `useQueries`; Chart.tsx renders HTF overlays with `LineType.WithSteps` and includes htfInterval in series title suffix. Prereq A13a.

- **[C10](TODO.md#c--strategy-summary--analytics)** Intraday session analytics. Discovered already shipped in prior session (30-min bucket breakdown of win rate/EV in `compute_session_analytics` backend + `SessionAnalytics` component + Session tab in Results). Checked off.

- **[C17](TODO.md#c--strategy-summary--analytics)** Benchmark correlation (SPY beta/R²). New `_compute_spy_correlation(equity, start, end)` in `backtest.py`: groups equity curve by ET date, computes daily returns, fetches SPY daily (TTL-cached), aligns on common dates, computes beta = cov/var_spy and R² = corr². Returns null for short/empty periods or failed SPY fetches. Added to backtest summary dict; `beta`/`r_squared` added to `BacktestResult.summary` TS type; new "Benchmark Correlation (SPY)" panel in Summary tab showing β value with context label (Amplified/Inverse/Tracking) and R² percentage with fit label.

- **[C19](TODO.md#c--strategy-summary--analytics)** Backtest result persistence. `BACKTEST_CACHE_KEY` localStorage entry stores `{result, request}` on each backtest. On page load, `_cachedBacktest` checks if saved ticker/start/end/interval matches the cache's request — if so, restores last backtest result into state so results are visible without re-running. Clears cache when result is null (ticker/date changes). Quota-exceeded silently skipped.

## 2026-05-02

- **[A13a](TODO.md#a--charts--indicators)** Multi-TF data foundation. Three new functions in `backend/shared.py`: `htf_lookback_days(indicator, params)` computes calendar-day warmup window (`int(period * 1.5 * 365/252 + 30)`); `fetch_higher_tf()` thin wrapper over `_fetch()` for HTF data; `align_htf_to_ltf(htf_series, ltf_index)` aligns daily values to intraday bars with strict anti-lookahead via `shift(1)` + UTC normalization + `pd.merge_asof(direction='backward')`. Handles weekend/holiday gaps and tz-aware/naive inputs. 6 exhaustive tests in `backend/tests/test_htf_alignment.py` (6/6 pass) covering lookahead, forward mapping, weekend gap, empty series, warmup NaN, lookback formula. Shared prereq for A13b, B21, D24.

- **[C15](TODO.md#c--strategy-summary--analytics)** Win/loss streak analysis panel. New `streakUtils.ts` (streak computation: max/avg win+loss streaks) and `StreakPanel.tsx` (UI panel). Inserted into Summary tab in Results.tsx. Shows max consecutive wins/losses (large colored numbers), avg streak lengths, and mini SVG distribution charts (120×36px, shown when ≥2 streaks). Gated on closed trades presence.

- **[D22](TODO.md#d--bots-live-trading)** Trade journal CSV export. Already shipped as part of D13 (verified: `exportCsv()` function at TradeJournal.tsx:140, download button at line 198). Checked off.

- **[C16](TODO.md#c--strategy-summary--analytics)** Kelly position sizing. New `KellySizing.tsx` component embedded in Summary tab when ≥5 completed trades. Computes Kelly criterion (`f* = W − (1−W)/R`) from backtest win rate and avg win/loss ratio. Displays full Kelly, ½ Kelly (recommended), ¼ Kelly fractions. Shows "no edge" warning with 0% size recommendation when f* ≤ 0.

- Summary tab layout fix: removed `maxHeight: 600` cap so content fills viewport. Cost Breakdown, Win/Loss Streaks, and Kelly Sizing now in responsive CSS grid (`auto-fit, minmax(320px, 1fr)`) — three columns on wide screens, stacking on narrow.

## 2026-05-01

- **[C13](TODO.md#c--strategy-summary--analytics)** Monte Carlo bug fixes. (1) `final_value` percentile stats were all identical — replaced with `min_equity` (minimum equity touched during each simulation), which spreads meaningfully across shuffles. Backend: `min_equities` tracked per-sim, returned as `min_equity` in response. Frontend: MonteCarloChart.tsx updated to display min equity stats with correct color semantics (p5=red worst, p95=green best). (2) `fetch()` → `api.post()` fix was already committed in prior session.

- **[C14](TODO.md#c--strategy-summary--analytics)** Trade duration histogram. New `TradeHoldDurationHistogram.tsx` component (207 lines): SVG histogram of hold times, buckets colored by win/loss dominance, summary row showing median/avg-win/avg-loss hold times. Intraday uses hours (unix timestamp diff / 3600), daily uses calendar days. "Hold Time" tab in Results appears when ≥2 completed trades.

- **[D21](TODO.md#d--bots-live-trading)** Strategy auto-pause on drawdown. `BotConfig.drawdown_threshold_pct: Optional[float]`. When set, `_tick()` checks peak-to-trough PnL vs `allocated_capital` after each position closes — pauses bot with `status="error"` + `pause_reason` message and fires `notify_error` (fire-and-forget) if threshold exceeded. Covers both long-exit and short-exit paths; state fields cleared before save. Frontend: AddBotBar "Max DD %" input + BotCard inline-editable field (pattern from allocated_capital).

## 2026-04-30

- **Overnight builder operational.** Push auth resolved — required installing the Claude GitHub App on GitHub (`github.com/apps/claude`) with Contents: Read & write permission. Routine prompt updated to use `claude/` prefixed branches + `gh pr create`. First successful delivery: PR #4 (C11 + C12) merged.

- **[C13](TODO.md#c--strategy-summary--analytics)** Monte Carlo bug fix: overnight builder used raw `fetch()` instead of project `api` client — requests hit Vite dev server (port 5173) instead of backend (8000), failing silently. Fixed to use `api.post()`.

- **[C11](TODO.md#c--strategy-summary--analytics)** Monte Carlo simulation. New `POST /api/backtest/montecarlo` endpoint (`backend/routes/monte_carlo.py`) accepts a list of exit-trade PnLs + initial capital, runs 1,000 random shuffles, and returns percentile curves (p5/p25/p50/p75/p95) over the trade sequence plus final-value percentiles, max-drawdown percentiles, and probability of ruin. New `MonteCarloChart.tsx` SVG component renders shaded percentile bands. New "Monte Carlo" tab in Results appears when ≥ 2 completed trades; auto-fetches on first visit, resets on new backtest run.

- **[C12](TODO.md#c--strategy-summary--analytics)** Rolling performance window. New `RollingWindowChart.tsx` computes rolling win rate, avg PnL, and Sharpe ratio over a sliding N-trade window entirely client-side from the existing trades array. Window selector (5 / 10 / 20 / 50 trades). Three stacked SVG mini-charts with reference lines (50% win rate, $0 PnL, 1.0 Sharpe). New "Rolling" tab in Results appears when ≥ 5 completed trades.

## 2026-04-29

- **[E5](TODO.md#e--discovery)** Quick backtest endpoint. New `POST /api/backtest/quick` returns summary-only stats (return %, Sharpe, win rate, num trades, max drawdown, `signal_now`, `last_signal_date`) without equity curve or trade list. Batch variant `POST /api/backtest/quick/batch` runs sequentially over a symbol list. Registered in `main.py` alongside existing routers. Route file: `backend/routes/backtest_quick.py`.
- **[D20](TODO.md#d--bots-live-trading)** Bot alerting via ntfy.sh. New `backend/notifications.py` with fire-and-forget `notify()` + four typed helpers (`notify_entry`, `notify_exit`, `notify_stop`, `notify_error`). Hooked into `bot_runner.py` at entry fill, detected exit, IBKR structural error, and MAX_CONSEC_ERRORS backoff. Enable by setting `NOTIFY_URL=https://ntfy.sh/your-topic` in `backend/.env`. New endpoints: `GET /api/notifications/status` and `GET /api/notifications/test`.

- **[C10](TODO.md#c--strategy-summary--analytics)** Intraday session analytics. `compute_session_analytics()` in `backtest.py` breaks down trade performance by 30-min time-of-day buckets (09:30–16:00 ET). Frontend: horizontal bar chart in Results summary tab with win-rate-colored bars, trade counts, avg PnL per bucket, best/worst window summary. Only renders for intraday intervals.

- **[C9](TODO.md#c--strategy-summary--analytics)** Strategy comparison mode. New `StrategyComparison.tsx` component: select 2-3 saved strategies, run backtests in parallel via `Promise.all`, overlay equity curves (blue/orange/green) on a single lightweight-charts instance, side-by-side metrics table (Return, Sharpe, Win Rate, Max DD, PF, EV, vs B\&H) with best/worst highlighting. Toggle via `⇄ Compare` button in chart pane header. Shared `savedStrategies.ts` extracted for `migrateRule`/`loadSavedStrategies`.

- D20 review-driven fixes: merged `notify_stop` into `notify_exit` (one notification per event, priority=high for stops), `asyncio.create_task()` for fire-and-forget (was blocking `_tick()`), `run_coroutine_threadsafe` for sync IBKR callback, httpx client lifecycle cleanup, removed NOTIFY_URL leak from API responses, added notifications to bot-managed close path. 14 fixes total from 8-persona parallel review.

- **[B4](TODO.md#b--strategy-engine--rules)** Per-rule signal visualization. Eye icon toggle on each rule row; backend emits `rule_signals` in backtest response (per-bar signal data for visualized rules with negation/muted handling); Chart.tsx merges signals into main markers as colored circles with legend overlay. Review-driven fixes: negation inversion, rule_index offset for sell rules, muted guard, variable shadow, React key collision, lucide-react Eye icon.

- **[C8](TODO.md#c--strategy-summary--analytics)** Fix short strategy final value mismatch. `final_value` used long formula for shorts with open positions, causing wrong Return % and vs B&H. Now matches equity curve calculation.

- **[A7](TODO.md#a--charts--indicators)** New chart indicators: Stochastic (%K/%D lines + 80/20 reference), VWAP (main chart overlay, orange), ADX (ADX/+DI/-DI lines + 25 trend reference). Full sidebar param editing + indicator registry. Three parallel worktree agents for backend compute, frontend rendering, and signal engine.

- **[B14](TODO.md#b--strategy-engine--rules)** Stochastic + ADX as rule indicators. Backend: `compute_stochastic`, `compute_vwap`, `compute_adx` in `indicators.py`. Signal engine: stoch/adx specs, resolve_series/resolve_ref with %K/%D crossover pattern (matching MACD). Frontend: RuleRow with param UIs, NEEDS_PARAM for stochastic crossovers.

- **[D19](TODO.md#d--bots-live-trading)** Bot card redesign — responsive sparkline columns (fixed 60% → flex 35/65 split), columnar stats (label above value with flex-wrap), compact mode kebab dropdown replacing inline buttons, portfolio strip column alignment. Shared `ui.tsx` for layout primitives (`btnStyle`, `StatCell`, `INFO_COLUMN_FLEX`). Fixes: P&L division-by-zero guard, stale detail cleanup on collapse, menuOpen reset on mode toggle. 106 tests across 3 new test files.

- **[A8 equity downsample](TODO.md#a--charts--indicators)** Fixed equity curve timestamp alignment. Root cause: Results.tsx was passing raw UTC timestamps to the equity chart while Chart.tsx applies `toET()` ET-shift to candlestick timestamps. Mismatch meant crosshair sync was broken and `downsampleEquity()` bucket keys didn't align with main chart bars. Fix: added `toDisplayTime()` to `shared/utils/time.ts` (exact mirror of Chart.tsx `toET`) and applied it to equity/baseline/trade-tick timestamps in Results.tsx before downsampling. `downsampleEquity()` logic itself was always correct — the TODO's "doesn't take effect" was caused by the timestamp mismatch, not the function or the effect re-firing.

- **[E5](TODO.md#e--discovery)** SignalScanner reframed as research tool. Rewrote frontend: saved strategy dropdown (no inline rule editing), lookback selector, sortable results table (return %, Sharpe, win rate, max DD, signal_now). Spawn Bot pre-fills AddBotBar via localStorage pending-spawn key. Batch error handling fix in `backtest_quick.py` (individual ticker failures no longer abort the batch). `onSpawnBot` handler wired through App.tsx → Discovery.tsx → SignalScanner.tsx; AddBotBar reads pending-spawn on mount.

## 2026-04-28

- **[D10](TODO.md#d--bots-live-trading)** compact sparkline alignment fix. Multiple iterations. Real root cause: compact row was a flat flex layout where sparkline position depended on variable-width text before it. Fix: restructured to two-column layout mirroring expanded mode — `flex: 1` left column (text/buttons) + `flex: 0 0 60%` right column (sparkline). Also fixed overflow menu z-index (removed `scale: '1'` creating stacking contexts on idle SortableBotCard wrappers) and moved buttons before sparkline.
- **[D10](TODO.md#d--bots-live-trading)** compact cards: inline buttons. Replaced overflow dropdown menu with inline action buttons (Backtest, Stop, Buy, Reset, Delete). Right-aligned via `marginLeft: auto`. Simpler code, better UX, net -29 lines.
- **[A1](TODO.md#a--charts--indicators)** portfolio sparkline alignment. Matched PortfolioStrip horizontal padding and gap to bot card values so sparkline left edges line up vertically across portfolio and bot rows.
- Sparkline instant settle on load. Added `fitContent()` call to MiniSparkline's ResizeObserver — charts were bunching to the right on page load because the initial mount width was stale and `fitContent` wasn't re-called after resize.
- Tab persistence. Active tab (Chart/Live Trading/Discovery) now persists to localStorage across page reloads.
- **[D5](TODO.md#d--bots-live-trading)** `list_bots` perf fix. Journal was read + parsed 27 times per `list_bots` call (3 functions x 9 bots). Added `_load_trades()` helper and optional `trades` parameter — now reads once and passes through. Live Trading page load went from 3-5s to instant.
- Build fixes. Resolved 6 pre-existing `tsc -b` errors in Chart.tsx (unused var, Group ref type) and chart-mount.test.tsx (circular type, unused vars). Prod build now passes clean.

## 2026-04-27

- 15-task blitz session using parallel subagent orchestration. Established the pattern: main session orchestrates (picks tasks, writes specs, dispatches, verifies diffs, commits), subagents do the heavy lifting in their own context windows. Four agents ran simultaneously at peak. Context needle barely moved in the main session despite the volume.

- **[B15](TODO.md#b--strategy-engine--rules)** MACD crossover fix. `crossover_up`/`crossover_down` conditions never fired because the frontend never set `rule.param` to `'signal'`. Auto-set on rule creation (`emptyRule()`), indicator change, condition change, and `migrateRule()` for existing saved strategies.

- **[D1](TODO.md#d--bots-live-trading)** Global timezone toggle. Header button switches all timestamps between ET (EST/EDT) and browser-local time (CET/CEST). `useSyncExternalStore`-based so formatting functions read the mode directly without requiring hooks at every call site. Persisted to localStorage.

- **[C3](TODO.md#c--strategy-summary--analytics)** Sharpe/DD color bands. Sharpe: green >=1, orange 0.5-1, red <0 (underperforms cash), gray 0-0.5. Max DD: red >=10%, gray <10%.

- **[C2](TODO.md#c--strategy-summary--analytics)** Alpha vs B&H metric. Replaced raw "B&H Return" with "vs B&H" showing outperformance delta. Green when strategy beats buy-and-hold, red when it doesn't, regardless of absolute return sign.

- **[B16](TODO.md#b--strategy-engine--rules)** Ghost trade markers verified fixed. Interval change clears `backtestResult` (-> markers), view interval change recomputes markers via useMemo. No stale markers survive. Marked done without code changes.

- **[C4](TODO.md#c--strategy-summary--analytics)** Histogram zero line + labels. Zero baseline, brighter vertical dashed line, min/max/$0 tick labels below bars with `$1.2k` shorthand.

- **[F1](TODO.md#f--architecture--housekeeping)** Paper Trading -> Live Trading. Tab label rename only; filenames/imports left alone.

- **[B10](TODO.md#b--strategy-engine--rules)** Spread gate frontend wiring. AddBotBar: "Max Spread bps" input (default 50). BotCard: inline-editable spread cap (same pattern as allocation). Empty/0 = disabled. Backend was already shipped.

- **[F9](TODO.md#f--architecture--housekeeping)** `STRATEGYLAB_DATA_DIR` env var. Threads through journal, bot_manager, watchlist. Defaults to `backend/data/`, auto-creates on startup. Data now survives `git clean`.

- **[D4](TODO.md#d--bots-live-trading)** Dead `BotState.total_pnl` removed. Never written by bot_runner; P&L is live-computed via `compute_realized_pnl()`. Old bots.json silently ignores the field via `from_dict()` filtering.

- **[C1](TODO.md#c--strategy-summary--analytics)** Inline range bars on waterfall. Removed the separate Biggest/Avg/Smallest stat column. Each Wins/Losses row now has an inline min-max range bar (4px, muted) with a brighter avg tick.

- **[B17](TODO.md#b--strategy-engine--rules)** Minimal trade markers. Tooltip was already implemented (B18 shipped it). Stripped text labels from main-chart markers; subpane markers retain labels. Clean arrows colored by P&L outcome.

- **[B11](TODO.md#b--strategy-engine--rules)** Strategy library UX. Inline rename + pin-to-top for saved strategies. Pinned sort first with star prefix in dropdown. Backward-compatible with old saves.

- **[D9](TODO.md#d--bots-live-trading)** Partial-position reconciliation. Tracks `_last_broker_qty` between ticks, logs WARN on external shrinkage (e.g. broker forced buy-in). Guards against false positives on first tick, pending orders, and full closures.

- **[B2](TODO.md#b--strategy-engine--rules)** Extended hours wiring. Alpaca client-side RTH filter (9:30-16:00 ET) when `extended_hours=False`. Yahoo/IBKR were already wired natively. Cache keys already included `extended_hours`.

- Workflow docs shipped. Parallel subagent orchestration pattern documented in CLAUDE.md + memory. Review workflow: full cycle for logic-touching tasks, skip for trivial (renames, colors, <10 lines).

- **[A1](TODO.md#a--charts--indicators)** Portfolio summary strip (earlier session). Staircase-merged sparkline + summary stats (Total P&L $/%, Allocated, Running/Total, Profitable bots).

- 6-feature parallel blitz (~15 min wall-clock). **[A5](TODO.md#a--charts--indicators)** resizable chart panes, **[A6](TODO.md#a--charts--indicators)** watchlist sidebar, **[B13](TODO.md#b--strategy-engine--rules)** BB/ATR/Volume rules, **[D2](TODO.md#d--bots-live-trading)** bot drag-to-reorder, **[F4](TODO.md#f--architecture--housekeeping)** frontend test harness, sparkline hover tooltip. Six worktree agents dispatched simultaneously, zero code conflicts, all working on first load. [Post-mortem](docs/postmortems/2026-04-27-session-postmortem-2.md)

---

## Pre-journal

_Everything shipped before the journal started (2026-04-27). Grouped by theme._

### Foundation & Architecture

- **[F13](TODO.md#f--architecture--housekeeping)** Structural refactoring — extract models/journal/bot_runner, split BotControlCenter, shared utils, centralize API client
- **[F5](TODO.md#f--architecture--housekeeping)** `./start.sh --prod` flag — builds frontend via `vite preview`, backend without `--reload`, cleared pre-existing TS errors so `npm run build` passes `tsc -b` clean
- **[F11](TODO.md#f--architecture--housekeeping)** Chart mount-once refactor — three `IChartApi` panes created once and kept alive across ticker/interval/indicator changes; teardown hardened against sibling-pane races, `AbortController`s on trading pollers, `ErrorBoundary` in `main.tsx`
- **[F12](TODO.md#f--architecture--housekeeping)** Idle-CPU reduction — dev ~107% to ~1.3%, prod ~58% to ~5%. MiniSparkline mount-once pattern, `useCallback` polling timers, JSON-diff guards on state setters, PositionsTable journal poll relaxed to 60s
- **[E6](TODO.md#e--discovery)** Moved signal scanner from bot page to new Discovery tab

### Charts & Visualization

- **[A4](TODO.md#a--charts--indicators)** Indicator system redesign — instance-based model replacing hardcoded toggles, `INDICATOR_DEFS` registry, `SubPane` + `PaneRegistry`, inline param editing, POST-based indicator endpoint
- **[A8a](TODO.md#a--charts--indicators)** Chart perf 10x — cached `Intl.DateTimeFormat` in `toET()`, consolidated EMA overlays from hundreds of `LineSeries` to 2 per overlay, gated SPY/QQQ fetches on toggle
- **[A8b](TODO.md#a--charts--indicators)** Chart display interval ("View as") — decouple display from backtest interval, trade markers snap to display bars with aggregation
- **[A2](TODO.md#a--charts--indicators)** Equity curve macro mode — resampled equity chart (D/W/M/Q/Y) via `MacroEquityChart.tsx` + `/api/backtest/macro`
- **[A9](TODO.md#a--charts--indicators)** Date range presets (D/W/M/Q/Y) + period stepping arrows
- **[A10](TODO.md#a--charts--indicators)** Equity curve: normalised B&H comparison + log scale toggles
- **[A11](TODO.md#a--charts--indicators)** MA8/MA21 with SMA/EMA/RMA selector + Savitzky-Golay smoothed variants
- **[A12](TODO.md#a--charts--indicators)** Baseline (buy & hold) overlay toggle on equity curve

### Strategy Engine

- **[B19](TODO.md#b--strategy-engine--rules)** Shorting — direction field end-to-end (backtest, bot runner, chart markers, bot cards, stop-loss inversion)
- **[B1](TODO.md#b--strategy-engine--rules)** Skip N trades after stop-loss + configurable downside trigger
- **[B3](TODO.md#b--strategy-engine--rules)** MA slope conditions (`turns_up`/`turns_down`) + `decelerating` via Savitzky-Golay second derivative
- **[B6](TODO.md#b--strategy-engine--rules)** Realistic cost model — IBKR Fixed per-share commission, empirical slippage, short borrow cost
- **[B7](TODO.md#b--strategy-engine--rules)** Slippage model redesign — separate measured vs modeled, always >= 0, floor at default, single signed-cost helper
- **[B12](TODO.md#b--strategy-engine--rules)** Parameterized MAs — generic `ma(period, type)` replacing 5 hardcoded entries, removed all S-G smoothing code
- **[B18](TODO.md#b--strategy-engine--rules)** Triggering rules in trade tooltip — backend tags each trade with fired `rules`, entries show buy rules, exits show sell rules or mechanical stop type

### Analytics

- **[C5](TODO.md#c--strategy-summary--analytics)** Expected value / profit factor — EV + PF headline numbers, 3-row decomposition waterfall
- **[C6](TODO.md#c--strategy-summary--analytics)** Strategy summary: min/max/avg gain and loss
- **[C7](TODO.md#c--strategy-summary--analytics)** Summary readability pass — size hierarchy on metrics row, renamed labels, dropped `(mean)` suffix

### Bot System & IBKR

- **[D11](TODO.md#d--bots-live-trading)** IBKR broker integration — `ib_insync` provider behind `TradingProvider` protocol, global broker selector, simultaneous long+short on same symbol
- **[D12](TODO.md#d--bots-live-trading)** IBKR stability pass — heartbeat auto-reconnect with clientId rotation, `ib.portfolio()` for live data, phantom SELL prevention
- **[D6 + D7](TODO.md#d--bots-live-trading)** IBKR heartbeat + multi-broker union — HeartbeatMonitor 5s pings, aggregate positions/orders across brokers, broker column + health dots + filters
- **[D8](TODO.md#d--bots-live-trading)** IBKR reliability overhaul — error event classification (structural vs transient), reconnect dedup via asyncio.Lock, exponential backoff, 3s TTL cache, adaptive UI polling
- **[D3](TODO.md#d--bots-live-trading)** Bot lifecycle: soft P&L reset via `pnl_epoch`, journal untouched, orphan row marking
- **[D13](TODO.md#d--bots-live-trading)** Paper trading polish — journal reason colors, Expected/Gain% columns, summary row, CSV export, heartbeat dots, position polling
- **[D14](TODO.md#d--bots-live-trading)** Track actual slippage — poll broker fill prices, log expected vs actual, surface in journal
- **[D15](TODO.md#d--bots-live-trading)** Manual buy on bot to start a position
- **[D16](TODO.md#d--bots-live-trading)** Editable allocation and strategy on bot card (click when stopped)
- **[D17](TODO.md#d--bots-live-trading)** Global start/stop all bots
- **[D18](TODO.md#d--bots-live-trading)** Bot sparkline: local vs aligned timescale toggle
- Verify allocation logic (was position_size=10.0, clamped to 0.01-1.0)
- Buying amount compounds P&L (allocated_capital + total_pnl), matching backtest
- Refresh button on journal
- UI update frequency 5s to 2s for bot list + detail polling
- Position size: removed slider, hardcoded 100%

## 2026-05-08

### Cost Model

- **[B8](TODO.md#b--strategy-engine--rules)** Spread-derived slippage default — `decide_modeled_bps()` now tries the active broker's `get_latest_quote()` before falling back to 2 bps default. Half-spread formula: `(ask − bid) / (2 × mid) × 10,000` bps per leg. Conservative floor: `max(2.0, half_spread_bps)`. Source label: `"spread-derived"` shown as "live spread" in StrategyBuilder. Priority: empirical (20+ fills) > spread-derived (live quote) > default. Lazy import inside `try/except` avoids circular import and handles Yahoo (no `get_latest_quote()`), broker offline, and market-closed (zero bid/ask) gracefully.

### Cost Model UI follow-ups

- **[B5a](TODO.md#b--strategy-engine--rules)** Borrow cost in TradeJournal — added Borrow column to the live trading journal table. Column only appears when any visible row has a non-null, positive `borrow_cost` (short positions only). Shows dollar cost in red; '—' for longs. CSV export includes the column when active. `borrow_cost` field added to `JournalTrade` TS type (was already stored in journal JSON from B5).

- **[B8a](TODO.md#b--strategy-engine--rules)** "Use live spread" button — added small button in Capital & Fees next to the Slippage input. Visible only when `slipInfo.half_spread_bps` is available (IBKR active, market hours). Clicking pre-fills `slippageBps` with the half-spread value and sets source label to "live spread". User-triggered (reproducible backtest assumptions), not auto-applied.

### Architecture & Bot fixes

- **[B5b](TODO.md#b--strategy-engine--rules)** Total borrow cost in TradeJournal summary row — `totalBorrowCost` added to `summaryStats` IIFE via null-safe `reduce`. Summary row Borrow cell (gated by `hasBorrowCost`) now shows `$X.XXXX` in red when total > 0, empty when zero.

- **[B5c](TODO.md#b--strategy-engine--rules)** Bot runner borrow cost on position resume — `exits.py` was already computing and logging `borrow_cost` on all exit paths via `_compute_borrow_cost()`. Gap: `entry_time` was never set when the bot resumed tracking an externally-opened position (`entry_price is None`). Fixed by setting `state.entry_time = datetime.now(timezone.utc).isoformat()` in the resume block, matching the pattern at entry fill. `_compute_borrow_cost()` returns 0.0 when `entry_time` is None, so short borrow was silently zeroed on externally-opened positions.

- **[F27](TODO.md#f--architecture--housekeeping)** Concurrent `_fetch()` dedup via threading.Lock — added `_fetch_dedup_locks: dict[tuple, threading.Lock]` (one lock per `(symbol, interval)`) and `_fetch_dedup_locks_meta` (threading.Lock protecting dict creation) to `shared.py`. The entire check→fetch→populate block runs inside `with _lock:`, so a second concurrent caller blocks until the first finishes and then hits the freshly-populated TTL cache. Zero overhead on warm-cache ticks. Eliminates redundant parallel HTTP requests when multiple bots on the same symbol start simultaneously.

## 2026-05-09 (PR #26 review fixes)

Multi-agent review of build 19 (PR #26: F29, F30, F28d, F31, F32) via `ce:review`. 9 reviewers — found 1 P1 + 4 P2 + 5 P3.

- **P1 fixed — [F28e](TODO.md#f--architecture--housekeeping):** Replaced the `@field_validator('direction')` pattern with a shared `DirectionField = Annotated[Literal['long', 'short'], BeforeValidator(str.lower)]` type alias in `models.py`. Applied to `StrategyRequest.direction` AND `BotConfig.direction`. Removed duplicate `@field_validator` from `UpdateBotRequest` in `routes/bots.py`. Closes the F28 direction-validation pass across all models.
- **P3 fixed:** Removed dead `import time` from `TestFetchOhlcvAsyncDedup` in `test_bot_runner.py`. Kept `from shared import fetch_ohlcv_async` as inline imports (module-level import breaks test patch bindings — pre-existing pattern).

Deferred findings added to TODO.md as F39–F43: batch quote silent null (F39), dedup test timing gate (F40), BotDetail.state type mismatch (F41), eval_rules runtime guard (F42), log injection via tickers (F43). Removed duplicate C25a entry (builder re-added it; canonical copy at bottom of F section with [next] tag was already present).
