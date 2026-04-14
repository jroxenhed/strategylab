# Realistic Cost Model — Design Spec

**Date:** 2026-04-14
**Status:** Design approved, ready for implementation plan

## Goal

Replace the current flat-percentage commission model and assumption-based slippage with (1) IBKR Fixed per-share commission, (2) empirical per-symbol slippage drawn from the live trade journal, and (3) short-borrow cost. Surface total cost drag prominently in the backtest summary so strategies whose edge cannot clear their own cost hurdle become visible at a glance.

## Context

User runs StrategyLab with ~$10,000 total algo-trading capital. At this scale, IBKR's $0.35 per-order minimum binds for almost every trade (positions of 20–200 shares at typical US equity prices). A single round-trip costs ~$0.70; a 100-trade/year strategy incurs 0.7% annual drag; a 500-trade/year strategy approaches 3.5%. The current flat-% model understates this dramatically.

Design goal is **realism + IBKR parity + differentiation** — match IBKR's actual billing for simple single-position cases, make the cost drag visible per-trade and in aggregate. Explicitly not "pessimism for pessimism's sake."

## Out of Scope (v1)

These were considered and deferred to TODO.md:

- **Debit-balance-aware margin interest.** Real IBKR charges margin interest only when net cash is negative. v1 uses borrow-only for shorts; margin interest on `position_value × hold_days` is wrong at this scale because short proceeds sit as collateral.
- **IBKR Tiered pricing** (exchange fees, SEC fee, FINRA TAF, clearing fee). v1 uses IBKR Fixed.
- **Dynamic borrow rate feed** (hard-to-borrow names). v1 uses a single editable rate.
- **FX conversion cost.** 0.002% × $10k = $0.20, YAGNI.

## Components

### 1. Commission — IBKR Fixed per-share

Replaces the existing `commission_pct` flat-% model.

Formula, applied on both entry AND exit:

```
commission_per_leg = max(shares × per_share_rate, min_per_order)
```

Defaults: `per_share_rate = $0.0035`, `min_per_order = $0.35`. Both editable in Settings → Capital & Fees.

**Already correct in existing code:** `routes/backtest.py` deducts commission on both entry (lines 162, 165) and exit (240, 245). No bug, no "verify and fix" — just swap the formula.

### 2. Slippage — empirical per-symbol with fallback

Unchanged in semantic (still `slippage_pct` applied directionally to fill prices) but the **default value** is now drawn from live trade data.

**Aggregation scope:** per-symbol, across all bots. Raw mean of `slippage_pct` values in `trade_journal.json` filtered by symbol. Includes favorable fills (negative values) — intellectually honest, not clamped to zero.

**New endpoint:** `GET /api/slippage/{symbol}` → `{empirical_pct: float|null, fill_count: int}`. Frontend calls this on symbol change.

**UI behavior:** slippage input auto-populates with empirical mean. Source badge renders inline:

- `empirical: 47 fills (+0.018%)` — standard case
- `empirical: 47 fills (−0.012%) ⚠ favorable` — when mean is negative, to flag that backtest is modeling optimistic fills
- `default: 0.01%` — no journal data for this symbol (fallback)
- `manual` — user has edited the field

**Fallback** when no journal data exists: `0.01%` (≈ half-spread on liquid large-caps).

### 3. Short borrow cost

Applied only when `direction == "short"`. No toggle — set rate to 0 to disable.

Formula, applied at trade exit:

```
hold_days = (exit_ts - entry_ts).total_seconds() / 86400   # fractional, supports intraday
borrow_cost = position_value × (borrow_rate_annual / 100 / 365) × hold_days
```

Default rate: `0.5%/year` (typical easy-to-borrow rate for liquid US equities). Editable.

UI: Short Costs section hidden entirely when `direction == "long"`.

## Backend Changes

### `backend/models.py` — `StrategyRequest`

Add fields:

```python
per_share_rate: float = 0.0035
min_per_order: float = 0.35
borrow_rate_annual: float = 0.5   # % per year
```

**Keep `commission_pct`** in the model but ignore it at runtime. Avoids breaking any in-flight request payloads. Field becomes dead after frontend migration.

`slippage_pct` unchanged — semantics identical, only the default source changes (now populated from journal by frontend).

### New helper in `routes/backtest.py`

```python
def per_leg_commission(shares, req):
    return max(shares * req.per_share_rate, req.min_per_order)

def borrow_cost(shares, entry_price, entry_ts, exit_ts, direction, req):
    if direction != "short" or req.borrow_rate_annual <= 0:
        return 0.0
    hold_days = (exit_ts - entry_ts).total_seconds() / 86400
    position_value = shares * entry_price
    return position_value * (req.borrow_rate_annual / 100 / 365) * hold_days
```

**Per-trade-leg field layout** (keeps existing schema, minimal diff):

- Entry leg (`buy` / `short`): writes `commission` and `slippage` fields as today. Formula for `commission` changes from `shares × price × commission_pct` to `per_leg_commission(shares, req)`.
- Exit leg (`sell` / `cover`): writes `commission` and `slippage` as today. Also writes a new `borrow_cost` field (0.0 for longs).

Integration: entry commission deducted from `capital` at entry time. Exit commission + borrow cost deducted from trade PnL at exit time (`pnl = gross_pnl - exit_commission - borrow_cost`). No new `entry_commission` / `exit_commission` fields — the existing per-leg `commission` field already carries each leg's value and the Trades tab already sums `buy.commission + sell.commission` into its "Comm" column. Only a new `Borrow` column (and the `borrow_cost` type field) is added.

### New route `routes/backtest.py` (or new file): `GET /api/slippage/{symbol}`

Scans `backend/data/trade_journal.json`, filters rows by `symbol`, returns:

```json
{"empirical_pct": 0.018, "fill_count": 47}
```

Or `{"empirical_pct": null, "fill_count": 0}` when no data. O(n) over journal, journal is tiny — no caching needed for v1.

## Frontend Changes

### `shared/types/index.ts`

- Add `per_share_rate: number`, `min_per_order: number`, `borrow_rate_annual: number` to `StrategyRequest`
- Add `borrow_cost: number` to `Trade` (present on exit legs, 0.0 for longs)
- Keep existing `commission` and `slippage` trade fields exactly as-is — the Trades tab's existing `buy.commission + sell.commission` sum logic continues to work unchanged; only the backend formula that populates `commission` changes

### `shared/hooks/` — new `useEmpiricalSlippage(symbol)`

TanStack Query hook calling `GET /api/slippage/{symbol}`. Returns `{empirical_pct, fill_count, source}` where source is one of `empirical | default | manual`. Refetches on symbol change.

### `features/sidebar/Sidebar.tsx` — Capital & Fees panel

```
Capital & Fees
  Capital ($)         [10000]
  % of Capital        [100]

  Slippage (%)        [0.018]   empirical: 47 fills
  Rate per share ($)  [0.0035]
  Min per order ($)   [0.35]

  Short Costs   (rendered only when direction === "short")
    Borrow rate (%/yr)  [0.5]
```

**Slippage input behavior:**

- Auto-populates from `useEmpiricalSlippage` on symbol change
- Editing the field marks it `manual`
- Clearing the field reverts to empirical/default
- Badge text reflects source, with `⚠ favorable` suffix when empirical mean < 0

### Strategy load migration (App.tsx or wherever saved strategies load)

When a persisted strategy object lacks `per_share_rate`, inject defaults (`0.0035`, `0.35`, `0.5`) on load. Fire a one-time toast:

> "Commission model updated — now using IBKR per-share ($0.0035/share, $0.35 min). Adjust in Settings if needed."

Suppression flag stored in `localStorage` under `commission_migration_notified`.

### `features/strategy/Results.tsx`

**Trades tab:** existing `Slip` and `Comm` columns stay as-is (values change because backend formula changed, column logic unchanged). Add one new column `Borrow` showing `sell.borrow_cost` for shorts and `—` for longs.

**Summary tab:** new "Cost Breakdown" block positioned near the existing P&L metrics:

```
Cost Breakdown
  Total commission:   $12.60
  Total borrow cost:  $0.48   (shorts only)
  Total slippage:     $4.23
  Total all-in costs: $17.31
  Cost drag:          0.17%   (of starting capital)
```

Borrow line hidden entirely for long-only strategies.

## Testing

**Unit tests (`backend/tests/test_backtest_costs.py`, new):**

- `calculate_trade_costs` on a long trade: borrow = 0, both commissions = max formula
- `calculate_trade_costs` on a short with 5-day hold: borrow = `position_value × 0.5/100/365 × 5`
- Min-commission binding: 10 shares × $0.0035 = $0.035 → clamps to $0.35
- Fractional `hold_days`: 30-minute intraday short produces correct fractional borrow

**Integration test:**

- Small-capital strategy (e.g. 100 trades, $10k capital, AAPL) — verify Summary tab cost drag line matches sum of per-trade costs
- Verify trade log per-row breakdown sums to aggregate total

**Regression:**

- `test_backtest_short.py` updated for per-share commissions (expected PnL numbers shift)

**Slippage endpoint:**

- Returns null/0 when no journal rows match symbol
- Returns raw mean (not abs) when fills are favorable — test with a synthetic journal containing negative slippage entries

## Backward Compatibility

- Old saved strategies in `localStorage` without new fields → defaults injected on load + one-time toast
- Old `StrategyRequest` payloads with `commission_pct` set → ignored at runtime; no crash, commission calculated from per-share formula using defaults
- Existing journal entries remain valid — `slippage_pct` field already present on all fills

## Deferred to TODO.md

- Debit-balance-aware margin interest (more accurate short-cost model when leveraged)
- IBKR Tiered pricing with exchange/regulatory fees
- Hard-to-borrow dynamic rate feed
- FX conversion cost
