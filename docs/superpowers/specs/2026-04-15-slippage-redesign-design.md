# Slippage Model Redesign

- **Date:** 2026-04-15
- **Status:** Draft
- **TODO:** [B7](../../../TODO.md) — this spec
- **Follow-up:** [B8](../../../TODO.md) — spread-derived defaults from broker quote data (out of scope here)

## Problem

The current slippage implementation conflates two distinct jobs behind a single
signed percent field:

1. **Measuring** what actually happened on past fills (diagnostic).
2. **Modeling** what will happen on future fills (backtest assumption).

Tangling them into one number caused three concrete defects:

- **Journal display is sign-wrong for sells and shorts.** `TradeJournal.tsx`
  renders raw `(price - expected) / expected` on every row and in the header
  average without inverting by side, so a favorable sell shows as a positive
  drag and the mixed-sign aggregate is incoherent.
- **Bot runner log sign drift.** `bot_runner.py:461-463` computes long-exit
  slippage as `sell_fill - price`, which is positive when the fill is
  *favorable*. The live `TRADE` log line lies for long sells.
- **Favorable-empirical auto-carry.** `/api/slippage/{symbol}` returns a signed
  pct where negative = favorable, and `StrategyBuilder` auto-populates the
  backtester's Capital & Fees field with that value. A symbol with a handful of
  lucky fills silently teaches the backtester to assume favorable execution
  forever.

The root cause is a convention error, not a bug pile: one number cannot safely
serve both roles.

## Goals

- **Separate measurement from modeling.** Measured slippage is diagnostic;
  modeled slippage is a backtest assumption. Different names, different shapes,
  different rules.
- **Cost is always ≥ 0 everywhere it surfaces.** A fill that happens to be
  favorable is zero cost, not a negative refund.
- **Modeled slippage is floored at a sensible default.** Empirical evidence
  can make the model *worse*, never better — favorable fills are noise or
  survivor bias, not a discount.
- **One shared module owns the convention.** All sign handling, all
  aggregation, all policy logic lives in `backend/slippage.py`. Callers call
  helpers; no site recomputes the formula inline.
- **Fill bias is a separate, signed diagnostic.** Surfaced only in aggregate
  ("you've been getting +0.3 bps on average"), never on individual rows and
  never fed into any cost model.
- **Units are basis points everywhere.** Percent is dead — fields, storage,
  logs, and UI labels all speak bps.

## Non-goals

- **Spread-derived defaults** from broker quote data — tracked as B8.
- **Per-symbol tiering** (large-cap vs small-cap default cost tables) — B8.
- **Backfilling `slippage_bps` onto legacy journal rows** — legacy rows
  compute lazily on read; no migration script runs against the live file.
- **Dual-field API grace period.** `StrategyRequest` accepts only the new
  field name; one user means one clean rename.

## Core convention

One module — `backend/slippage.py` — owns the sign and unit convention. Every
consumer (journal writer, `/api/slippage` endpoint, bot runner, backtester)
calls into it. No site recomputes the formula inline.

Two helpers:

```python
def slippage_cost_bps(side: str, expected: float, fill: float) -> float:
    """Unsigned cost to the trader in basis points. Always >= 0.
    Favorable fills return 0, not a negative number."""

def fill_bias_bps(side: str, expected: float, fill: float) -> float:
    """Signed fill-vs-quote deviation in bps.
    Positive = favorable (you got a better price than expected).
    Negative = unfavorable. Diagnostic only — never fed into cost models."""
```

Side-inversion table, defined once in this module:

| Side     | "Worse when fill is …" | Cost formula (raw, bps)            |
|----------|------------------------|------------------------------------|
| `buy`    | above expected         | `max(0,  (fill - expected) / expected * 1e4)` |
| `cover`  | above expected         | `max(0,  (fill - expected) / expected * 1e4)` |
| `sell`   | below expected         | `max(0,  (expected - fill) / expected * 1e4)` |
| `short`  | below expected         | `max(0,  (expected - fill) / expected * 1e4)` |

`fill_bias_bps` uses the same per-side direction but without the `max(0, ...)`
floor and with the sign flipped (positive = favorable).

Both helpers accept `side` case-insensitively (`.lower()` internally) — call
sites that already have uppercase labels (e.g. `bot_runner` emits `"BUY"`)
pass them through unchanged.

Units: **basis points everywhere** — module API, journal storage field,
`StrategyRequest` field, log lines, and UI labels. `1 bp = 0.01%`. The legacy
percent convention is removed, not aliased.

## Data model changes

### Journal row (hybrid storage)

Raw prices stay the source of truth. A new cached `slippage_bps` field is
written at row-creation time by `journal._log_trade`.

Row shape (relevant fields):

```
{
  "symbol":         "AAPL",
  "side":           "buy" | "sell" | "short" | "cover",
  "qty":            <positive int>,
  "price":          <actual fill price>,
  "expected_price": <decision-time price>,
  "slippage_bps":   <unsigned, cached at write time>,
  ...existing pnl/reason/bot_id/broker fields unchanged...
}
```

- `slippage_bps` is computed via `slippage_cost_bps(side, expected, fill)`.
- `fill_bias_bps` is **not** stored. It is always derived on read from the raw
  prices, so a future convention change can't leave stale signed values on
  disk.
- **Legacy rows** (missing `slippage_bps`): the read path computes it lazily
  via the same helper. No backfill script, no destructive migration.

### `BotState.slippage_pcts → slippage_bps`

Rename the in-memory + persisted field. `bots.json` contains the old key.
Migration runs lazily on `BotManager` deserialize: if a bot dict has
`slippage_pcts` and no `slippage_bps`, copy the values across with `x * 100`
scaling (percent → bps) and drop the old key on next save.

### `StrategyRequest.slippage_pct → slippage_bps`

Rename the API field. Unsigned, default `2.0`, floored at 0 on input (a
negative value is a client bug, not a feature). Old saved strategies in
`localStorage` carry `slippage_pct`; the frontend migrates them on read (see
Backtester section).

## Policy

Three tunable constants at the top of `backend/slippage.py`:

```python
SLIPPAGE_DEFAULT_BPS = 2.0    # fallback when not enough history
SLIPPAGE_MIN_FILLS   = 20     # below this, empirical is noise
SLIPPAGE_WINDOW      = 50     # most-recent fills per symbol
```

One function owns the modeled-value policy:

```python
def decide_modeled_bps(symbol: str) -> ModeledSlippage:
    """Return the modeled cost the backtester should use for `symbol`,
    plus the diagnostic aggregates over the same window.
    Policy: empirical can make the model WORSE, never better than default."""

    fills = _recent_fills(symbol, limit=SLIPPAGE_WINDOW)
    fill_count = len(fills)

    if fill_count == 0:
        return ModeledSlippage(
            modeled_bps=SLIPPAGE_DEFAULT_BPS,
            measured_bps=None,
            fill_bias_bps=None,
            fill_count=0,
            source="default",
        )

    measured = mean(slippage_cost_bps(f.side, f.expected, f.fill) for f in fills)
    bias     = mean(fill_bias_bps(f.side, f.expected, f.fill) for f in fills)

    if fill_count < SLIPPAGE_MIN_FILLS:
        modeled = SLIPPAGE_DEFAULT_BPS
        source  = "default"
    else:
        modeled = max(SLIPPAGE_DEFAULT_BPS, measured)  # floor at default
        source  = "empirical"

    return ModeledSlippage(modeled, measured, bias, fill_count, source)
```

The `max(SLIPPAGE_DEFAULT_BPS, measured)` line is load-bearing: it's the
concrete enforcement of "empirical can make the model worse, never better."
Favorable historical fills always return `SLIPPAGE_DEFAULT_BPS` as `modeled`,
but still surface in `measured_bps` and `fill_bias_bps` for the diagnostic
panel.

The `source` field lets the UI label the value transparently
(`"default"` / `"empirical"`) without reverse-engineering the policy.

## API

### `GET /api/slippage/{symbol}`

Replaces today's `{empirical_pct, fill_count}` shape.

**Response:**

```json
{
  "modeled_bps":    2.0,
  "measured_bps":   1.4,
  "fill_bias_bps":  0.3,
  "fill_count":     47,
  "source":         "empirical"
}
```

| Field            | Type             | Meaning                                                                   |
|------------------|------------------|---------------------------------------------------------------------------|
| `modeled_bps`    | `float` (≥ 0)    | What the backtester should use. Already floored and gated per policy.     |
| `measured_bps`   | `float \| null`  | Unsigned avg cost over the sample window. `null` if `fill_count == 0`.    |
| `fill_bias_bps`  | `float \| null`  | Signed avg deviation over the sample. Positive = favorable. Diagnostic.   |
| `fill_count`     | `int` (≥ 0)      | Size of the sample actually used (capped by `SLIPPAGE_WINDOW`).           |
| `source`         | `"default" \| "empirical"` | Which branch of `decide_modeled_bps` produced `modeled_bps`.  |

**Notes:**

- `modeled_bps` is the *only* field the backtester / StrategyBuilder needs
  to decide the simulation cost. The rest is for the journal diagnostic panel.
- `measured_bps` and `fill_bias_bps` are always derived from raw journal
  prices via the shared helpers — never read from a cached field.
- No `"manual"` source is returned by this endpoint. The `"manual"` label
  is a frontend-only state, set when the user edits the field in
  `StrategyBuilder`.

## Backtester

### `StrategyRequest` field

Rename `slippage_pct` → `slippage_bps`.

```python
class StrategyRequest(BaseModel):
    ...
    slippage_bps: float = Field(default=2.0, ge=0.0)   # unsigned, bps
```

Pydantic's `ge=0.0` rejects negative values at the validation boundary — the
API contract makes "cost is always ≥ 0" unrepresentable otherwise.

### Drag application in `routes/backtest.py`

Same four-direction logic as today, fed an unsigned bps number instead of a
signed percent.

```python
drag = req.slippage_bps / 10_000   # bps → fractional

# Entry
if is_short:
    fill_price = price * (1 - drag)     # short entry: worse = lower
else:
    fill_price = price * (1 + drag)     # long entry:  worse = higher

# Exit
if is_short:
    exit_price = raw_exit * (1 + drag)  # cover: worse = higher
else:
    exit_price = raw_exit * (1 - drag)  # sell:  worse = lower
```

Dollar-amount slippage fields on each trade (`entry_slippage`,
`exit_slippage`) stay positive via the existing `abs(...)` wrappers.

### Saved-strategy migration (frontend)

Old strategies in `localStorage` carry `slippage_pct` (sometimes negative).
The `StrategyBuilder` loader converts on read:

```ts
const slippage_bps =
  saved.slippage_bps
  ?? Math.max(0, (saved.slippage_pct ?? 0) * 100)
```

`Math.max(0, ...)` retroactively applies the new "cost ≥ 0" rule to any
favorable-empirical values that were stored under the old convention. After
first re-save, the `slippage_pct` key is gone.

### No dual-field grace period

`StrategyRequest` accepts only `slippage_bps`. The frontend is the only
client; migrating it is a one-file change. Any stale external client would
422 on the missing field (acceptable — there are no other clients).

## Bot runner

Inline slippage math is deleted at `bot_runner.py:295-302` (entry fill) and
`bot_runner.py:459-466` (exit fill). Both sites call the shared helpers:

```python
from slippage import slippage_cost_bps, fill_bias_bps

cost_bps = slippage_cost_bps(side_label, expected=price, fill=fill_price)
bias_bps = fill_bias_bps(side_label, expected=price, fill=fill_price)

state.slippage_bps.append(round(cost_bps, 2))
self._log(
    "TRADE",
    f"{side_label} {qty} {cfg.symbol} @ {fill_price:.2f} "
    f"(expected={price:.2f}, cost={cost_bps:.1f}bps, bias={bias_bps:+.1f}bps)",
)
```

The log line carries **two** numbers:

- `cost=` — unsigned drag actually paid, for comparison against the modeled
  value.
- `bias=` — signed fill-vs-quote deviation, for the trader's in-the-moment
  read ("did I get a favorable fill this time?").

The call to `_log_trade(...)` is unchanged at the call site — it still passes
`expected_price=price`. Caching of the unsigned `slippage_bps` on the journal
row happens inside `journal._log_trade` via the same shared helper, so the
bot runner and the write-path agree by construction.

This also retires the **long-exit sign drift** at `bot_runner.py:461-463`
(where `sell_fill - price` was positive on favorable fills). With a shared
helper, that whole class of error disappears.

`state.slippage_pcts` is renamed to `state.slippage_bps`. Values are unsigned
cost in bps — `BotCard` sparklines / metrics consume this field (see
Frontend section). The `bots.json` deserializer migrates old keys lazily
(see Data Model section).

## Frontend

### Hook: `useEmpiricalSlippage` → `useSlippage`

Returns the new endpoint shape. Path and query-key updated; everything else
matches existing TanStack patterns.

```ts
export interface SlippageInfo {
  modeled_bps: number
  measured_bps: number | null
  fill_bias_bps: number | null
  fill_count: number
  source: 'default' | 'empirical'
}
export function useSlippage(symbol: string): UseQueryResult<SlippageInfo>
```

### `StrategyBuilder.tsx` — Capital & Fees block

- State: `slippage: number | ''` → `slippageBps: number`. Default `2.0`.
- On symbol change or first load, set `slippageBps = data.modeled_bps`.
- `slippageSource: 'empirical' | 'default' | 'manual'` — mirrors `data.source`
  until the user edits, then flips to `'manual'` and stays there.
- Input is a plain number (`step=0.5`, suffix label `bps`).
- Inline hint text next to the input:
  - `empirical · N fills` (tooltip: "floored at 2 bps default")
  - `default · no history yet`
  - `manual`
- Submit payload: `{ slippage_bps: slippageBps }`. No `slippage_pct` ever sent.
- On strategy load, run the migration formula from the Backtester section.

### `TradeJournal.tsx`

- **Row "Slip" column** shows unsigned cost in bps. Source:
  `t.slippage_bps` if present, else compute client-side via a mirror of
  `slippage_cost_bps` from the raw `expected_price` + `price`. Color: neutral
  gray at 0, red-tinted as magnitude grows. No green, no signed display.
- **Header diagnostic block** (new, above the table):
  > **Fills** — 42 trades · avg cost **1.4 bps** · fill bias **+0.3 bps**
  > _(slight favorable)_
- Delete `slippageColor(t)` and the signed/per-row sign logic.
- Summary `avgSlippage` → `avgCostBps` (unsigned, from row `slippage_bps`).
- `fill_bias_bps` shown only in aggregate in the header block, never on a row.
- CSV export column renamed `slippage_bps`, always unsigned.

### `BotCard.tsx`

Any sparkline / metric tied to `slippage_pcts` switches to `slippage_bps`.
Display labels change from `%` to `bps`. No structural changes — this is a
field and label swap.

### Client-side helper for legacy rows

A small TS utility mirrors `slippage_cost_bps` so legacy journal rows
(without a cached `slippage_bps`) still display correctly. Logic is a
one-to-one port of the Python helper; kept alongside the row renderer in
`TradeJournal.tsx`. Not exported — row rendering is the only reader.

## Testing

### New: `backend/tests/test_slippage.py`

**Helper sign convention — all four sides:**

| Test                  | Input                                       | Expected        |
|-----------------------|---------------------------------------------|-----------------|
| `buy` worse-above     | expected=100, fill=100.10                   | cost=10 bps     |
| `buy` favorable       | expected=100, fill=99.90                    | cost=0 bps      |
| `cover` worse-above   | expected=100, fill=100.10                   | cost=10 bps     |
| `cover` favorable     | expected=100, fill=99.90                    | cost=0 bps      |
| `sell` worse-below    | expected=100, fill=99.90                    | cost=10 bps     |
| `sell` favorable      | expected=100, fill=100.10                   | cost=0 bps      |
| `short` worse-below   | expected=100, fill=99.90                    | cost=10 bps     |
| `short` favorable     | expected=100, fill=100.10                   | cost=0 bps      |

**Fill bias symmetry:**

- `fill_bias_bps("buy", 100, 99.90)` → `+10` (favorable).
- `fill_bias_bps("sell", 100, 100.10)` → `+10` (favorable).
- Sign of `fill_bias_bps` is the opposite of `slippage_cost_bps` for
  unfavorable fills, and positive where cost is 0 for favorable fills.

**Policy (`decide_modeled_bps`):**

- Empty journal → `modeled=2.0`, `source="default"`, `measured=None`.
- `fill_count < 20` → `modeled=2.0`, `source="default"`, `measured` still
  populated from the sample.
- `fill_count >= 20` with `measured=0.5` → `modeled=2.0` (floored),
  `source="empirical"`.
- `fill_count >= 20` with `measured=3.0` → `modeled=3.0`,
  `source="empirical"`.
- Window cap: journal with 100 fills → only the most-recent 50 influence
  the aggregates.

**Hybrid caching:**

- A row written by `_log_trade` has a cached `slippage_bps` matching the
  helper result.
- A legacy row (no `slippage_bps`) returns the same value when read via
  the derivation path.

### Updated fixtures

- `backend/tests/test_slippage_endpoint.py` — new response shape (all five
  fields).
- `backend/tests/test_backtest_short.py` — `slippage_pct` → `slippage_bps`
  in fixtures; assert short-cover drag sign (`cover` worse-when-higher
  still makes the cost subtract from short PnL).

### Manual verification (post-implementation)

1. Load a saved strategy from `localStorage` that previously had a negative
   `slippage_pct` value. Confirm it migrates to `0` and the UI labels it
   `default · no history yet` (or `empirical · N fills` with a floored
   value, if journal history exists).
2. Run a backtest for a symbol known to have historical favorable fills.
   Confirm modeled cost used is the `2.0` default, never a favorable value.
3. Fire one live bot trade. Confirm:
   - `TRADE` log line contains both `cost=` and `bias=`.
   - New journal row has `slippage_bps` cached on disk.
   - `GET /api/slippage/{sym}` reflects the new fill.
4. Open `TradeJournal`. Confirm:
   - Row "Slip" column shows unsigned bps for every side.
   - Header diagnostic block shows both avg cost and signed fill bias.
   - No green per-row slippage colors remain.

## Rollout

One PR, one deploy — single user, no staging. Steps ordered so the backend
is coherent at every commit boundary:

1. **`backend/slippage.py` + tests.** Two helpers, policy function, constants,
   `test_slippage.py`. Nothing else calls into it yet.
2. **Journal write-time caching + endpoint rewrite.** `journal._log_trade`
   caches `slippage_bps` on new rows; `/api/slippage/{symbol}` moves to the
   new response shape. Update `test_slippage_endpoint.py`.
3. **Backtester wiring.** Rename `StrategyRequest.slippage_pct →
   slippage_bps` with `ge=0.0` validation. Update drag application in
   `routes/backtest.py`. Update `test_backtest_short.py` fixtures.
4. **Bot runner.** Delete inline math at `bot_runner.py:295-302` and
   `:459-466`; call shared helpers; rename `state.slippage_pcts →
   slippage_bps`; add lazy `bots.json` deserialize migration.
5. **Frontend.** Rename `useEmpiricalSlippage → useSlippage`, rewire
   `StrategyBuilder` (state rename + migration formula + source label),
   rewrite `TradeJournal` (one-column row + header diagnostic block), swap
   `BotCard` field/label.
6. **Manual verification pass** per the four-step checklist above.

Commits should match step boundaries so a `git bisect` on any future
regression lands on the right layer.

## Open questions

None. All decisions resolved in the brainstorming session (Q1–Q10).
