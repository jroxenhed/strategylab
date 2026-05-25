# Trailing Stop Loss — Design Spec

**Date:** 2026-04-06

## Overview

Add a trailing stop loss to the backtester, as an optional addition to the existing fixed stop loss. Both can be active simultaneously — the fixed stop acts as a hard floor, the trailing stop follows price upward.

---

## Backend

### New model: `TrailingStopConfig` (in `backtest.py`)

```python
class TrailingStopConfig(BaseModel):
    type: str = "pct"               # "pct" | "atr"
    value: float = 5.0              # % below peak (pct), or ATR multiplier (atr)
    source: str = "high"            # "high" | "close" — which price updates the peak
    activate_on_profit: bool = False # only start trailing once price exceeds entry
```

### Changes to `StrategyRequest`

Add one optional field:
```python
trailing_stop: Optional[TrailingStopConfig] = None
```

The existing `stop_loss_pct` field is unchanged.

### ATR computation (`signal_engine.py`)

Add ATR (14-period) to `compute_indicators()`. The function signature gains optional `high` and `low` parameters:

```python
def compute_indicators(close, high=None, low=None) -> dict[str, pd.Series]:
```

ATR formula: rolling mean of True Range over 14 bars.  
True Range = max(high − low, |high − prev_close|, |low − prev_close|)

If `high`/`low` are not provided, ATR is omitted from the returned dict (backwards-compatible).

### Backtest loop changes (`backtest.py`)

Pass `df["High"]` and `df["Low"]` into `compute_indicators()`.

Per open trade, track two new variables:
- `trail_peak` — highest price seen since entry (or since profit threshold crossed if `activate_on_profit`)
- `trail_stop_price` — computed each bar from `trail_peak`

**On BUY:** initialise `trail_peak = fill_price`, `trail_stop_price = None`.

**Each bar while in position:**
1. Get bar source price: `high.iloc[i]` if `source == "high"`, else `close.iloc[i]`
2. If `activate_on_profit`: only update `trail_peak` when source price > `entry_price`; otherwise update unconditionally
3. `trail_peak = max(trail_peak, source_price)`
4. Compute `trail_stop_price`:
   - pct: `trail_peak * (1 - value / 100)`
   - atr: `trail_peak - value * atr.iloc[i]`
5. `trail_stop_hit = low.iloc[i] <= trail_stop_price`

**Exit priority:** fixed stop or trailing stop — whichever fires first. If both fire on the same bar, fixed stop price is used (it's the lower, harder floor).

**Trade record:** add `trailing_stop: bool` field to sell entries (alongside existing `stop_loss: bool`).

**Signal trace:** extend `STOP_LOSS` action to `STOP_LOSS` (fixed) vs `TRAIL_STOP` (trailing).

---

## Frontend

### `types/index.ts`

Add interface:
```ts
export interface TrailingStopConfig {
  type: 'pct' | 'atr'
  value: number
  source: 'high' | 'close'
  activate_on_profit: boolean
}
```

Add to `BacktestRequest`:
```ts
trailing_stop?: TrailingStopConfig
```

### `StrategyBuilder.tsx`

- Add `trailingEnabled` boolean state (default `false`)
- Add `trailingConfig` state (default: `{ type: 'pct', value: 5, source: 'high', activate_on_profit: false }`)
- Both persisted to `localStorage`

UI layout below the existing Stop Loss row:
```
[✓] Trailing Stop
    Type:     [% ▾]   Value: [5.0    ]
    Source:   [High ▾]
    [✓] Activate on profit only
```

Sub-fields only visible when `trailingEnabled` is true. Send `trailing_stop` in the backtest request only when enabled.

### `Results.tsx`

Exit reason column: show `"TSL"` for trailing stop exits (currently `"SL"` for fixed stop, `"Signal"` for sell rule).

### `Chart.tsx`

`buildMarkers()`: trailing stop exits get label `"TSL"` instead of `"SL"`. Color remains red (loss) / green (win) per existing logic.

---

## What is NOT in scope

- Trailing stop in live paper trading (Alpaca integration) — backtester only for now
- Configurable ATR period (hardcoded 14)
- Cooldown after trailing stop exit (separate future feature)
