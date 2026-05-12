# C28 Walk-Forward Analysis — Implementation Plan

## Goal

Walk-forward analysis (WFA) solves the silent overfitting problem: a strategy optimized over its full backtest history looks good precisely because the parameters were chosen on that data. WFA instead partitions history into rolling (or anchored) in-sample (IS) windows where parameters are selected by Sharpe, then evaluates each IS winner on the adjacent held-out out-of-sample (OOS) window and rolls forward. The user gets a per-window table of IS vs OOS metrics, a concatenated/rescaled OOS equity curve that is never in the optimizer's view, a Walk-Forward Efficiency (WFE), and a parameter-stability CV across windows.

**WFE definition (v1).** WFE = `mean(per-window OOS Sharpe) / mean(per-window IS-winner Sharpe)`. This is the mean-of-Sharpes form, not the Sharpe-of-the-aggregate form. The two are not algebraically identical: the aggregate-Sharpe form would compute the Sharpe of the stitched OOS daily returns and divide by the IS-winner mean. The mean-of-Sharpes form is what most practitioner tools (TradeStation WFO, AmiBroker) emit; it's also more robust to one window's outlier returns dominating the ratio. A future C-item can add the aggregate-Sharpe form as a secondary number alongside.

Success criterion: a strategy that scores Sharpe ≥ 1.5 on full-history backtest but whose parameters shift dramatically window-to-window should show WFE < 0.5 and per-window `stability_tag: "spike"` on several windows, giving the user a concrete signal to distrust deployment. A genuinely robust strategy should hold WFE ≥ 0.6 across ≥ 6 windows.

---

## Locked Decisions

1. **Rolling (fixed-width IS) is the default.** `expand_train: bool = False`. `True` = anchored/expanding IS (sklearn `TimeSeriesSplit`-style). Default is rolling because sklearn-naive anchoring misleads practitioners.
2. **IS objective v1: Sharpe peak + neighborhood-stability tag.** After picking the IS-Sharpe winner, check its direct grid neighbors (±1 step per dimension). If ≥ 60 % of those neighbors also rank in the top quartile of IS Sharpe scores for that window, tag `"stable_plateau"`; else `"spike"`. Cluster-centroid selection deferred to C29.
3. **Window units are bar counts:** `is_bars: int`, `oos_bars: int`, `gap_bars: int = 0`. Calendar offsets deferred to C31.
4. **Non-overlapping OOS by default.** `step_bars: int` is an explicit field with `default = oos_bars`. Making it explicit allows future overlapping-step experiments without breaking the API.
5. **Min-trades validation per window:** `min_trades_is: int = 30`. Windows below this threshold are tagged `"low_trades_is"` but do NOT abort the run. A per-run warning count is returned.
6. **Combo cap per window:** reuse `_MAX_COMBOS = 200` from `backtest_optimizer.py`. The cap applies to the IS grid per window, not to the entire WFA run.
7. **Equity stitching:** rescale at window boundaries. Window N's OOS equity curve is multiplied by `prev_final_equity / new_initial_capital`. First window scale = 1.0. This removes the capital-reset sawtooth. Time format (unix int for intraday, `"YYYY-MM-DD"` string for daily+) must be preserved through the rescaling step.
8. **No `end > start` guard in `StrategyRequest`.** The WFA route must validate every IS and OOS slice's start/end before calling `run_backtest()` and raise HTTP 400 for degenerate windows rather than surfacing a 500.
9. **Frontend placement:** new `WalkForwardPanel.tsx` sub-tab in `Results.tsx` alongside `OptimizerPanel`. Add `'walk_forward'` to the `ResultsTab` union. No changes to `App.tsx`.
10. **Neighborhood-stability threshold = 60 %.** `STABILITY_THRESHOLD = 0.60` module-level constant. ≥ 60 % of grid-neighbors of the IS winner ranking in the top quartile of IS Sharpe scores → `"stable_plateau"`; else `"spike"`.
11. **Window-count thresholds:** hard 400 if computed `len(windows) < 2` (WFE meaningless from a single window); soft warning logged + `low_windows_warn: bool` in response if `2 ≤ len(windows) < 6`. The 6-window threshold is the practitioner-validated minimum for WFA results to carry statistical weight; below 2 the WFE ratio is undefined in practice.
12. **Stitched-equity chart is self-contained.** `WalkForwardPanel.tsx` creates its own `IChartApi` via the standard `createChart` pattern; it does NOT share the main chart instance from `Chart.tsx`. Rationale: the chart-teardown race documented in `CLAUDE.md` (Key Bugs Fixed) is the kind of cross-component coupling we want to avoid. The optimizer panel doesn't share the main chart either.
13. **Regime is always stripped in v1.** WFA hardcodes `req.regime = None` on every cloned `StrategyRequest`. No request field exposes a toggle — there's no alternative behavior in v1. `_compute_regime_series()` HTF lookback extends before window start and can clip yfinance intraday limits silently; regime-aware WFA is a non-trivial design problem (per-window HTF warmup precheck) and is deferred to a future C-item. The toggle field will be added when there's a second behavior to choose between.
14. **Intraday intervals are supported in v1 (revised post-implementation).** All `_INTRADAY_INTERVALS` (`1m`/`2m`/`5m`/`15m`/`30m`/`60m`/`90m`/`1h`) are accepted. Per-window IS/OOS boundary strings are formatted via the `_format_boundary(ts, interval)` helper: daily+ intervals use `"%Y-%m-%d"`, intraday uses `"%Y-%m-%d %H:%M:%S"` so adjacent IS-end and OOS-start bars on the same calendar day don't collide on string equality (which would cause provider re-fetch of the full day, leaking IS into OOS). `YahooProvider.fetch()` was loosened to accept both formats via `pd.Timestamp()` and passes `datetime` objects to `yf.Ticker.history()`; `AlpacaProvider.fetch()` already accepted both via `pd.Timestamp(end, tz='UTC')`. Provider day-limits for intraday (`_INTERVAL_MAX_DAYS`: 7 for 1m, 60 for 5m/15m/30m, 730 for 60m/1h) surface in the "not enough bars" 400 error message so the user knows why their wide-range request fell short.

---

## Open Decisions for Implementer

Two module-level constants:

| Constant | Default | Notes |
|---|---|---|
| `_WFA_TIMEOUT_SECS` | `120` | Overall request budget (2× optimizer). |
| `STABILITY_THRESHOLD` | `0.60` | Fraction of grid-neighbors that must rank in the top quartile of IS Sharpe to tag the IS winner as `"stable_plateau"` rather than `"spike"`. Worth revising based on real-strategy spike-vs-plateau rates after launch. |

Inline literals (not constants):
- 6 — low-windows warning threshold
- 75 — neighbor top-quartile percentile cutoff
- 2 — hard minimum window count for a valid run

`_VALID_METRICS`, `_MAX_COMBOS`, and `_MAX_PARAMS` are imported from `backtest_optimizer.py` and not duplicated.

---

## File-by-file Changes

### New: `backend/routes/walk_forward.py`

**Pydantic models:**

```python
class WalkForwardParam(BaseModel):
    path: str
    values: list[float]          # same shape as OptimizeParam.values

class WalkForwardRequest(BaseModel):
    base: StrategyRequest
    params: list[WalkForwardParam]   # 1–3 params, same _MAX_PARAMS cap
    is_bars: int                     # must be > 0
    oos_bars: int                    # must be > 0
    gap_bars: int = 0
    step_bars: int = 0               # 0 means "use oos_bars" (non-overlapping default)
    expand_train: bool = False       # False = rolling, True = anchored
    metric: str = "sharpe_ratio"
    min_trades_is: int = 30
    # No disable_regime field — regime is always stripped in v1 (locked decision 13).
    # No interval field — base.interval is read directly; intraday is rejected at the route boundary (locked decision 14).

class WindowResult(BaseModel):
    window_index: int
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    best_params: dict[str, float]
    is_sharpe: float
    is_metrics: dict               # full summary dict from run_backtest()["summary"]
    oos_metrics: dict
    stability_tag: str               # "stable_plateau" | "spike" | "low_trades_is" | "no_oos_trades" | "no_is_trades"
    is_combo_count: int              # how many IS combos were evaluated (0 when stability_tag == "no_is_trades")
    scale_factor: float              # rescaling multiplier applied to this window's OOS curve (1.0 for no_is_trades)
    # Note: per-window equity curves are stitched server-side into WalkForwardResponse.stitched_equity.
    # Not exposed per-window in the response to keep payload small.

class WalkForwardResponse(BaseModel):
    windows: list[WindowResult]
    stitched_equity: list[dict]      # [{"time": str|int, "value": float}]
    wfe: float | None                # None when no OOS trades at all
    param_cv: dict[str, float]       # {param_path: CV} — std/mean of best_params per window
    total_combos: int                # sum of IS combos across all windows
    total_oos_trades: int
    low_trades_is_count: int         # windows where IS trades < min_trades_is
    low_windows_warn: bool           # True when 2 ≤ len(windows) < 6 (results are statistically thin)
    timed_out: bool
```

**Stability tag semantics (full union):**
- `"stable_plateau"` — IS winner's grid-neighbors mostly rank in the top quartile (passes anti-overfit check).
- `"spike"` — IS winner is an isolated peak; neighbors crater. Treat with suspicion.
- `"low_trades_is"` — IS window produced < `min_trades_is` trades; the optimizer had too little signal to choose meaningfully. Stability check skipped.
- `"no_oos_trades"` — IS picked a winner but OOS produced zero trades. Window contributes to `param_cv` but not to WFE.
- `"no_is_trades"` — IS grid search produced zero successful backtests (e.g. all parameter combos errored, or the window is too short for indicator warmup). Window is still listed in the response so the user sees the failure, but `best_params={}`, `is_metrics={}`, `oos_metrics={"num_trades": 0}`, `scale_factor=1.0`, and the window contributes nothing to `stitched_equity`. Excluded from WFE and `param_cv`.

**Route:** `POST /api/backtest/walk_forward`

**Algorithm (see Algorithm Detail section for full pseudocode):**
1. Validate request fields (param count, combo cap, metric, is_bars, oos_bars, step_bars resolution).
2. Fetch full bar list for the base request's ticker/interval/start/end to determine total bar count and convert bar-index windows to ISO date strings.
3. Compute window slices by integer bar index. Check `is_bars + oos_bars + gap_bars ≤ total_bars`.
4. For each window: run IS grid search (reuse `_apply_param` + `run_backtest`), pick IS winner, tag stability, run OOS backtest with winner params, rescale OOS equity.
5. Stitch rescaled OOS curves. Compute WFE, param CV.
6. Return `WalkForwardResponse`.

**Import what you can from existing modules; do not duplicate:**
- `from routes.backtest import run_backtest`
- `from routes.backtest_sweep import _apply_param`
- `from routes.backtest_optimizer import _VALID_METRICS, _MAX_COMBOS, _MAX_PARAMS`
- `from shared import _fetch, _format_time, _INTRADAY_INTERVALS`
- `from models import StrategyRequest`

**Implementation note on cloning:** `_apply_param()` already does `model_copy(deep=True)` internally on its `base` arg (`backtest_sweep.py:53`) and returns the new copy. So the pseudocode's pattern `is_req = base.model_copy(deep=True, update={...}); for path,v: is_req = _apply_param(is_req, path, v)` works correctly but double-copies on the first iteration. For per-window cloning of `base` (where we update `start`/`end` only), a shallow `model_copy(update={...})` is fine — but for the regime-strip clone at the top of the route, use `deep=True` to avoid leaking the original `regime` object reference.

---

### Modified: `backend/main.py`

Add two lines mirroring the optimizer pattern (lines 38 and 124 are the anchors):

```python
# After line 38 (after backtest_optimizer import):
from routes.walk_forward import router as walk_forward_router

# After line 124 (after backtest_optimizer_router registration):
app.include_router(walk_forward_router)
```

Anchor strings to grep before editing:
- `"from routes.backtest_optimizer import router as backtest_optimizer_router"` — add the new import on the next line
- `"app.include_router(backtest_optimizer_router)"` — add the new include_router on the next line

---

### New: `frontend/src/features/strategy/WalkForwardPanel.tsx`

**Props interface** (mirrors `OptimizerPanel`):

```typescript
interface Props {
  lastRequest: StrategyRequest
}
```

**State:**
- `isBarStr: string` — IS window bar count input (displayed as integer)
- `oosBarStr: string` — OOS bar count input
- `gapBarStr: string` — gap bars, default `"0"`
- `stepBarStr: string` — step bars, default `""` (empty = use OOS bars)
- `expandTrain: boolean` — checkbox, default `false`
- `metric: string` — select among `METRICS` (same constant as OptimizerPanel)
- `paramRows: (ParamRow | null)[]` — 3-slot param grid, same shape as OptimizerPanel
- `loading: boolean`
- `error: string`
- `result: WalkForwardResponse | null`

**Reset on `lastRequest` change**: same `useEffect`/`useRef` pattern as `OptimizerPanel`.

**Validation before submit:**
- `is_bars > 0`, `oos_bars > 0` (both required integers)
- At least one active param row with valid values
- `estimatedCombos ≤ 200` (same guard as Optimizer)

**Result display:**
- Summary bar: WFE badge (color: green ≥ 0.7, amber 0.5–0.69, red < 0.5), total OOS trades, timeout warning, `low_windows_warn` callout when true ("Only N windows — results are statistically thin").
- Per-window table: window index, IS dates, OOS dates, best params, IS Sharpe, OOS Sharpe, OOS return %, stability tag badge:
  - `"stable_plateau"` = green
  - `"spike"` = amber
  - `"low_trades_is"` = grey
  - `"no_oos_trades"` = red
  - `"no_is_trades"` = dark grey (distinct from `low_trades_is`; signals full window failure, not partial)
- Stitched equity chart: inline lightweight-charts `LineSeries` of `result.stitched_equity`. Reuse the `createChart` pattern from `Results.tsx` (same `ColorType`, dark theme). Self-contained chart instance (locked decision 12); does not reuse the main chart from `Chart.tsx`. Keep chart simple — no crosshair sync needed here.
- Param CV table: per-param CV value (raw number, no interpretive label — let the user read the data).

**Inline types** (same pattern as `OptimizerPanel`):

Import `StrategyRequest` from shared types (matches OptimizerPanel.tsx line 2: `import type { StrategyRequest } from '../../shared/types'`). Declare the response types inline:

```typescript
interface WalkForwardResponse {
  windows: WindowResult[]
  stitched_equity: Array<{ time: string | number; value: number }>
  wfe: number | null
  param_cv: Record<string, number>
  total_combos: number
  total_oos_trades: number
  low_trades_is_count: number
  low_windows_warn: boolean
  timed_out: boolean
}

interface WindowResult {
  window_index: number
  is_start: string; is_end: string
  oos_start: string; oos_end: string
  best_params: Record<string, number>
  is_sharpe: number
  is_metrics: Record<string, number>
  oos_metrics: Record<string, number>
  stability_tag: "stable_plateau" | "spike" | "low_trades_is" | "no_oos_trades" | "no_is_trades"
  is_combo_count: number
  scale_factor: number
}
```

Use the same `s` style object convention as `OptimizerPanel` for consistency.

---

### Modified: `frontend/src/features/strategy/Results.tsx`

**Four changes — grep for each anchor before editing:**

1. **Import** (after the `OptimizerPanel` import line):
   ```typescript
   import WalkForwardPanel from './WalkForwardPanel'
   ```
   Anchor: `"import OptimizerPanel from './OptimizerPanel'"`

2. **ResultsTab union** (line 19):
   ```typescript
   export type ResultsTab = 'summary' | 'equity' | 'trades' | 'trace' | 'session' | 'monte_carlo' | 'rolling' | 'hold_duration' | 'sensitivity' | 'optimizer' | 'walk_forward'
   ```
   Anchor: `"export type ResultsTab ="` — replace the whole line.

3. **Tab list** (inside the `map` at ~line 398): extend the `lastRequest` guard array:
   ```typescript
   ...(lastRequest ? ['sensitivity', 'optimizer', 'walk_forward'] : []),
   ```
   Anchor: `"...(lastRequest ? ['sensitivity', 'optimizer'] : []),"` — replace.

4. **Tab label** (inside the `.map` label block at ~line 413):
   ```typescript
   : tab === 'walk_forward' ? 'Walk-Forward'
   ```
   Add before the final `: \`Signal Trace...\`` fallback.
   Anchor: `": tab === 'optimizer' ? 'Optimizer'"` — add the new line immediately after.

5. **Render block** (after the `optimizer` block at ~line 749):
   ```tsx
   {activeTab === 'walk_forward' && lastRequest && (
     <div style={{ padding: '0 16px 16px' }}>
       <WalkForwardPanel lastRequest={lastRequest} />
     </div>
   )}
   ```
   Anchor: `"{activeTab === 'optimizer' && lastRequest && ("` — add the new block immediately after the closing `)}`.

---

### Modified: `frontend/src/shared/types/strategy.ts`

Add after the `StrategyRequest` interface (after line ~102). These types are referenced in `WalkForwardPanel.tsx` via `../../shared/types` but may be declared inline in the panel as `OptimizerPanel` does. **Preferred approach**: declare inline in `WalkForwardPanel.tsx` only (matches `OptimizerPanel` pattern) to avoid touching the shared types file. If the implementer needs them in `index.ts` for other consumers, add them to `strategy.ts` and re-export via `index.ts`; otherwise skip this file entirely.

---

## Algorithm Detail

Pseudocode for `run_walk_forward(req: WalkForwardRequest)`:

```
VALIDATE:
  if req.base.interval in _INTRADAY_INTERVALS:
    raise 400 "Walk-forward v1 supports daily+ intervals only — intraday support requires sub-day window boundaries"
  if len(req.params) == 0: raise 400 "At least one param required"
  if len(req.params) > _MAX_PARAMS: raise 400
  if req.metric not in _VALID_METRICS: raise 400
  if req.is_bars <= 0 or req.oos_bars <= 0: raise 400
  step = req.step_bars if req.step_bars > 0 else req.oos_bars
  total_combos_per_window = product(len(p.values) for p in req.params)
  if total_combos_per_window > _MAX_COMBOS: raise 400

FETCH BARS:
  # Strip regime unconditionally (locked decision 13). Deep copy so we don't mutate the caller.
  base = req.base.model_copy(deep=True, update={"regime": None})
  df = _fetch(base.ticker, base.start, base.end, base.interval, source=base.source)
  total_bars = len(df)
  min_required = req.is_bars + req.oos_bars + req.gap_bars
  if total_bars < min_required: raise 400 "Not enough bars for one window"

COMPUTE WINDOWS:
  windows = []
  oos_end_idx = req.is_bars + req.gap_bars + req.oos_bars - 1
  while oos_end_idx < total_bars:
    if req.expand_train:
      is_start_idx = 0                         # anchored: always from data start
    else:
      is_start_idx = oos_end_idx - req.oos_bars - req.gap_bars - req.is_bars + 1
    is_end_idx   = is_start_idx + req.is_bars - 1
    oos_start_idx = is_end_idx + 1 + req.gap_bars
    oos_end_idx_actual = oos_start_idx + req.oos_bars - 1
    if oos_end_idx_actual >= total_bars: break
    windows.append((is_start_idx, is_end_idx, oos_start_idx, oos_end_idx_actual))
    oos_end_idx += step

  if len(windows) < 2: raise 400 "Need at least 2 windows for walk-forward analysis (WFE undefined below this)"

  low_windows_warn = (len(windows) < 6)  # surfaces in response, doesn't fail

WALK FORWARD LOOP:
  results = []
  window_stitched = []           # list of per-window rescaled curves; flattened in AGGREGATE
  prev_final_equity = base.initial_capital
  total_oos_trades = 0
  low_trades_is_count = 0
  timed_out = False
  start_time = monotonic()

  for w_idx, (is_s, is_e, oos_s, oos_e) in enumerate(windows):
    if monotonic() - start_time > _WFA_TIMEOUT_SECS:
      timed_out = True; break

    is_start_date = df.index[is_s].strftime("%Y-%m-%d")
    is_end_date   = df.index[is_e].strftime("%Y-%m-%d")
    oos_start_date = df.index[oos_s].strftime("%Y-%m-%d")
    oos_end_date   = df.index[oos_e].strftime("%Y-%m-%d")

    # -- IS grid search --
    is_combos = []
    for combo in cartesian_product(req.params):
      if monotonic() - start_time > _WFA_TIMEOUT_SECS:
        timed_out = True; break
      is_req = base.model_copy(deep=True, update={"start": is_start_date, "end": is_end_date})
      for path, value in combo.items():
        is_req = _apply_param(is_req, path, value)
      try:
        r = run_backtest(is_req)
        is_combos.append((combo, r["summary"]))
      except HTTPException as exc:
        if exc.status_code >= 500: raise
        # else: skip this combo (invalid param for this slice)
      except Exception:
        pass  # isolate per-combo errors

    if len(is_combos) == 0:
      # IS grid search produced zero successful backtests — append a degenerate window
      # row instead of dropping it silently, so the user can see the failure.
      results.append(WindowResult(
        window_index=w_idx,
        is_start=is_start_date, is_end=is_end_date,
        oos_start=oos_start_date, oos_end=oos_end_date,
        best_params={},
        is_sharpe=0.0,
        is_metrics={},
        oos_metrics={"num_trades": 0},
        stability_tag="no_is_trades",
        is_combo_count=0,
        scale_factor=1.0,
      ))
      window_stitched.append([])  # no contribution to stitched equity
      continue

    # -- Pick IS winner --
    is_combos.sort(key=lambda x: x[1].get(req.metric, 0), reverse=True)
    best_combo, best_is_summary = is_combos[0]

    # -- Low-trades IS check --
    if best_is_summary.get("num_trades", 0) < req.min_trades_is:
      low_trades_is_count += 1
      stability_tag = "low_trades_is"
    else:
      # -- Neighborhood stability tag --
      # Identify grid-neighbor combos (differ by exactly 1 step in one dimension)
      # Build a lookup: combo_key → IS Sharpe rank quartile (top 25% = True)
      all_sharpes = [s.get("sharpe_ratio", 0) for _, s in is_combos]
      q75 = percentile(all_sharpes, 75)  # 75th percentile is an inline literal, not a constant
      neighbor_keys = get_neighbor_keys(best_combo, req.params)  # combos ±1 step
      neighbor_results = [s for c, s in is_combos if combo_key(c) in neighbor_keys]
      if len(neighbor_results) == 0:
        stability_tag = "spike"  # no neighbors available (edge of grid)
      else:
        top_q_neighbors = sum(1 for s in neighbor_results if s.get("sharpe_ratio", 0) >= q75)
        stability_tag = "stable_plateau" if top_q_neighbors / len(neighbor_results) >= STABILITY_THRESHOLD else "spike"

    # -- OOS evaluation --
    oos_req = base.model_copy(deep=True, update={"start": oos_start_date, "end": oos_end_date})
    for path, value in best_combo.items():
      oos_req = _apply_param(oos_req, path, value)
    try:
      oos_result = run_backtest(oos_req)
    except HTTPException as exc:
      if exc.status_code >= 500: raise
      oos_result = {"summary": {"num_trades": 0}, "equity_curve": []}
    except Exception:
      oos_result = {"summary": {"num_trades": 0}, "equity_curve": []}

    # Only overwrite to "no_oos_trades" if the IS-side check didn't already flag low_trades_is —
    # the low_trades_is signal is more actionable (the optimizer had too little IS signal to trust),
    # while no_oos_trades is often a downstream symptom of that.
    if oos_result["summary"].get("num_trades", 0) == 0 and stability_tag != "low_trades_is":
      stability_tag = "no_oos_trades"

    # -- Equity rescaling --
    raw_curve = oos_result.get("equity_curve", [])
    scale_factor = prev_final_equity / base.initial_capital
    rescaled_curve = [{"time": pt["time"], "value": pt["value"] * scale_factor} for pt in raw_curve]
    if rescaled_curve:
      prev_final_equity = rescaled_curve[-1]["value"]
    # rescaled_curve is appended to the per-window stitching list (see AGGREGATE) but
    # NOT stored on the WindowResult — keeps payload small.
    window_stitched.append(rescaled_curve)

    total_oos_trades += oos_result["summary"].get("num_trades", 0)
    results.append(WindowResult(
      window_index=w_idx,
      is_start=is_start_date, is_end=is_end_date,
      oos_start=oos_start_date, oos_end=oos_end_date,
      best_params=best_combo,
      is_sharpe=round(best_is_summary.get("sharpe_ratio", 0), 3),
      is_metrics=best_is_summary,
      oos_metrics=oos_result["summary"],
      stability_tag=stability_tag,
      is_combo_count=len(is_combos),
      scale_factor=scale_factor,
    ))

AGGREGATE:
  stitched = [pt for curve in window_stitched for pt in curve]
  # Remove duplicate timestamps at window seams (keep the later window's first point)
  stitched = deduplicate_by_time(stitched)

  # WFE = mean(per-window OOS Sharpe) / mean(per-window IS-winner Sharpe).
  # Mean-of-Sharpes form, not Sharpe-of-the-aggregate. Excludes "no_is_trades" windows
  # (their IS Sharpe is 0 by construction and would dilute the denominator).
  contributing = [w for w in results if w.stability_tag != "no_is_trades" and w.oos_metrics.get("num_trades", 0) > 0]
  oos_sharpes = [w.oos_metrics.get("sharpe_ratio", 0) for w in contributing]
  is_sharpes  = [w.is_sharpe for w in contributing]
  if oos_sharpes and is_sharpes and mean(is_sharpes) != 0:
    wfe = round(mean(oos_sharpes) / mean(is_sharpes), 3)
  else:
    wfe = None

  # Param CV per dimension — exclude only no_is_trades windows (they have empty best_params).
  # no_oos_trades and low_trades_is windows DO contribute: the IS-winner params are real,
  # even if the OOS evaluation was weak. Including them measures parameter drift across windows
  # regardless of whether OOS produced trades.
  contributing_for_cv = [w for w in results if w.stability_tag != "no_is_trades"]
  param_cv = {}
  for p in req.params:
    vals = [w.best_params.get(p.path, 0) for w in contributing_for_cv]
    if len(vals) >= 2 and mean(vals) != 0:
      param_cv[p.path] = round(stdev(vals) / mean(vals), 3)
    else:
      param_cv[p.path] = 0.0

  return WalkForwardResponse(
    windows=results,
    stitched_equity=stitched,
    wfe=wfe,
    param_cv=param_cv,
    total_combos=sum(w.is_combo_count for w in results),
    total_oos_trades=total_oos_trades,
    low_trades_is_count=low_trades_is_count,
    low_windows_warn=low_windows_warn,
    timed_out=timed_out,
  )
```

**`get_neighbor_keys` helper:**
For a best_combo `{path: value}` and the param grid, enumerate combos that differ in exactly one dimension by exactly one step (preceding or following value in `p.values`). Return a set of frozenset-style keys for O(1) lookup.

**`combo_key` helper:**
`frozenset(combo.items())` — order-independent dict key.

---

## Response Shape

```typescript
// WalkForwardResponse (TypeScript-style sketch)
{
  windows: Array<{
    window_index: number
    is_start: string           // "YYYY-MM-DD"
    is_end: string
    oos_start: string
    oos_end: string
    best_params: Record<string, number>
    is_sharpe: number
    is_metrics: {
      sharpe_ratio: number
      total_return_pct: number
      win_rate_pct: number
      num_trades: number
      max_drawdown_pct: number
      final_value: number
      // ... full summary dict passthrough
    }
    oos_metrics: { /* same shape */ }
    stability_tag: "stable_plateau" | "spike" | "low_trades_is" | "no_oos_trades" | "no_is_trades"
    is_combo_count: number
    scale_factor: number
  }>
  stitched_equity: Array<{ time: string | number; value: number }>
  wfe: number | null            // null when no OOS trades at all
  param_cv: Record<string, number>  // CV (std/mean) per param across windows
  total_combos: number          // total IS backtests run
  total_oos_trades: number
  low_trades_is_count: number   // windows flagged below min_trades_is
  low_windows_warn: boolean     // true when 2 ≤ windows.length < 6
  timed_out: boolean
}
```

Per-window equity is NOT exposed in the response. The server builds `stitched_equity` from rescaled per-window curves internally and returns only that.

---

## Validation Plan

The implementer must confirm all of the following before marking C28 done.

### Build check
- `npm run build` clean (NOT `tsc --noEmit`).
- `python3 -c "import ast; ast.parse(open('backend/routes/walk_forward.py').read())"` — syntax clean.

### Backend tests — `backend/tests/test_walk_forward.py`

Write these as unit tests using the existing `conftest.py` setup (see `test_backtest_optimizer.py` for the fixture pattern):

1. **`test_insufficient_bars_rejected`** — request `is_bars=1000, oos_bars=1000` on a synthetic 100-bar daily DataFrame; expect HTTP 400 "Not enough bars for one window".
1a. **`test_single_window_rejected`** — request whose math produces exactly 1 window (e.g., `is_bars=80, oos_bars=20` on 100 bars); expect HTTP 400 "Need at least 2 windows for walk-forward analysis".
1b. **`test_intraday_interval_rejected`** — request with `base.interval="5m"`; expect HTTP 400 with the intraday-not-supported message. Same for `"1m"`, `"15m"`, `"1h"`.
3. **`test_single_param_two_windows`** — small synthetic dataset, 1 param, 3 values, rolling windows. Assert `len(response.windows) == 2`, `stitched_equity` is non-empty, time-sorted, no duplicate timestamps at seam.
4. **`test_oos_zero_trades`** — OOS window that contains no signal crossings. Assert `stability_tag == "no_oos_trades"`, `oos_metrics["num_trades"] == 0`, window still appears in `response.windows`.
5. **`test_anchored_vs_rolling_window_counts`** — same request with `expand_train=False` and `expand_train=True`. Assert rolling produces `N` windows, anchored produces `N` windows with `is_start` always equal to `base.start` for every window.
6. **`test_rescaling_math`** — two windows, first OOS ends at `$12,000` (started at `$10,000`). Second window's `scale_factor` must equal `1.2`. Assert `response.stitched_equity` is monotone-connected at the seam (no $10,000 → $12,000 jump back to $10,000 sawtooth). Per-window rescaled curves are not in the response — assert on `stitched_equity` only.
7. **`test_neighborhood_stability_tag`** — manufacture IS results where the top combo's neighbors are all below Q75; assert `stability_tag == "spike"`. Manufacture a different case where ≥ 60 % of neighbors are in Q75; assert `stability_tag == "stable_plateau"`.
8. **`test_max_combos_cap_per_window`** — pass 3 params × 7 values each = 343 combos; expect HTTP 400 before any backtest runs.
9. **`test_timeout_returns_partial`** — mock `time.monotonic` to exceed `_WFA_TIMEOUT_SECS` after the first window; assert `timed_out == True` and `len(windows) >= 1`.
10. **`test_no_is_trades_window_included`** — manufacture a window where every IS combo errors (e.g. param values that the strategy engine rejects); assert the window appears in `response.windows` with `stability_tag == "no_is_trades"`, `best_params == {}`, `scale_factor == 1.0`, and the window does NOT contribute to `wfe` or `param_cv`.

### Frontend tests — `frontend/src/features/strategy/WalkForwardPanel.test.tsx`

Use the same testing setup as `frontend/src/features/trading/BotCard.test.tsx` (vitest + react-testing-library).

1. **`renders form with default values`** — renders without crashing; IS bars, OOS bars inputs exist; Run button is present.
2. **`validates empty is_bars`** — submit with empty IS bars; expect error message shown, no API call made.
3. **`run button disabled when estimatedCombos > 200`** — set 3 params × 10 values each; assert button has `disabled` attribute or `opacity: 0.6`.
4. **`renders result table after successful response`** — mock `api.post` to return a valid `WalkForwardResponse` with 2 windows; assert table renders 2 rows, WFE badge present.

### Manual smoke tests
- Run an obviously over-tuned RSI strategy (e.g. RSI threshold optimized to exactly 28.7 on 2020-2024 data) through WFA on the same date range with 6+ windows. Expect: WFE < 0.5, most windows `stability_tag == "spike"`.
- Run a classic 50/200 MA crossover (broadly robust) with 4+ windows. Expect: WFE > 0.5, at least some windows `"stable_plateau"`.
- Confirm stitched equity curve renders in the Walk-Forward tab without the capital-reset sawtooth (curve should not jump back to `$10,000` between windows).

---

## Known Gotchas

1. **Capital-reset sawtooth.** `run_backtest()` line ~339 hard-resets `capital = req.initial_capital` on every call. Naive OOS equity concatenation produces a sawtooth where each window restarts at the initial capital. The rescaling fix (locked decision 7) corrects this in post-processing: `scale_factor = prev_final_equity / base.initial_capital` is applied to every point in the per-window raw curve before appending to `window_stitched`. `prev_final_equity` is updated from the last rescaled point of each non-empty window. Verify that `equity_curve` values in `run_backtest()` are the actual running equity (dollar amounts starting from `initial_capital`), not returns. (Confirmed at `backtest.py:804` — `equity.append({"time": date, "value": round(total_value, 2)})` where `total_value = capital + position*price`.)

2. **`_compute_regime_series()` HTF lookback is why regime is always stripped in v1.** The HTF lookback for a 200-bar MA on `1d` timeframe is `int(200 * 1.5 * 365/252 + 30) = 465 calendar days`. For an IS window that starts 6 months into the dataset, `_compute_regime_series()` would try to fetch HTF data from 465 days before the IS window start — which may predate yfinance's intraday limit (7 days for `1m`, 60 days for `5m`). This silently clips or raises. Locked decision 13 sidesteps this entirely: the WFA route unconditionally sets `regime = None` on every cloned request. To support regime in a future release, the window-builder must enforce `is_start_idx >= htf_lookback_days * bars_per_day` from the dataset start AND fetch a wider HTF range than the IS slice itself.

3. **Indicator warmup eats IS windows.** RSI 14 needs 14 bars to produce a valid signal; MACD slow=26 needs 26; MA 200 needs 200. A 60-bar IS window with MA 200 will have zero valid signals. The implementer should surface this as a warning count (absorbed under `low_trades_is_count`), not an error. As a sanity check: if `is_bars < 2 * max_indicator_period`, log a warning. The exact `max_indicator_period` is not available from `StrategyRequest` directly — use `is_combos == 0` detection as the proxy (it catches the symptom regardless of cause).

4. **Wall-clock cost.** 10 windows × 50 IS combos × 200 ms per backtest = 100 seconds, already near `_WFA_TIMEOUT_SECS = 120`. Users should be warned in the frontend before submitting: display `estimated_backtests = len(windows) * combos_per_window` and compare to a soft budget. Show a grey label like "~N backtests — may take up to 2 min" beneath the Run button.

5. **`_fetch()` cache.** The in-memory TTL cache in `shared.py` (2 min intraday, 1 hour historical) means repeated per-window calls to the same ticker/interval/start/end will hit cache. However, each IS/OOS window uses different date ranges, so **each window slice is a separate cache entry** and will call yfinance. For 10 windows × 2 (IS + OOS) = 20 uncached fetches. This is acceptable but worth noting: the backend should not try to "optimize" by fetching the full range once and slicing in Python — `run_backtest()` internally calls `_fetch()` with the window-specific start/end, so it will correctly fetch each slice.

6. **`_format_time` consistency.** `_format_time` returns `int` for intraday, `str` for daily+. The rescaling post-process in Python iterates `{"time": pt["time"], "value": ...}` and preserves the original type. The TypeScript type `string | number` covers both. Ensure the frontend's stitched equity chart handles both types (it already does in `Results.tsx`'s equity chart code via the existing `typeof pt.time === 'number'` branch).

7. **Duplicate timestamps at window seams.** If `step_bars == oos_bars` (non-overlapping), the last bar of window N's OOS equals the bar before window N+1's OOS start — no overlap. But if `step_bars < oos_bars` (overlapping step), timestamps will repeat in the stitched curve. The `deduplicate_by_time` step in the algorithm must handle this. For v1 with the non-overlapping default, this is a non-issue but the dedup guard is cheap to add and prevents hard-to-debug chart artifacts later.

---

## Out of Scope (defer per locked decisions)

- Cluster-centroid IS selector (C29)
- CPCV / PBO score (C30)
- Calendar-term window inputs (C31)
- Regime-enabled WFA (mentioned as a known gotcha; explicitly not shipped in v1)
- Intraday-interval WFA (rejected with 400 in v1; requires sub-day window boundaries via ISO datetime strings — queue follow-up C-item once daily-WFA is in real use)

---

## Tier and Review Notes

This is a **Tier C** item per CLAUDE.md:
- New public API contract (`POST /api/backtest/walk_forward`, new Pydantic models)
- New TypeScript types and component
- Aggregate diff will exceed 300 lines
- Contract-surface override applies: new Pydantic `response_model` with a `WindowResult` sub-model

**Morning calibration pass required** (agent-native + reliability) before merging the eventual PR.

**Test-first approach for rescaling and neighborhood-stability tag.** Write `test_rescaling_math` and `test_neighborhood_stability_tag` before implementing those functions. Both are pure functions with no side effects and well-defined inputs/outputs — TDD is directly applicable and the most efficient way to get the edge cases right.

**Do NOT commit or push.** Orchestrator owns the commit.
