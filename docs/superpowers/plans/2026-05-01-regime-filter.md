# Regime-Gated Direction-Switching — Full Implementation Plan

## Context

StrategyLab bots have a static `direction` flag — each bot is long or short, forever. When the market regime flips, a directional bot bleeds until someone manually intervenes. On Alpaca (primary broker), positions are netted — you cannot hold simultaneous long and short. A single bot must be able to switch directions.

**Goal:** A single bot that trades both long and short on the same symbol, with a higher-timeframe regime signal deciding which direction is active. When the regime flips, the bot closes the current position and can enter the opposite direction.

**A13 is in this plan.** Multi-timeframe indicator overlay is essential for strategy design — you can't pick regime parameters if you can't see the daily indicator on your intraday chart.

**Relationship to existing safeguards:** Drawdown auto-pause (D21, shipped) is reactive — pauses AFTER loss. The regime filter is proactive — prevents entries before the bleed starts. They're complementary.

## Design Decisions

### Three-state regime, not binary
The regime produces three states, not two:
- **Long mode** — regime condition is True (e.g., price above 200 SMA)
- **Short mode** — regime condition is False AND dual rule sets are provided
- **Flat mode** — regime condition is False AND no dual rule sets (only single direction rules)

This is the critical design choice. A user who enables regime with a single long strategy gets "trade long when bullish, sit flat when bearish" — NOT "go short when bearish." Short mode only activates when the user has explicitly designed and provided short entry/exit rules. The presence of dual rule sets is the signal that the user has validated a short strategy.

### On-flip behavior: Configurable, default close_only
`on_flip: "close_and_reverse" | "close_only" | "hold"`
- **close_only** (default): Close current position on flip, go flat. Wait for entry rules in new direction to fire. Safe default — the user is never forced into a position they didn't design.
- **close_and_reverse**: Close current position, immediately enter opposite direction. Only valid when dual rule sets are provided. If dual rules are None, downgraded to close_only. Maximizes time-in-market but user must have validated both directions.
- **hold**: Position plays out under its own sell rules. Regime only gates new entries. Legacy V1 behavior.

**Backtest behavior on flip bar:**
- `hold`: do nothing to position, flip regime, block entries in old direction
- `close_only`: close at bar's close (with slippage), go flat, no entry this bar. Entry rules for new direction evaluate starting next bar.
- `close_and_reverse`: close at bar's close + enter opposite at same bar's close. Two fills, two slippage events. Only if dual rules exist.

**Bot runner behavior for close_and_reverse:**
1. Close current position (market order)
2. Poll for fill (up to 2.5s, existing `_get_fill_price_provider`)
3. Verify position cleared (up to 3s, existing pattern)
4. If cleared AND in market hours: submit entry in opposite direction on SAME tick
5. If not cleared: log error, set `state.pending_regime_flip = True`. Next tick retries: if `pending_regime_flip` is True AND position still exists, retry close. If position cleared, attempt reverse entry.
6. If reverse entry fails (buying power, API error): bot is safely flat. Next tick re-evaluates normally.

**Known divergence: live vs backtest slippage on regime flips.** The backtest models close_and_reverse as two fills at the same bar's close. Live execution has a 5-10 second gap between close and reverse entry. During volatile regime-flip bars (which are directional by definition), the reverse entry will chase a moved market. This is a structural cost that backtests underestimate. Users should expect ~2x modeled slippage on regime flip bars in live trading. Documented, not hidden.

### Dual rule sets: Optional, gates direction switching
- When `regime.enabled` AND `long_buy_rules` + `short_buy_rules` are provided → full direction switching
- When `regime.enabled` AND only `buy_rules`/`sell_rules` → sit-flat gate (regime gates on/off using single rule set + direction)
- When `regime: None` → current behavior, zero change

Empty short rule panels (None) mean "go flat in short regime." They do NOT fall back to buy_rules. This is explicit: the user must provide both rule sets to get direction switching.

### Per-trade direction
Each trade record gets `direction: "long" | "short"` set from `position_direction` at entry time, not from global `req.direction`.

### Consecutive-bar smoothing, default 3
`min_bars: int = 3`. "Set and forget" demands stability.

### NaN / warmup: Gate closed (False = flat)
NaN regime → no entries. Conservative-by-default.

### Lookahead bias contract (non-negotiable)
`align_htf_to_ltf` uses **T-1 HTF close for T's intraday bars**. Both sides normalized to UTC via `.tz_convert('UTC')` (NOT `.tz_localize` — yfinance daily index is already tz-aware in America/New_York). Strict `<`.

### Same-symbol guard: Bidirectional exclusion
When ANY bot on a symbol has `regime.enabled`, no other bot can run on that symbol (regardless of direction). This is symmetric: a non-regime bot already running blocks a new regime bot, and vice versa. A regime bot's direction is dynamic — it owns the symbol exclusively.

### Stop-loss companion requirement
Regime doesn't protect current position from drawdown. UI warns when regime enabled without stop-loss.

### Inter-stage safety: Bot runner rejects unsupported regime config
Until Stage 5 ships, the bot runner checks `cfg.regime.enabled` and refuses to start with error "Regime filter not yet supported for live bots. Use backtest to validate." Prevents silent failure where a user creates a regime bot from a saved strategy and it ignores the regime config.

## Schema Definitions

### Python — RegimeConfig + StrategyRequest changes (backend/models.py)

```python
class RegimeConfig(BaseModel):
    enabled: bool = False
    timeframe: str = "1d"
    indicator: str = "ma"
    indicator_params: dict = {"period": 200, "type": "sma"}
    condition: str = "above"        # above/below/rising/falling/crosses_above/crosses_below
    value: Optional[float] = None
    param: Optional[str] = "close"
    min_bars: int = 3
    on_flip: str = "close_only"     # close_only/close_and_reverse/hold

class StrategyRequest(BaseModel):
    # ... all existing fields unchanged ...
    direction: str = "long"         # backward compat default

    # Dual rule sets (Optional — None = use buy_rules/sell_rules, no direction switching)
    long_buy_rules: Optional[list[Rule]] = None
    long_sell_rules: Optional[list[Rule]] = None
    long_buy_logic: str = "AND"
    long_sell_logic: str = "AND"
    short_buy_rules: Optional[list[Rule]] = None
    short_sell_rules: Optional[list[Rule]] = None
    short_buy_logic: str = "AND"
    short_sell_logic: str = "AND"

    regime: Optional[RegimeConfig] = None
```

BotConfig in `backend/bot_manager.py` mirrors these same fields.

### BotState additions (backend/bot_manager.py)

```python
regime_active: bool = True              # True = long regime, False = short/flat regime
regime_consec: int = 0
regime_last_htf_time: Optional[str] = None
regime_direction: Optional[str] = None  # "long" | "short" | "flat"
position_direction: Optional[str] = None  # direction of current open position
pending_regime_flip: bool = False       # True = close failed, retry next tick
```

### TypeScript types (frontend/src/shared/types/index.ts)

```typescript
interface RegimeConfig {
  enabled: boolean
  timeframe: string
  indicator: string
  indicator_params: Record<string, any>
  condition: string
  value?: number
  param?: string
  min_bars: number
  on_flip: 'close_and_reverse' | 'close_only' | 'hold'
}
// Add regime?: RegimeConfig to StrategyRequest, SavedStrategy, BotConfig
// Add dual rule fields (all Optional) to same
// Trade.direction is per-trade
```

## Backtest Loop Refactor (backend/routes/backtest.py)

### Stage 3 refactor: `is_short` → `position_direction`
This refactor happens in Stage 3 (sit-flat gate), NOT Stage 4. Reason: Stage 3 already touches backtest.py. Doing the variable refactor in Stage 3 means Stage 4 builds on clean primitives rather than re-editing Stage 3's additions.

- Remove `is_short = req.direction == "short"` (line 179)
- Add `position_direction: Optional[str] = None` (None when flat)
- For single-direction strategies without regime: `position_direction = req.direction` at entry time. All existing behavior unchanged.
- Helper `is_pos_short() → position_direction == "short"`
- Replace ALL ~20 `is_short` references with `is_pos_short()` or `position_direction`
- **Also replace `req.direction` in trade records** (lines 283, 381) with `position_direction`. These are NOT `is_short` hits and will be missed by a simple search-and-replace.

### Regime pre-computation (before main loop)
1. Fetch HTF data via `fetch_higher_tf()` (separate from LTF fetch, independent lookback)
2. Compute regime indicator via `compute_instance()` from routes/indicators.py
3. Evaluate regime condition → raw boolean series
4. Apply min_bars smoothing
5. Align to LTF index via `align_htf_to_ltf()` → `regime_bool` Series
6. NaN → False (gate closed during warmup)
7. Derive `regime_direction` per bar:
   - `regime_bool[i] == True` → "long"
   - `regime_bool[i] == False` AND dual rules provided → "short"
   - `regime_bool[i] == False` AND no dual rules → "flat"
8. When regime is None → regime_direction = req.direction for all bars (current behavior)

### Main loop flow with regime
```
for each bar i:
    current_dir = regime_direction[i]  # "long" | "short" | "flat"

    # 1. Detect regime flip while positioned
    if position > 0 and position_direction != current_dir:
        if on_flip == "hold": pass
        elif on_flip == "close_only":
            close position at bar close
            position_direction = None
        elif on_flip == "close_and_reverse" and current_dir != "flat":
            close position + enter in current_dir
        elif on_flip == "close_and_reverse" and current_dir == "flat":
            close position only (can't enter flat)

    # 2. Entry (when flat)
    if position == 0 and current_dir != "flat":
        select rules based on current_dir:
            "long" → long_buy_rules or buy_rules
            "short" → short_buy_rules
        evaluate entry rules → enter with position_direction = current_dir

    # 3. Exit (when positioned)
    elif position > 0:
        select exit rules based on position_direction:
            "long" → long_sell_rules or sell_rules
            "short" → short_sell_rules or sell_rules
        evaluate stops + sell rules using position_direction for direction logic
```

### Backtest response: add `regime_active` field
The backtest response must include the `regime_direction` series (per-bar "long"/"short"/"flat") for chart shading. Add to the response dict alongside equity curve, trades, etc.

## Bot Runner Changes (backend/bot_runner.py)

### Regime evaluation in _tick()
After bar fetch + indicator computation, before entry/exit logic:
1. Fetch HTF data (separate call, lookback via `htf_lookback_days()`)
2. Compute regime indicator via `compute_instance()`
3. Evaluate on last completed HTF bar (second-to-last in series = strict T-1)
4. Track consecutive bars in BotState; flip only when >= min_bars
5. Derive regime_direction:
   - True + dual rules → "long"
   - False + dual rules → "short"
   - True + single rules → req.direction (current behavior)
   - False + single rules → "flat"
6. Update `state.position_direction` on all entry/exit operations

### `is_short` refactor in bot_runner
Same pattern as backtest: replace all ~30 `cfg.direction` / `is_short` references with `state.position_direction`. Critical ordering: when close_and_reverse fires mid-tick, update `state.position_direction` to the new direction BEFORE entering the entry code path, not after. The entry path's slippage/side/type logic reads `position_direction` and must see the new value.

### Pending regime flip retry
If close fails (position not cleared after 3s):
- Set `state.pending_regime_flip = True`
- Next tick: if `pending_regime_flip` AND position still exists, retry close
- If `pending_regime_flip` AND position cleared (filled between ticks), proceed to reverse entry if applicable
- Clear `pending_regime_flip` on successful close or if regime direction reverts to match position

### Same-symbol guard (bot_manager.py)
```python
# Updated guard in start_bot():
for bid, task in self.tasks.items():
    if bid != bot_id and not task.done():
        other_cfg, _ = self.bots[bid]
        if other_cfg.symbol == config.symbol:
            # If EITHER bot has regime enabled, block regardless of direction
            if config.regime and config.regime.enabled:
                raise ValueError(f"Regime bot on {config.symbol} requires exclusive symbol access")
            if other_cfg.regime and other_cfg.regime.enabled:
                raise ValueError(f"Bot {bid} has regime enabled on {config.symbol}")
            # Otherwise, existing guard: same symbol + same direction blocked
            if other_cfg.direction == config.direction:
                raise ValueError(f"Bot {bid} already running {config.direction} on {config.symbol}")
```

## Journal PnL for Bidirectional Bots (backend/journal.py)

### New helper: `compute_bidirectional_pnl`
Add a new function alongside existing `compute_realized_pnl`:
```python
def compute_bidirectional_pnl(symbol: str, bot_id: str, since: Optional[str] = None) -> float:
    """Sum realized PnL across all directions for a bidirectional bot."""
    long_pnl = compute_realized_pnl(symbol, "long", bot_id, since)
    short_pnl = compute_realized_pnl(symbol, "short", bot_id, since)
    return long_pnl + short_pnl
```
No existing callers change. Regime bot callers use this new function. Existing single-direction bots continue using `compute_realized_pnl` with explicit direction.

Call sites in bot_runner that need updating for regime bots:
- Line 252: PnL for activity log
- Line 336: position sizing (capital compounding)
- Line 612: equity snapshot
- bot_manager.py lines 333, 394, 405: bot list summary

Also: `first_bot_entry_time` (journal.py line 111) filters by direction. Needs a bidirectional variant for regime bots.

## Multi-TF Data Foundation (backend/shared.py)

### fetch_higher_tf(ticker, start, end, htf_interval, source)
Wraps `_fetch()`. Caller computes lookback via `htf_lookback_days()`.

### htf_lookback_days(indicator, params) → int
Calendar days needed: `period * 1.5 * 365/252 + 30` buffer. E.g., 200 SMA daily → ~460 calendar days.

### align_htf_to_ltf(htf_series, ltf_index) → pd.Series
- Normalize both indexes to UTC via `.tz_convert('UTC')` (NOT `.tz_localize` — yfinance daily index is already tz-aware in America/New_York, Alpaca is UTC)
- `pd.merge_asof` with direction='backward'. For each LTF bar, find most recent HTF bar strictly before it.
- Returns Series indexed on ltf_index. Bars before first HTF bar get NaN.
- **Critical timezone behavior**: yfinance daily bar for 2024-01-15 has timestamp `2024-01-15 00:00:00-05:00` = `2024-01-15 05:00:00 UTC`. Next day's first intraday bar at 9:30 ET = `2024-01-16 14:30:00 UTC`. merge_asof backward correctly picks the Jan 15 daily bar for Jan 16's intraday bars. Monday's intraday bars at `2024-01-15 14:30 UTC` correctly pick Friday Jan 12's daily bar. No manual T-1 shift needed — the timestamp ordering handles it naturally when both sides are UTC.

## A13: Multi-TF Indicator Overlay

### HTF indicator endpoint (backend/routes/indicators.py)
- Extend `IndicatorsPostRequest` with `htf_interval: str | None = None`
- When set, fetch + compute at HTF resolution via `fetch_higher_tf()`
- Return time-value pairs at HTF resolution

### Indicator TF selector (frontend/src/features/sidebar/IndicatorList.tsx)
- Per-instance dropdown: "Same" / "1D" / "1W"
- `IndicatorInstance` type gets `htfInterval?: string`

### HTF data fetching (frontend/src/shared/hooks/useOHLCV.ts)
- Group instances by timeframe; HTF instances get separate API call
- Multiple same-TF instances share one POST

### Stepped overlay (frontend/src/features/chart/Chart.tsx)
- Use `LineType.WithSteps` (confirmed in MacroEquityChart.tsx)
- Stepped value for day D appears starting at day D+1's first intraday bar (visual consistency with anti-lookahead)

### Chart regime shading (Chart.tsx)
- Implementation: histogram series on invisible price scale (`priceScaleId: 'regime-bg'`, `visible: false`, `scaleMargins: { top: 0, bottom: 0 }`). Full-height bars with very low opacity.
- Green tint (#26a64120) when regime = long, red tint (#f8514920) when regime = short, no bar when flat.
- This approach is distinct from volume bars (which use `priceScaleId: 'volume'` with `top: 0.75`) — no conflict.

## Incremental Delivery Schedule

### Stage 1: A13a — Data Foundation `[next]`
**Ships:** `fetch_higher_tf()`, `align_htf_to_ltf()`, `htf_lookback_days()`, alignment tests.
**Files:** `backend/shared.py`, `backend/tests/test_htf_alignment.py`
**Tests:** Lookahead, weekend gaps, first-bar edge, timezone normalization (Yahoo ET vs Alpaca UTC), weekly HTF.

### Stage 2: A13b — Multi-TF Indicator Overlay
**Ships:** Daily/weekly indicators on intraday charts.
**Files:** `backend/routes/indicators.py`, `frontend/src/features/sidebar/IndicatorList.tsx`, `frontend/src/shared/types/indicators.ts`, `frontend/src/shared/hooks/useOHLCV.ts`, `frontend/src/features/chart/Chart.tsx`
**Prereq:** A13a. Can parallel with Stage 3.

### Stage 3: B21 — Regime Sit-Flat Gate + `is_short` Refactor
**Ships:** RegimeConfig model, `is_short` → `position_direction` refactor in backtest, regime gate (single rule set, trade vs sit flat), regime chart shading, regime UI in StrategyBuilder, regime in saved strategies, `regime_active` in backtest response, regime evaluation tests. Bot runner: reject `regime.enabled=True` with clear error message.
**Files:** `backend/models.py`, `backend/routes/backtest.py`, `frontend/src/features/strategy/StrategyBuilder.tsx`, `frontend/src/features/chart/Chart.tsx`, `frontend/src/shared/types/index.ts`, `frontend/src/features/strategy/savedStrategies.ts`, `backend/tests/test_regime.py`, `backend/bot_runner.py` (guard only), `backend/routes/bots.py` (guard only)
**Prereq:** A13a. Can parallel with Stage 2.
**Note:** The `is_short` → `position_direction` refactor is done HERE, not Stage 4. For single-direction strategies, `position_direction = req.direction` at entry time — zero behavioral change. Stage 4 activates per-bar switching on clean primitives.

### Stage 4a: B21v2a — Symmetric Direction Switching (Backtest)
**Ships:** `on_flip` behavior in backtest (close_only, close_and_reverse, hold), per-bar `position_direction` switching driven by regime, per-trade direction in trade records. Uses SAME rules for both directions (no dual rule sets yet). UI: add `on_flip` dropdown to regime section, hide direction toggle when regime has on_flip != hold.
**Files:** `backend/routes/backtest.py`, `frontend/src/features/strategy/StrategyBuilder.tsx` (small change: on_flip dropdown + direction toggle gating), `frontend/src/shared/types/index.ts`, `backend/tests/test_backtest_regime_switch.py`
**Tests:** PnL sign correctness: short trades profit when price falls, long trades profit when price rises, verified against known price sequences. Regime flip bar: verify two slippage events on close_and_reverse. hold mode: verify position persists through flip.
**Prereq:** B21.

### Stage 4b: B21v2b — Dual Rule Sets
**Ships:** `long_buy_rules` / `long_sell_rules` / `short_buy_rules` / `short_sell_rules` in schema and backtest. Three-state regime (long/short/flat based on dual rule presence). Long/short tab split in StrategyBuilder UI.
**Files:** `backend/models.py`, `backend/routes/backtest.py`, `frontend/src/features/strategy/StrategyBuilder.tsx` (tab split), `frontend/src/shared/types/index.ts`
**Prereq:** B21v2a.

### Stage 5: D24 — Regime Live Bot
**Ships:** Regime evaluation in bot_runner, `is_short` → `position_direction` refactor in bot_runner, position flip sequence, `pending_regime_flip` retry logic, BotState regime/direction fields, `compute_bidirectional_pnl` in journal, updated call sites in bot_runner + bot_manager, same-symbol guard update, regime status on bot card, AddBotBar regime passthrough, remove bot runner regime guard (replace with actual implementation).
**Files:** `backend/bot_runner.py`, `backend/bot_manager.py`, `backend/journal.py`, `backend/routes/bots.py`, `frontend/src/features/trading/BotCard.tsx`, `frontend/src/features/trading/AddBotBar.tsx`
**Prereq:** B21v2b.

## Dependency Graph

```
Stage 1: A13a (data foundation)
    ↓
    ├── Stage 2: A13b (multi-TF overlay) — parallel track
    │
    └── Stage 3: B21 (sit-flat gate + is_short refactor)
            ↓
        Stage 4a: B21v2a (symmetric direction switching)
            ↓
        Stage 4b: B21v2b (dual rule sets)
            ↓
        Stage 5: D24 (live bot)
```

Stages 2 and 3 run in parallel (no file overlap). Stages 3 → 4a → 4b → 5 are sequential.

## Risk Assessment

### CRITICAL
1. **Lookahead bias in align_htf_to_ltf** — Backtest results inform real capital allocation. T-1 contract, UTC normalization via `.tz_convert('UTC')`, exhaustive tests.
2. **Position flip failure at broker** — Close fills but reverse fails. Safely flat is acceptable. `pending_regime_flip` ensures retry. Next tick re-evaluates.
3. **Alpaca netting collision** — Bidirectional same-symbol guard prevents other bots from conflicting with regime bot.
4. **Live vs backtest slippage divergence on regime flips** — Documented as structural ~2x slippage cost on flip bars. Users warned, not hidden.

### HIGH
5. **`is_short` → `position_direction` substitution completeness** — ~20 sites in backtest.py, ~30 in bot_runner.py. Includes `req.direction` in trade records (lines 283, 381) which are NOT `is_short` hits. Tests must verify PnL sign against known price sequences, not just smoke tests.
6. **Sell rules not firing after regime flip (hold mode)** — `hold` is NOT default. Users who choose it explicitly accept the risk. Stop-loss warning in UI.
7. **Bot HTF lookback** — `htf_lookback_days()` computes from indicator params. Log warning if insufficient.

### MEDIUM
8. **Timestamp format** — Yahoo (America/New_York) vs Alpaca (UTC). `.tz_convert('UTC')` on both sides.
9. **Frontend state** — Dual rule sets add ~8 state vars. Group into objects.
10. **Saved strategy backward compat** — All Optional with defaults.

## Backward Compatibility

- `regime: None` → gate always True → zero behavioral change
- `long_buy_rules: None` → falls back to `buy_rules`/`sell_rules` + `direction`
- `short_buy_rules: None` + regime enabled → flat when regime condition is False (NOT direction switching)
- Existing bots.json → Pydantic defaults all new fields to None
- Existing localStorage strategies → optional fields, loadSavedStrategy handles missing
- `/api/backtest` → regime is optional, omitting = current behavior
- `compute_realized_pnl` → unchanged. New `compute_bidirectional_pnl` for regime bots only
- Trade direction field → same value for non-regime strategies
- Bot runner rejects `regime.enabled=True` until Stage 5 ships → no silent failures

## Verification

1. `npm run build` — TypeScript errors
2. Backtest without regime → identical results to current behavior (regression)
3. Backtest with sit-flat regime → fewer trades, no trades during flat periods, regime shading visible
4. Backtest with close_and_reverse → trades in both directions, two slippage events per flip, PnL sign correct per direction
5. Backtest with close_only → goes flat on flip, re-enters when entry rules fire
6. PnL correctness: known price sequence test → short trades profit when price falls, long trades profit when price rises
7. Position flip: close → fill → reverse entry completes within one tick
8. Same-symbol guard: regime bot blocks other bots, other bots block regime bot
9. Lookahead: manual check of 3-5 regime transition dates vs known daily closes
10. Inter-stage: bot runner rejects regime.enabled before Stage 5
