# UX Walkthrough Improvements — Consolidated Plan

**Date:** 2026-05-16
**Sources:** `docs/postmortems/2026-05-15-ux-walkthrough.md` (Q1) and `docs/postmortems/2026-05-15-ux-walkthrough-quant-laptop.md` (Q2). Both are first-session quant pass-throughs; items below merge their overlapping findings under a single F-ID.

## Goal

Close the gap between StrategyLab's analytics quality (WFA stability tags, empirical slippage, Kelly side-by-side, regime composition — all best-in-class per both reports) and the first-five-minutes flow, which has small dull edges that punch above their weight on first impressions. No new analytics; this is purely about surfacing, defaulting, and laying out what already exists.

## Non-goals

- Reworking the strategy save/load model into a full project/versioning system (deferred — F-UX10, separate plan).
- Mobile/<1280-px viewport support.
- Live Trading deep-dive UX (only smoke-tested in both reports).
- Discovery scanner content (P3.4 is a layout-only fix; scanner work is out of scope).

## Severity model

Borrowing the postmortems' P1/P2/P3 + Q1's Tier 1/2/3 — collapsed into three tiers:

- **T1 — Integrity / silent-wrong-answer.** Block daily-driver adoption. Ship first.
- **T2 — Workflow friction.** Slows the loop or hides what the user just asked for. Ship next, in 1–2 bundles.
- **T3 — Polish.** Visible-on-screenshot wins, no behavioural risk. Bundle last.

---

## T1 — Integrity (ship first)

### F-UX1 · Optimizer / WFA Param 1 default → first rule threshold
**Source:** Q1 U1.
**Problem:** Param 1 dropdown defaults to *Slippage (bps)*. A first-time user runs the optimizer and "discovers" 0 bps is best. Slippage is a cost assumption, not a parameter to fit.
**Mechanism (corrected per feasibility review):** The default is `paramOptions[0]?.path` in `OptimizerPanel.tsx`. `buildParamOptions()` in `frontend/src/features/strategy/paramOptions.ts` currently pushes cost-parameters (Stop Loss, Trailing, Slippage) before buy/sell rule thresholds — that ordering is the bug.
**Fix:** Reorder `buildParamOptions` so buy-rule thresholds come first, then sell-rule thresholds, then risk params (stop/trailing), and slippage/borrow last. The `[0]` default then naturally lands on the right thing. No changes needed in `OptimizerPanel` / `WalkForwardPanel` callsites.
**Files:** `frontend/src/features/strategy/paramOptions.ts`. (Plan previously named `features/optimizer/` and `features/walkforward/` — those directories do not exist; both panels live under `features/strategy/`.)
**Effort:** ~5 lines + 1 test asserting first option is a buy/sell rule threshold for a typical request.
**Risk:** Existing saved-config localStorage entries that pinned "slippage_bps" still rebind correctly via the `validPaths` set check at `OptimizerPanel.tsx:79`. Confirmed.
**Tag:** `[easy] [hardening]` — keeping `[hardening]` (footgun, not cosmetic) despite Q1's `[polish]` tag in the suggested-TODO list.

### F-UX2 · Surface intraday date-range clamp in UI
**Source:** Q1 U3, Q2 P1.3 — same issue. **Highest integrity item.**
**Problem:** Pick 1H interval with 2020-01-01 → 2026-12-31, request is silently clamped to ~730 d. Header still shows full range; user thinks they backtested 6 years.

**Decision: frontend-only first.** Per scope review, the frontend already knows the interval-→-limit mapping (yfinance: 1m=7d, 5/15/30m=60d, 1h=730d — documented in CLAUDE.md). The chip can be computed entirely from `(From, To, interval)` on the client. No backend change needed for v1.

**Fix (v1, frontend-only):**
1. In sidebar (`frontend/src/App.tsx` or the date-range component near the From/To inputs), add a derived warning: when `interval` is intraday AND `(To − From) > clampLimit`, show chip `⚠ Intraday clamped to last 730d — effective range: <effective_from> → <to>`. Effective-from = `to - clampLimit`.
2. Chip exposes a "Use effective range" affordance. **Critical trap (per `project_date_input_blur.md` memory):** the From/To inputs commit on `onBlur`, not `onChange`. Programmatically setting `input.value` will NOT propagate to React state. The chip's handler MUST update the React state directly (via the same setter the From input's `onBlur` handler calls), not by mutating the input DOM node. Document this inline at the callsite.

**Fix (v2, optional, deferred):** True effective range from the provider (after `_fetch`'s clamp resolves) is more accurate than the client estimate when the provider returns less than requested for other reasons (holidays at the boundary, missing bars). If/when needed, expose via a response field — but this is a provider-return-contract change (single clamp branch lives at `backend/shared.py:61-68` in `YahooProvider.fetch`, called via `run_in_executor` with positional args at line 546; the wrapper signature must change). Not part of this bundle.

**Edge case (per feasibility review):** When clamping causes the provider to raise `HTTPException(404 "No data")` (e.g. user picked a window entirely outside the clamp), the chip is the *only* place the user learns why. Make the chip render in error-state form on 404 too — keep it on the same component so the message is colocated with the inputs.

**Files:** `frontend/src/App.tsx` (sidebar From/To section), plus whichever component owns the From input's `onBlur` (grep `project_date_input_blur.md` style: search for `dateInput` / `from_date`).
**Effort:** ~1.5 hours (down from 2h — no backend work).
**Tag:** `[medium] [hardening]`

### F-UX3 · Zero-trade / unparseable-threshold rule validation (frontend-only)
**Source:** Q1 U2.
**Problem:** Set MACD condition to "Is above" without a value → backtest runs with 0 trades, no warning. Generalises to any rule where `value` is NaN, empty, or unparseable.
**Fix:** Frontend-only guard. Red border + inline message ("Threshold required for 'Is above'") on the rule row when the rule's condition requires a threshold but `value` is blank/NaN. Disable Run Backtest while any rule is in error state. Per scope review, dropping the backend Pydantic validator — the frontend controls all submission paths; a backend 422 layer is gold-plating for a UI footgun. If untrusted API callers are ever a concern, file a separate hardening item.
**Files:** `frontend/src/features/strategy/RuleRow.tsx` (condition→requires-threshold map already exists for the dropdown — reuse).
**Care:**
- Muted rules (`muted=True`) skip validation — they don't evaluate.
- Threshold-free conditions (`crosses_above`, `crosses_below`, `turns_up`, `turns_down`) must be whitelisted — they're valid with no `value`. Per feasibility review: grep `signal_engine.py` for the condition list and mirror exactly; do not hardcode the "needs threshold" list in two places.
**Effort:** ~45 minutes.
**Tag:** `[easy] [hardening]`

### F-UX4 · Slippage input — locale-aware parse
**Source:** Q2 P2.1.
**Problem:** Empirical slippage paints as `3,09` in `,`-decimal locales (Swedish). HTML5 number input sets `invalid="true"` + `valuemax="0"`. Submit silently sends NaN.
**Fix:** Format with `Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })` (not `toLocaleString` — which respects browser locale, which is the bug). Parse with `Number.parseFloat` after stripping non-`[0-9.]`. Apply to *every* numeric input that takes empirical/default float values, not just slippage — grep for `toLocaleString` in `frontend/src/`.
**Files:** Slippage input lives in the right-side Settings panel (RiskPanel.tsx / SettingsPanel.tsx — grep `slippage_bps`).
**Effort:** ~1 hour including audit of similar inputs.
**Tag:** `[easy] [hardening]`

---

## T2 — Workflow friction (three bundles: layout / workflow / copy)

### F-UX5 · Chart pane min-height — don't crush candles
**Source:** Q2 P1.1. **Visual payoff per LOC: highest in the plan.**
**Problem:** Add BB + MACD + RSI + ADX + Stoch → main candle pane collapses to ~50 px; only the BB lines and price ribbon are visible. Equal-share allocation punishes the common "BB + 2 oscillators" setup.
**Fix:** Main candle pane gets `min-height: 40%` of the chart column. Sub-panes share the remainder, each with its own `min-height: 80px` so they don't degenerate either; overflow scrolls.
**Files:** `frontend/src/features/chart/Chart.tsx` — read it before editing per CLAUDE.md; the flex column is one of three `IChartApi` instances stacked.
**Care — DO NOT ADD ResizeObserver.** `Chart.tsx:53` uses lw-charts v5 `autoSize: true`. Per `project_lwcharts_v5_autosize.md` memory and F218 postmortem, pairing v5 `autoSize` with an external `ResizeObserver`-driven `applyOptions()` causes the 60Hz repaint loop. Resize redistribution is already handled by the existing debounced `onLayout` path (around `Chart.tsx:730-738`) for the react-resizable-panels boundary. For the new intra-chart min-height logic, route through the existing `syncWidthsRef` on the panel-resize handler. If sync issues appear under resize, fix via the existing subscription — never via a new ResizeObserver.
**Effort:** ~1 hour. Browser-verify with the exact 5-indicator repro AND **in combination with F-UX6** (per coherence review): collapse rule editor + 5 indicators → confirm candle pane is genuinely usable, not just nominally above the threshold.
**Tag:** `[medium] [polish]`

### F-UX6 · Collapse rule editor after a backtest run
**Source:** Q2 P1.2 + P3.5.
**Problem:** Equity / MC / Rolling / Hold Time / Detail charts render into ~120–180 px because the rule editor consumes the upper half. Once a run is loaded, the user wants to read results.
**Fix:** Collapse the rule-editor panel into the existing adaptive-header chip when a result lands; restore via chevron click.

**Trigger semantics (per design review — pinning these now to avoid implementer drift):**
- **Auto-collapse fires once per page load** — not "once per session" (ambiguous across reloads). Specifically: `useState(false)` for `userHasManuallyToggled`; on first `result != null` transition AND `!userHasManuallyToggled`, collapse. After any manual toggle, never auto-collapse again until reload. (Simpler than persisting collapse intent to localStorage; localStorage holds *only* the chevron's last manual state across sessions if we want, but v1 ships without persistence — re-evaluate after we see real use.)
- **Subsequent runs while collapsed:** chip summary text updates to reflect the rules that just ran. No flicker — chip is the same element, only its inner text changes.
- **Unsaved rule edits in progress:** if the user has the editor open AND has edited a rule field since last run AND a new result lands (e.g. background optimizer Apply & Re-run via F-UX7), DO NOT auto-collapse — the user's hand is on the keyboard. Detect via a `dirtyRules` boolean already implied by the Save-As button's enabled state.

**Chip content for partially-filled rules:** When a rule has no threshold (and Run is therefore disabled per F-UX3), the chip omits that rule and shows `… (1 incomplete rule)`. Don't render a malformed summary.

**Files:** `frontend/src/App.tsx` (rule editor + results layout). The adaptive header line (e.g. `Direction: long entry · goes flat on flip, re-enters on signal`) is the anchor for the collapsed summary.
**Effort:** ~2 hours. Behaviour-change item — browser-verify the three states above PLUS in combination with F-UX5 (combined visual payoff per coherence review).
**Tag:** `[medium] [polish]`

### F-UX7 · "Apply best to rules" in Optimizer + "Apply & Re-run"
**Source:** Q1 U4. **Biggest workflow win per Q1.**
**Problem:** Optimizer winner row (Buy=35/Sell=65) is read-only. User re-types into rule rows. Clicking the row does nothing.
**Fix:** Two buttons on the winner row: `Apply to rules` (writes the params back to Rule[].value) and `Apply & Re-run` (apply + immediately fire backtest). Selection of any other result row exposes the same buttons inline.

**Encoding (per feasibility review — concrete):** `paramOptions.ts` produces flat string paths (`buy_rule_${i}_value`, `buy_rule_${i}_params_${key}`, `stop_loss_pct`, `slippage_bps`, etc.). Writeback needs a `applyParamPath(req, path, value) → req'` resolver that mirrors how the optimizer *reads* the path during sweep. Add this resolver alongside `buildParamOptions` so the read/write pair is colocated and tested together. **Do not** introduce a parallel encoding (string label like "Buy Rule 1 Threshold") — that would drift from the optimizer's own decoder.

**Dirty-state interaction (per design review):**
- After Apply, the strategy is dirty (rules diverge from the loaded saved-strategy). Surface the existing dirty indicator (the Save-As button's enabled state) — don't add a new one, but make sure the existing one fires.
- Cross-item interaction with F-UX1: after Apply & Re-run, the Param 1 selector should *not* reset to the new default (per F-UX1) — it stays where the user set it, so they can re-run the optimizer over the new neighborhood without re-picking parameters.

**Button states during in-flight re-run:** `Apply to rules` is instant (no loading state needed). `Apply & Re-run` disables both buttons while the backtest is running (reuse the existing Run-Backtest disabled pattern); label stays "Apply & Re-run" — no spinner-text swap (avoids label flicker that confuses screen readers).

**Files:** `frontend/src/features/strategy/OptimizerPanel.tsx`, `frontend/src/features/strategy/WalkForwardPanel.tsx`, `frontend/src/features/strategy/paramOptions.ts` (add `applyParamPath`), `frontend/src/App.tsx` (re-run hook).
**Effort:** ~3 hours including the resolver + 2 unit tests (apply path round-trip, multi-rule path).
**Tag:** `[medium] [polish]`

### F-UX8 · `timeScale().fitContent()` on data refresh
**Source:** Q1 U7.
**Problem:** Switch KO 1H → NVDA Daily; candle pane stays zoomed in a ~1-month window on the right edge while equity curve covers 2020–2026. No fit-data button.
**Fix:** Call `chart.timeScale().fitContent()` on the three IChartApi instances **only when ticker, interval, or range actually change** — NOT on every data-loaded effect (per feasibility review: would override user zoom on every intraday auto-refresh poll). Track the last-applied `(ticker, interval, from, to)` tuple in a ref; fit only when it diverges from current. Guard with the existing teardown-safety pattern (read `chartRef.current` dynamically + try/catch — see CLAUDE.md → Key Bugs Fixed → "Chart teardown race").
**Files:** `frontend/src/features/chart/Chart.tsx`. There's already a data-loaded effect — extend it with the divergence check.
**Effort:** ~45 minutes incl. teardown-safety verification + the divergence-check test.
**Tag:** `[easy] [polish]`

### F-UX9 · Add `is_above_signal` / `is_below_signal` MACD conditions
**Source:** Q1 U5.
**Problem:** State conditions for MACD are limited to "Is above value" / "Is below value". The canonical regime use ("MACD currently above its signal line") requires a workaround (`MACD Is above 0`).
**Fix:** Extend `signal_engine.py` MACD condition list with `is_above_signal` / `is_below_signal` (compare `macd_line[i]` to `signal_line[i]`, threshold ignored). Mirror to the UI dropdown — UI label "Is above signal" / "Is below signal", backend enum `is_above_signal` / `is_below_signal` (snake_case throughout the codebase). Add to the threshold-free condition whitelist used by F-UX3.
**Care:** Same field naming convention as the existing `crosses_above_signal` / `crosses_below_signal`. Reuse the already-computed signal-line series. Rule engine remains direction-agnostic per CLAUDE.md → Short Selling — no special-casing for short rules.
**Effort:** ~1 hour incl. backend tests.
**Tag:** `[medium] [arch]`

### F-UX10 · Results panel auto-scroll on completion
**Source:** Q1 U6.
**Problem:** Equity Curve / Optimizer / WFA tables land below the fold of an internal sub-scroller. No scrollbar hint. Three times the reviewer had to resize the viewport to see the headline they'd just generated.
**Fix:** On the `result-updated` effect for backtest / optimizer / WFA completion: `panelRef.current?.scrollTo({top: 0, behavior: 'smooth'})`. Plus CSS `scrollbar-gutter: stable` + `::-webkit-scrollbar { width: 8px }` to surface that the scrollbar exists.
**Effort:** ~30 minutes.
**Tag:** `[easy] [polish]`

### F-UX11 · "Aggregate" header combobox — hide when redundant; rename
**Source:** Q2 P2.3.
**Problem:** Header shows `Aggregate: 1h ▾ (View 1D / 1W / 1M)` next to sidebar `Interval: 1 Hour`. Two distinct concepts overloaded with overlapping labels.
**Fix:** Hide the header control when its value matches the sidebar interval (no-op state). Show only when an actual aggregation is in effect (e.g. 5 m data viewed as 1 D bars). Rename label to `Aggregate ▾` — drop "View".
**Effort:** ~30 minutes.
**Tag:** `[easy] [polish]`

### F-UX12 · Direction segmented control above LONG / SHORT tabs
**Source:** Q2 P2.4.
**Problem:** Direction is implicit — inferred from which rule tabs have rules in them. New user wastes a minute hunting.
**Fix:** Small `Long / Short / Both` segmented control above the LONG/SHORT tabs. Maps to existing `direction` field on `StrategyRequest`/`BotConfig` (CLAUDE.md → Short Selling section).

**Mute semantics — pinning the design call (per design review):** When user picks `Long-only`, the SHORT tab is **hidden entirely** (not dimmed, not "muted by Long mode"-labelled). When toggled back to `Both`, short rules reappear exactly as they were. Rationale: a hidden tab is unambiguous; a dimmed tab with grayed-out rules invites "is this active or not?" confusion every time the user opens the editor. The existing `Rule.muted` field is **not** reused for this — it's the per-rule mute (a different concept). Direction state lives only in the `direction` field. No data is lost: short rules sit in the request payload, just visually inaccessible while direction == "long".

**Persistence:** `direction` is already part of saved-strategy payload via `StrategyRequest`. No schema change.

**Files:** `frontend/src/App.tsx` (rule editor section), `frontend/src/features/strategy/RuleEditor.tsx` (or wherever LONG/SHORT tabs render — grep `direction` in `frontend/src/features/strategy/`).
**Effort:** ~1.5 hours.
**Tag:** `[medium] [polish]`

### F-UX13 · IBKR-disabled tooltip copy fix
**Source:** Q2 P2.2.
**Problem:** Disabled IBKR data-source button says "Set ALPACA_API_KEY in .env to enable" — wrong product.
**Fix:** "Set `IBKR_HOST` + `IBKR_PORT` in `backend/.env` and start IB Gateway." (Per `project_ibkr.md` memory.)
**Effort:** 1 line.
**Tag:** `[easy] [polish]`

### F-UX14 · Right-side Settings — show effective values for per-direction overrides
**Source:** Q2 P2.5.
**Problem:** Stop-Loss / Time Stop / Trailing appear in both the right global panel and inside LONG/SHORT tabs. Per-direction fields show `"global"` placeholder but the *effective* value isn't visible.
**Fix:** Render `Effective: 2 % (from global)` next to each per-direction override that isn't overridden. When override is set, render `Effective: 1.5 % (long override)`.
**Effort:** ~1 hour.
**Tag:** `[easy] [polish]`

---

## T3 — Polish (bundle last)

Each is small; one commit per bucket of 3–5 items.

- **F-UX15** Sticky current-strategy metrics strip above tab bar. `RSI 35/65 daily · 17 trades · +412% · Sharpe 1.07 · MaxDD −36%`. (Q1 U14, `[easy] [polish]`)
- **F-UX16** Two-param optimizer heatmap (6×6 Sharpe, cold→hot). The table tells you the peak; the heatmap tells you whether the peak is *real*. (Q1 U12, `[medium] [polish]`)
- **F-UX17** Param picker `<optgroup>` per rule when >2 rules present. (Q1 U10, `[easy] [polish]`)
- **F-UX18** Tooltip on Enable Signal Trace checkbox: "Records every rule evaluation per bar — slower, useful for debugging missed signals." (Q1 U13, `[easy] [polish]`)
- **F-UX19** `aria-valuemax="0"` audit — reflect true bound or omit attribute on numeric spinbuttons. (Q2 P3.1, `[easy] [polish]`)
- **F-UX20** `react-resizable-panels` autoSaveId casing + drop unrecognised `onLayout`. Console-noise hygiene. (Q2 P3.2, `[easy] [polish]`)
- **F-UX21** Trades-table P&L: bold + 3 px left-border accent. (Q2 P3.3, `[easy] [polish]`)
- **F-UX22** Discovery tab placeholder copy: "Preview only — scanner coming soon" + 2-col widget layout so whitespace reads as intentional. (Q2 P3.4, `[easy] [polish]`)
- **F-UX23** Standardise Sensitivity / Optimizer / WFA control rows via shared CSS / Tailwind classes (NOT a shared component — abstraction not earned per scope review; do CSS-only first, only extract a component if a second motivating need appears). (Q2 P3.6, `[easy] [polish]`)
- **F-UX24** Default-viewport rails: narrow Sidebar/Settings rails by ~40 px each below 1440 px width to give results column breathing room. (Q1 U8, `[easy] [polish]`)

---

## T2b — New feature requests (user-added 2026-05-16)

Four additions surfaced after the postmortems. Sized like T2 items; sequenced into a new bundle after Bundle D so the postmortem-driven fixes ship first.

### F-UX26 · Indicator sidebar — UI/UX overhaul + reordering + color options
**Source:** User feature request 2026-05-16.
**State of existing code (per feasibility review — IMPORTANT):** sidebar lives at `frontend/src/features/sidebar/IndicatorList.tsx` + `Sidebar.tsx` (NOT `features/indicators/`). `PRESET_COLORS` already exists with **10 colors** (not 6), `IndicatorInstance.color` is already a per-instance field consumed by `SubPane.tsx:123`, and expand-on-click is already implemented (`isExpanded` at line 135). Verify what `savedStrategies.ts` serializes before assuming a schema change is needed — `color` may already be persisted.

**Split into two items (per scope review — cosmetic and schema-touching work shouldn't ship together):**

#### F-UX26a · Sidebar layout pass + palette expansion (v1)
**Fix:**
1. Denser row height, single-line collapsed summary (`RSI · 14 · Wilder ▾`). The expand-on-click panel already exists — tighten the collapsed state.
2. Expand `PRESET_COLORS` from 10 → 14–16 colors (Tailwind 500-tone slice).
3. Add an optional "custom hex" input as a final swatch in the palette (text input, validated on blur).
**Files:** `frontend/src/features/sidebar/IndicatorList.tsx`, `frontend/src/features/sidebar/Sidebar.tsx`.
**No schema change** — color persistence already works via existing `IndicatorInstance.color`.
**Effort:** ~2 hours.
**Tag:** `[medium] [polish]`

#### F-UX26b · Drag-reorder indicator panes (v2)
**Fix:**
- Drag handle per row; reorder writes a new array order to the indicators array. Pane order in the chart follows the array.
- **Critical:** pane order change → Chart.tsx must rebuild affected sub-panes (`chart.remove()` + reinit via React unmount of `SubPane` for that instance, NOT mutate live `IChartApi`). lw-charts v5 in-place series reorder is fragile — re-create the sub-pane component. Use a stable React `key` per indicator id so React unmounts/remounts cleanly on order change.
- Use HTML5 drag-and-drop (no new dep). If F-UX28 lands first and pulls in `@dnd-kit`, share it.
**Files:** `frontend/src/features/sidebar/IndicatorList.tsx`, `frontend/src/features/chart/Chart.tsx` (verify `SubPane` is keyed by indicator id).
**Effort:** ~3 hours.
**Tag:** `[medium] [arch]`

### F-UX27 · Multi-timeframe rules in strategy builder
**Source:** User feature request 2026-05-16.
**Problem:** Only regime rules support an HTF timeframe. Entry/exit rules are locked to the base timeframe.

**Reality check (per feasibility review — corrected):** the plan previously claimed "`eval_rules()` already handles HTF for regime via per-rule timeframe lookups; reuse that path." This is wrong. `eval_rules()` (`backend/signal_engine.py:543`) takes a single `indicators: dict[str, pd.Series]` keyed by indicator name with **no timeframe dimension**. Regime HTF is handled *outside* `eval_rules()` by `_compute_regime_series()` in `backend/routes/backtest.py:223`, which computes a separate `htf_indicators` dict and runs `eval_rules` over HTF bars in a separate pass. There is no per-rule TF dispatch inside `eval_rules`.

**Architectural choice (must be made before implementation):**
- (a) Extend `indicators` to `dict[(name, tf), Series]` and teach `eval_rule` to look up by `r.timeframe`. Cleanest long-term; touches every `eval_rules` callsite.
- (b) Pre-resolve per-rule series into a flat dict with synthetic keys (e.g. `RSI@1d`). Less invasive; key-encoding becomes a contract.
- (c) Partition rules by timeframe, evaluate each subset on its own pre-aligned indicators, AND the boolean masks back together. Cleanest reuse of `_compute_regime_series` pattern.

**Recommendation:** start with (c) for v1 — it mirrors how regime already works and minimises engine churn. Move to (a) only if (c) becomes painful.

**Split into two items (per scope review):**

#### F-UX27a · Single-HTF rules (v1)
- One HTF per rule allowed; multiple rules can each have their own HTF but the engine fetches at most a small fixed set of distinct HTFs.
- **Schema:** add optional `timeframe: str | None` to `Rule` (`backend/signal_engine.py:28`). Default `None` = base timeframe. Additive — old saved strategies unaffected.
- **Engine:** `_compute_regime_series`-style HTF pre-computation in `backend/routes/backtest.py`, partition entry/exit rules by their `timeframe` field, run `eval_rules` per partition, AND results. Use `align_htf_to_ltf` from `backend/shared.py` (already imported at `routes/backtest.py:10`).
- **UI:** per-rule timeframe dropdown next to the indicator chip in `frontend/src/features/strategy/RuleRow.tsx`. Hide when `timeframe == base` (default).
- **WFA strip:** `routes/walk_forward.py` `model_copy` already strips regime at line 298. Extend to also strip `timeframe` from every rule in `entry_rules`, `exit_rules`, and short-side variants. Not a one-liner — traverse all rule lists. Surface a notice in the WFA UI: "Multi-TF rules disabled in walk-forward — same constraint as regime."
- **Chart rendering:** v1 does NOT render HTF indicator series on the base chart (forward-fill visual confusion). Summary chip shows TF inline: `RSI<35 (1h) · MACD>signal (1d)`.
- **Effort:** ~6 hours.
- **Tag:** `[hard] [arch]`

#### F-UX27b · Mixed-HTF parallelism + HTF chart rendering (v2 — deferred)
- Optimise the multiple-distinct-HTFs case (parallel `_fetch` fan-out instead of sequential).
- Render HTF indicator series on dedicated HTF chart panes.
- File only after F-UX27a ships and shows real pain.
- **Tag:** `[hard] [arch]`

### F-UX28 · Watchlist — reorder, collapsible groups, quick-add
**Source:** User feature request 2026-05-16.
**State of existing code (per feasibility review):** `frontend/src/features/watchlist/WatchlistPanel.tsx`, localStorage-only (confirmed lines 16, 22 — no backend persistence). Storage is `string[]` today.

**Split into two items (per scope review — schema migration shouldn't ship with cosmetic features):**

#### F-UX28a · Drag-reorder + quick-add (v1)
- Drag-reorder within the flat list. HTML5 drag is fine.
- Explicit `+ Add ticker` button in the watchlist header — keep the existing `+` icon AND add a labelled button. Input accepts comma-separated tickers for bulk add (parse `,` and whitespace, dedupe, uppercase).
- **No schema change** — array order in localStorage already encodes reorder.
- **Files:** `frontend/src/features/watchlist/WatchlistPanel.tsx`.
- **Effort:** ~2 hours.
- **Tag:** `[easy] [polish]`

#### F-UX28b · Collapsible groups (v2)
- Named groups; group rows expand/collapse; tickers belong to exactly one group.
- **Schema migration:** `string[]` → `{groups: [{name, tickers: string[], collapsed: bool}], ungrouped: string[]}`. Migrate on load (old format → all tickers in `ungrouped`).
- **Invariants:** tickers unique across all groups. Drag from group A to group B *moves*, doesn't copy. Document the invariant in a comment at the data shape.
- **Shadow paths (per feasibility review):** handle (a) corrupt JSON in localStorage → reset to empty state with a one-time toast, (b) old-format detected → migrate, (c) duplicate-detection on drop → drop the source-side reference, keep the target.
- **Files:** `WatchlistPanel.tsx` + a small `watchlistStorage.ts` helper for migration.
- **Effort:** ~3 hours.
- **Tag:** `[medium] [polish]`

### F-UX29 · Collapsible charts panel (disable charts when collapsed)
**Source:** User feature request 2026-05-16.
**Problem:** Chart panel is always live. F215 already eliminated the 60Hz repaint loop, but the chart is still mounted, holding three `IChartApi` instances, and re-fetching on data refresh.

**Profile first (per scope review):** before committing to full unmount, measure post-F215 idle CPU with the chart mounted but inactive. If it's <1–2 %, the simpler approach (cancel auto-refresh AbortController; let lw-charts idle; `display: none` the panel) is sufficient and ships in 30 minutes instead of 3 hours. If measurable CPU remains, proceed with full unmount.

**Decision rule:**
- Idle CPU < 1.5 % post-F215 → **F-UX29-lite:** cancel-subscriptions + `display: none`. ~45 min.
- Idle CPU ≥ 1.5 % OR user explicitly wants the lw-charts memory back → **F-UX29-full:** unmount via React conditional render (see below). ~3 hours.

**F-UX29-full mechanism (per feasibility review — React-driven, not imperative):**
- Layout container conditionally renders `<Chart ... />` based on `collapsed` state. React's unmount cycle invokes the existing teardown paths (null refs before `chart.remove()` at `Chart.tsx:380, 388` — pattern already correct). `SubPane.tsx` owns its own chart refs and cleans them up on its own unmount.
- **Do NOT add imperative `chart.remove()` calls in a "collapse handler"** — that competes with React's unmount and risks the teardown-race. Let React drive.
- The chevron simply toggles a `collapsed: boolean` in App.tsx state; the rest follows.

**Re-fetch on restore:** there is no client-side cache today (`_fetch()` TTL cache is server-side). On restore, the chart re-issues the HTTP fetch; the server cache TTL handles the hot-restore case (<2 min intraday / <1 hour historical). Don't introduce a client-side cache as part of this — that's a separate item if needed.

**Other care:**
- `display: none` is **not** an alternative for the full-unmount path — per CLAUDE.md, it keeps components mounted (F152 pattern). It IS the right tool for F-UX29-lite (which deliberately keeps them mounted with subscriptions cancelled).
- Persist `collapsed` state to localStorage so power users who never want the chart get the same layout across reloads.
- AbortController for in-flight fetches at collapse time: already present per F215 — wire to the new collapse handler.

**Files:** `frontend/src/features/chart/Chart.tsx`, `frontend/src/features/chart/SubPane.tsx` (verify cleanup), `frontend/src/App.tsx` (conditional render).
**Effort:** profile (15 min) → lite (45 min) OR full (3 hours).
**Tag:** `[medium] [arch]`

---

## Deferred (separate plan)

- **F-UX25** Strategy lifecycle: fork-from-this, diff, commit note, version tag. Extends the existing `⇄ Compare` button. (Q1 U11, `[hard] [arch]`) — Worth its own plan; touches save model, requires brainstorming on project notion.

---

## Sequencing

1. **Bundle A (T1, integrity):** F-UX1, F-UX2, F-UX3, F-UX4. ~6 hours total. Ship as one PR — these are independent files but share "data integrity" framing in the PR description.
2. **Bundle B (T2, layout):** F-UX5, F-UX6, F-UX10. Layout-focused; **verify F-UX5+F-UX6 in combination** (per coherence review): the actual "results pane is usable now" assertion only holds with both landed.
3. **Bundle C (T2, workflow):** F-UX7, F-UX8, F-UX9. Optimizer/chart workflow.
4. **Bundle D (T2, copy/controls):** F-UX11, F-UX12, F-UX13, F-UX14. Small components + copy.
5. **Bundle F (T2b, new features — v1 only):**
   - F-UX28a (watchlist reorder + quick-add, ~2h) + F-UX26a (sidebar layout + palette, ~2h) — small, no schema, ship together.
   - F-UX29 (profile + lite-or-full collapse, ~1–3h) — independent.
   - F-UX26b (drag-reorder panes, ~3h) — needs F-UX26a; own PR.
   - F-UX28b (watchlist groups + migration, ~3h) — own PR (schema migration isolated).
   - F-UX27a (single-HTF rules, ~6h) — largest, own PR, schema-touching.
   - **v2 deferred:** F-UX27b (mixed-HTF + HTF chart panes).
6. **Bundle E (T3 polish):** All F-UX15..24 in 2–3 sub-bundles by file proximity.

## Verification protocol (per CLAUDE.md Live-Browser UI Verification)

Every T1, T2, and T2b item is browser-verified before commit:
- F-UX1: pick "Slippage" in saved config → reload → assert dropdown reset to first rule threshold.
- F-UX2: set 1h + 6y range → assert clamp chip visible + click "Use effective range" → assert From input updates AND React state updates (not just DOM). Then 1h + range entirely before clamp window → assert 404 case renders chip in error form.
- F-UX3: empty MACD value with "Is above" → assert red border + Run disabled. Set condition to "crosses_above" (threshold-free) with no value → assert no red border (whitelisted).
- F-UX4: switch Chrome locale to `sv-SE` → assert slippage input round-trips `3.09` not `3,09`.
- F-UX5 + F-UX6 **combined**: add 5 indicators + run backtest → screenshot. Assert (a) main candle pane > 40 % of chart column AND (b) rule editor auto-collapsed AND (c) results pane has usable vertical space (>400px). Single-item screenshots of either alone are insufficient.
- F-UX6 (additional): start a backtest while editor is dirty (mid-edit) → assert NO auto-collapse. Verify chip text omits incomplete rules.
- F-UX7: run optimizer → click Apply → assert rule rows updated AND Save-As button enabled (dirty state). Click Apply & Re-run → assert button disabled during in-flight, re-enabled on completion. Verify Param 1 selector did NOT reset to F-UX1's default.
- F-UX8: switch ticker + interval → assert all 3 chart panes auto-fit. Then wait through an auto-refresh poll → assert user's manual zoom is preserved (no fit on poll).
- F-UX10: run backtest with Detail tab active → assert auto-scrolled to top.
- F-UX12: toggle Long-only → assert SHORT tab hidden (not dimmed). Toggle to Both → assert short rules return with original values intact.
- F-UX26a: add 5 indicators → screenshot the denser collapsed rows; expand one → assert param chips visible. Pick a new palette color → assert chart series updates + persisted across reload.
- F-UX26b: drag RSI above MACD → assert chart pane order matches; assert React unmounts the moved SubPane (use react-devtools or stable-key inspection — not just visual).
- F-UX27a: add `RSI < 30 (1h base)` + `MACD is_above_signal (1d HTF)` rule → assert backtest runs without error AND the HTF rule actually gates trades (compare to no-HTF run, expect different trade count). Try same setup in WFA → assert HTF stripped + UI notice visible.
- F-UX28a: drag-reorder tickers → reload → assert order preserved. Quick-add `aapl, msft, googl` → assert all three uppercased and added, dedupe works if `AAPL` already present.
- F-UX28b: create group, drag ticker from ungrouped to group → assert moved (not copied — source ticker removed). Collapse group + reload → assert restored. Corrupt localStorage manually → reload → assert empty state with toast (no white screen).
- F-UX29: first measure idle CPU with chart mounted (Chrome devtools Performance tab, 10s trace). Decide lite vs full per the decision rule. Then verify: collapse → assert no chart-related fetches OR repaints in trace. Restore → assert chart renders with same data (lite: instant; full: HTTP refetch hitting server cache).

T3 items verified via screenshot diff only — no behavioural assertion needed.

## Risks / things to watch

- **F-UX2** (clamp): the v1 frontend-only path computes effective dates from the interval limit constants. If yfinance/Alpaca silently changes a limit, the chip will lie. Defer the backend-truth path (v2) until that becomes a real problem.
- **F-UX5** + **F-UX6** interact — verify them in combination, not sequentially. Min-height + collapse-rule-editor together is what actually fixes "results pane content area is squeezed" (Q2 P1.2).
- **F-UX5** must NOT introduce a `ResizeObserver` (F218 trap, `project_lwcharts_v5_autosize.md`). Use existing debounced `onLayout` paths.
- **F-UX7** (Apply best): writeback resolver `applyParamPath(req, path, value)` lives next to `buildParamOptions` so the read/write pair stays colocated. No string-label parallel encoding.
- **F-UX9** (MACD is_above_signal): the rule engine is direction-agnostic per CLAUDE.md → Short Selling. New conditions must respect that — no special-casing for short rules.
- **F-UX12** (direction toggle): hide-not-dim is the chosen design. Don't reuse `Rule.muted` — that's a different concept.
- **F-UX26** state-of-code: 10 colors already exist (not 6), `IndicatorInstance.color` already persists, expand-on-click already works. Plan extends — does not rebuild.
- **F-UX26b** (pane reorder): use React conditional render + stable key on `SubPane` to drive unmount/remount. Do NOT mutate live `IChartApi` series order — risks F218-class repaint pathology.
- **F-UX27a** (single-HTF rules): pick architectural approach (c) — partition rules by timeframe, evaluate subsets, AND results. Mirrors regime. WFA strip extends the existing `model_copy` at `routes/walk_forward.py:298` to traverse every rule list.
- **F-UX27**: HTF chart pane rendering is explicitly deferred to F-UX27b.
- **F-UX28b** (watchlist groups): tickers unique across all groups; drag-between-groups is a move, not a copy; corrupt localStorage falls back to empty state with toast.
- **F-UX29** (collapse chart): profile first. Drive unmount via React conditional render, not imperative `chart.remove()`. `display: none` is only correct for F-UX29-lite (keeps mounted on purpose) — for the full-unmount path, use conditional render.

## Estimated total effort

- T1 (Bundle A): ~6 hours, one PR
- T2 (Bundles B/C/D): ~10 hours, three PRs
- T2b v1 (Bundle F): ~17 hours, five PRs
  - F-UX28a + F-UX26a combined: ~4h
  - F-UX29: ~1–3h (profile-gated)
  - F-UX26b: ~3h
  - F-UX28b: ~3h
  - F-UX27a: ~6h
- T2b v2 deferred: F-UX27b (mixed-HTF + HTF chart panes)
- T3 (Bundle E polish): ~6 hours, two PRs

**~39 hours, 11 PRs total.** Postmortem-driven bundles ship first; new feature requests follow with v1/v2 splits so schema-touching work is isolated. Sized for the overnight builder one bundle per night, or 1–2 evenings of focused interactive work per bundle.
