# EV/trade + Profit Factor with Decomposition Waterfall

**Date:** 2026-04-12
**Scope:** Backtest summary tab only — no bot card changes.

## Problem

The current strategy summary tab shows avg/min/max gain and loss plus a P&L distribution histogram, but hides the one number that determines whether a strategy is worth running: **expected value per trade**. Readers stare at "avg gain $378 / avg loss $441" and conclude "losses bigger than wins = losing strategy" — even when the win *frequency* overcomes the larger average loss and the strategy is genuinely profitable.

Example that triggered this spec: BABA 15m short backtest with win rate 56.51%, avg gain $378, avg loss $441.
```
0.5651 × $378 + 0.4349 × -$441 = +$21.82 per trade  →  positive EV across 791 trades
```
A user scanning the summary misses this entirely today.

## Goal

Make EV/trade and profit factor the headline numbers of the P&L distribution block, and add a visual decomposition waterfall so readers immediately see *how* wins and losses net out to the EV figure.

## Architecture

Single-session feature. Backend computes four new summary fields alongside the existing `gain_stats` / `loss_stats`; frontend renders a two-number header row and a 3-row waterfall above the existing StatRows + histogram inside the P&L Distribution block.

- **No new routes, no new models.** Existing `StrategyResponse.summary` gains four fields.
- **No bot card changes.** Live bot cards stay as-is; a follow-up can extend them once the backtest version proves its value.
- **EV/PF are always mean-based**, regardless of the existing mean/median toggle. A small `(mean)` suffix makes this explicit. The toggle continues to affect the existing StatRow labels only.

## Backend changes

### File: `backend/routes/backtest.py`

The existing block around line 273–278 already computes `gains`, `losses`, `gain_stats`, `loss_stats`, and `pnl_distribution`. Extend it with:

```python
gross_profit = round(sum(gains), 2)
gross_loss = round(abs(sum(losses)), 2)
num_sells = len(sell_trades)
ev_per_trade = round((gross_profit - gross_loss) / num_sells, 2) if num_sells > 0 else None
profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else None
```

Wire the four new fields into the `summary` dict returned from the route.

### File: `backend/models.py` (or wherever `StrategyResponse` lives)

Add to the summary model:
```python
gross_profit: float
gross_loss: float
ev_per_trade: float | None
profit_factor: float | None
```

### Rationale for `None` on `profit_factor`

Python can't serialize `float('inf')` to JSON. When `gross_loss == 0`, return `None` and let the frontend render `∞` (green) when `gross_profit > 0` or `—` when there are zero trades overall.

## Frontend changes

### File: `frontend/src/shared/types/index.ts`

Extend the summary type with the four new fields, same names and types as the backend.

### File: `frontend/src/features/strategy/Results.tsx`

Restructure the existing P&L Distribution block (currently lines 156–184). Today's structure:

```jsx
<div padding 12 16 borderTop>
  <div flex row gap 24>
    <div minWidth 180>       {/* header + 6 StatRows */}
    <PnlHistogram />
  </div>
</div>
```

New structure:

```jsx
<div padding 12 16 borderTop>
  <div header row>                {/* "P&L DISTRIBUTION" label + mean/median toggle */}
  <div ev/pf row>                 {/* "EV: +$21.82 / trade (mean)   PF: 1.11 (mean)" */}
  <EvWaterfall ... />             {/* full-width 3-row bar visualization */}
  <div flex row gap 24>           {/* existing layout, unchanged */}
    <div minWidth 180>            {/* 6 StatRows (unchanged) */}
    <PnlHistogram />              {/* unchanged */}
  </div>
</div>
```

### EV/PF header row

Two big numbers side by side, matching the `metricsGrid` tile typography (16px bold). Both get a small grey `(mean)` suffix.

- **EV** — color `#26a641` if `> 0`, `#f85149` if `≤ 0`, `#8b949e` if null. Format: `+$21.82 / trade`.
- **PF** — color `#26a641` if `> 1`, `#f85149` if `≤ 1`, render as `∞` (green) if `null && gross_profit > 0`, `—` (grey) if `null && gross_profit == 0`. Format: `1.11`.

### EvWaterfall component

Inline in `Results.tsx` (no separate file unless it exceeds ~50 lines). 3 rows in a CSS grid with four columns: label / bar / computation / value.

```
Wins    [████████████████]  56.5% × $378.21 = +$213.36
Losses  [███████████]       43.5% × $441.12 = -$191.83
─────────────────────────────────────────────────────
Net     [██]                                 +$21.53
```

- **Label column** — fixed width ~55px, uppercase small grey (`Wins`, `Losses`, `Net`).
- **Bar column** — flex, height 14px, rounded corners. Green (`#26a641`) for Wins, red (`#f85149`) for Losses, colored by sign for Net. Width = `abs(contribution) / max_contribution * 100%` where `max_contribution = max(win_contribution, loss_contribution)`.
- **Computation column** — monospace, grey, 11px. Format `{winRate}% × ${avgGain} = +${contribution}`. Omitted on the Net row.
- **Value column** — right-aligned monospace, 12px, colored by sign.
- **Separator** — 1px `#30363d` border above the Net row.

**Derived values (frontend, from existing + new summary fields):**
- `winRate = summary.win_rate_pct`
- `lossRate = 100 - winRate`
- `avgGain = summary.gain_stats.mean` (fallback 0)
- `avgLoss = abs(summary.loss_stats.mean)` (fallback 0)
- `winContribution = gross_profit / num_sells`   (= winRate/100 × avgGain)
- `lossContribution = gross_loss / num_sells`    (= lossRate/100 × avgLoss)
- `netContribution = ev_per_trade`

Using `gross_profit / num_sells` is preferred over `winRate × avgGain` to avoid rounding drift between frontend and backend.

### Edge cases

| Case | EV | PF | Waterfall |
|---|---|---|---|
| `num_trades == 0` | — | — | Block hidden by existing guard, no change |
| All wins, no losses | `+$N` green | `∞` green | Omit Losses row; Net bar = Wins bar width |
| All losses, no wins | `-$N` red | `0.00` red | Omit Wins row; Net bar = Losses bar width, red |
| Mixed with break-even trades | Correct (diluted by break-evens via `num_sells`) | Mean-based, unaffected | Normal 3-row layout |

Break-even trades are excluded from `gains` and `losses` (the existing filters are `> 0` and `< 0`) but remain in `num_sells`, so they correctly dilute EV. This matches the real-world interpretation — a break-even trade is still a trade taken, and its "zero contribution" is part of the strategy's actual per-trade expectation.

## Testing

### Backend (TDD, before code)

Extend `backend/tests/test_backtest_stats.py` with four cases. The existing `_side_stats` test pattern can be copied.

1. **`test_ev_pf_mixed`** — 10 wins averaging $100, 5 losses averaging -$50. Expected `gross_profit=1000`, `gross_loss=250`, `ev_per_trade=50.0`, `profit_factor=4.0`.
2. **`test_ev_pf_all_wins`** — 5 wins, no losses. Expected `gross_loss=0`, `profit_factor=None`, `ev_per_trade > 0`.
3. **`test_ev_pf_all_losses`** — 5 losses, no wins. Expected `gross_profit=0`, `profit_factor=0.0`, `ev_per_trade < 0`.
4. **`test_ev_pf_with_breakeven`** — 2 wins of $100, 2 losses of -$100, 1 break-even. Expected `ev_per_trade=0.0` (0 total P&L / 5 trades), `profit_factor=1.0`.

Extract the new computation into a small helper (`_edge_stats(gains, losses, num_sells) -> dict`) to make it unit-testable without running a full backtest.

### Frontend

Manual browser check after implementation:
- **Golden path** — run a known mixed-result backtest, verify EV and PF numbers match hand calculation from the existing stats, waterfall bars are proportional, colors correct.
- **All wins** — find a trivially short backtest window on a trending symbol where every trade wins; verify PF shows `∞`, Losses row is omitted.
- **All losses** — inverted conditions; verify PF shows `0.00`, Wins row is omitted.
- **Short strategy** — run a short-direction backtest; verify the feature works identically (all sign conventions go through the same `pnl` field, which is already direction-adjusted).

No unit/Jest tests — frontend has no test harness today and this is a pure display component.

## Out of scope

- **Bot card EV/PF display.** Tracked as a follow-up in TODO.md.
- **Waterfall on the Trades tab** or anywhere else in the UI.
- **Alternative EV definitions** (geometric, risk-adjusted, Kelly-sized). Standard arithmetic EV only.

## Open questions

None — the four clarifying questions from `memory/idea_expected_value_viz.md` have all been answered in the design above.
