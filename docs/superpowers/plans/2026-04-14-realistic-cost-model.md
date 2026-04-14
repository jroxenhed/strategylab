# Realistic Cost Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flat-% commission with IBKR Fixed per-share pricing, empirical per-symbol slippage defaults from the live journal, and short-borrow cost — and surface total cost drag in the backtest summary.

**Architecture:** Backend gains three `StrategyRequest` fields (`per_share_rate`, `min_per_order`, `borrow_rate_annual`) and two pure helpers (`per_leg_commission`, `borrow_cost`) wired into `routes/backtest.py`. A new `GET /api/slippage/{symbol}` endpoint aggregates `trade_journal.json`. Frontend updates the Capital & Fees panel (lives in `StrategyBuilder.tsx`, not `Sidebar.tsx`), adds a TanStack Query hook for empirical slippage, and extends `Results.tsx` with a Borrow column + Cost Breakdown block.

**Tech Stack:** FastAPI + Pydantic (backend), React + TypeScript + TanStack Query (frontend), pytest.

**Spec:** `docs/superpowers/specs/2026-04-14-realistic-cost-model-design.md`

---

## File Map

**Modify:**
- `backend/models.py` — add 3 fields to `StrategyRequest`
- `backend/routes/backtest.py` — add helpers, replace commission formula, add borrow cost
- `backend/tests/test_backtest_short.py` — update expected PnL for new commission model
- `frontend/src/shared/types/index.ts` — add fields to `StrategyRequest`, `Trade`, `SavedStrategy`
- `frontend/src/features/strategy/StrategyBuilder.tsx` — replace Commission % input with per-share/min fields + Short Costs section + empirical slippage wiring + migration toast
- `frontend/src/features/strategy/Results.tsx` — add Borrow column to Trades tab, Cost Breakdown block to Summary tab

**Create:**
- `backend/routes/slippage.py` — new router for `GET /api/slippage/{symbol}`
- `backend/tests/test_backtest_costs.py` — unit tests for new helpers
- `backend/tests/test_slippage_endpoint.py` — endpoint tests
- `frontend/src/shared/hooks/useEmpiricalSlippage.ts` — TanStack Query hook

---

## Task 1: Add cost-model fields to `StrategyRequest`

**Files:**
- Modify: `backend/models.py:29-56`

- [ ] **Step 1: Add the three new fields**

In `backend/models.py`, inside `class StrategyRequest`, directly after the existing `commission_pct: float = 0.0` line, add:

```python
    per_share_rate: float = 0.0035   # IBKR Fixed per-share commission
    min_per_order: float = 0.35      # IBKR Fixed minimum per order
    borrow_rate_annual: float = 0.5  # % per year, only applied when direction == "short"
```

Leave `commission_pct` in place — it stays in the schema for inbound-payload compatibility and is simply unused at runtime after Task 3.

- [ ] **Step 2: Sanity-check the model loads**

Run: `cd backend && python -c "from models import StrategyRequest; r = StrategyRequest(ticker='X', buy_rules=[], sell_rules=[]); print(r.per_share_rate, r.min_per_order, r.borrow_rate_annual)"`
Expected: `0.0035 0.35 0.5`

- [ ] **Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat: add per-share commission + borrow-rate fields to StrategyRequest"
```

---

## Task 2: Add pure cost helpers with unit tests (TDD)

**Files:**
- Create: `backend/tests/test_backtest_costs.py`
- Modify: `backend/routes/backtest.py` — insert helpers below imports, above `_side_stats`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_backtest_costs.py`:

```python
"""Unit tests for per-leg commission and borrow-cost helpers."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

from datetime import datetime, timedelta
from models import StrategyRequest
from routes.backtest import per_leg_commission, borrow_cost
from signal_engine import Rule


def _req(**kw) -> StrategyRequest:
    defaults = dict(
        ticker="X", buy_rules=[Rule(indicator="price", condition="below", value=1)],
        sell_rules=[Rule(indicator="price", condition="above", value=1)],
    )
    return StrategyRequest(**{**defaults, **kw})


def test_commission_uses_per_share_when_above_min():
    # 200 shares * 0.0035 = 0.70 > 0.35 min
    assert per_leg_commission(200, _req()) == 0.70


def test_commission_clamps_to_min_when_below():
    # 10 shares * 0.0035 = 0.035 → clamp to 0.35
    assert per_leg_commission(10, _req()) == 0.35


def test_commission_exact_boundary():
    # 100 shares * 0.0035 = 0.35 → exactly min
    assert per_leg_commission(100, _req()) == 0.35


def test_borrow_zero_for_long():
    entry = datetime(2024, 1, 1)
    exit_ = datetime(2024, 1, 6)
    assert borrow_cost(100, 50.0, entry, exit_, "long", _req()) == 0.0


def test_borrow_zero_when_rate_is_zero():
    entry = datetime(2024, 1, 1)
    exit_ = datetime(2024, 1, 6)
    assert borrow_cost(100, 50.0, entry, exit_, "short", _req(borrow_rate_annual=0.0)) == 0.0


def test_borrow_short_5_day_hold():
    # position_value = 100 * 50 = 5000
    # daily_rate = 0.5 / 100 / 365
    # cost = 5000 * (0.005/365) * 5
    entry = datetime(2024, 1, 1)
    exit_ = datetime(2024, 1, 6)
    expected = 5000 * (0.5 / 100 / 365) * 5
    assert abs(borrow_cost(100, 50.0, entry, exit_, "short", _req()) - expected) < 1e-9


def test_borrow_fractional_intraday_hold():
    # 30 minutes = 0.5/24 days = 1/48 days
    entry = datetime(2024, 1, 1, 10, 0, 0)
    exit_ = datetime(2024, 1, 1, 10, 30, 0)
    expected = (100 * 50.0) * (0.5 / 100 / 365) * (30 * 60 / 86400)
    assert abs(borrow_cost(100, 50.0, entry, exit_, "short", _req()) - expected) < 1e-9
```

- [ ] **Step 2: Run tests — expect failure (helpers don't exist)**

Run: `cd backend && pytest tests/test_backtest_costs.py -v`
Expected: `ImportError` / `ImportError: cannot import name 'per_leg_commission'`

- [ ] **Step 3: Implement the helpers**

In `backend/routes/backtest.py`, directly after `from models import ...` (line 10) and before `router = APIRouter()`, add:

```python
def per_leg_commission(shares: float, req) -> float:
    """IBKR Fixed per-share commission with min-per-order floor."""
    return max(shares * req.per_share_rate, req.min_per_order)


def borrow_cost(shares: float, entry_price: float, entry_ts, exit_ts,
                direction: str, req) -> float:
    """Short-borrow cost for the holding period. Zero for longs or rate=0."""
    if direction != "short" or req.borrow_rate_annual <= 0:
        return 0.0
    hold_days = (exit_ts - entry_ts).total_seconds() / 86400
    position_value = shares * entry_price
    return position_value * (req.borrow_rate_annual / 100 / 365) * hold_days
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd backend && pytest tests/test_backtest_costs.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/backtest.py backend/tests/test_backtest_costs.py
git commit -m "feat: add per_leg_commission + borrow_cost helpers"
```

---

## Task 3: Wire helpers into the backtest loop

This replaces the four flat-% commission calculations in `routes/backtest.py` with `per_leg_commission`, and deducts `borrow_cost` from short-trade PnL on exit. Entry timestamp is captured per-position so the exit can compute hold-days.

**Files:**
- Modify: `backend/routes/backtest.py` — entry block (~lines 150–182), exit block (~lines 237–261), add `entry_ts` tracking

- [ ] **Step 1: Add `entry_ts` to per-position state**

Near the top of `run_backtest` where `entry_price` is initialized (around line 78), add an `entry_ts` variable initialized to `None`:

```python
        capital = req.initial_capital
        position = 0.0
        entry_price = 0.0
        entry_ts = None
        trail_peak = 0.0
```

- [ ] **Step 2: Capture `entry_ts` on entry and switch to per-share commission**

Inside the `if position == 0 and hour_ok and eval_rules(...)` block (around lines 150–176), replace the commission calculation and add entry timestamp capture.

Find:

```python
                shares = (capital * effective_size) / fill_price
                commission = shares * fill_price * req.commission_pct / 100
                position = shares
                entry_price = fill_price
                capital -= shares * fill_price + commission
```

Replace with:

```python
                shares = (capital * effective_size) / fill_price
                commission = per_leg_commission(shares, req)
                position = shares
                entry_price = fill_price
                entry_ts = df.index[i]
                capital -= shares * fill_price + commission
```

- [ ] **Step 3: Replace exit commission and add borrow cost**

Inside the `if stop_hit or trail_hit or sell_fired:` block (around lines 237–261), replace both commission computations and wire in borrow cost.

Find:

```python
                    exit_slippage = abs(position * (raw_exit - exit_price))
                    if is_short:
                        commission = position * exit_price * req.commission_pct / 100
                        pnl = position * (entry_price - exit_price) - commission
                        capital += position * entry_price + pnl
                    else:
                        proceeds = position * exit_price
                        commission = proceeds * req.commission_pct / 100
                        pnl = (proceeds - commission) - position * entry_price
                        capital += proceeds - commission
                    exit_type = "cover" if is_short else "sell"
                    trades.append({
                        "type": exit_type,
                        "date": date,
                        "price": round(exit_price, 4),
                        "shares": round(position, 4),
                        "direction": req.direction,
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl / (position * entry_price) * 100, 2),
                        "stop_loss": exit_reason == "stop_loss",
                        "trailing_stop": exit_reason == "trailing_stop",
                        "slippage": round(exit_slippage, 2),
                        "commission": round(commission, 2),
                    })
```

Replace with:

```python
                    exit_slippage = abs(position * (raw_exit - exit_price))
                    commission = per_leg_commission(position, req)
                    bcost = borrow_cost(position, entry_price, entry_ts, df.index[i],
                                        req.direction, req)
                    if is_short:
                        pnl = position * (entry_price - exit_price) - commission - bcost
                        capital += position * entry_price + pnl
                    else:
                        proceeds = position * exit_price
                        pnl = (proceeds - commission) - position * entry_price
                        capital += proceeds - commission
                    exit_type = "cover" if is_short else "sell"
                    trades.append({
                        "type": exit_type,
                        "date": date,
                        "price": round(exit_price, 4),
                        "shares": round(position, 4),
                        "direction": req.direction,
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl / (position * entry_price) * 100, 2),
                        "stop_loss": exit_reason == "stop_loss",
                        "trailing_stop": exit_reason == "trailing_stop",
                        "slippage": round(exit_slippage, 2),
                        "commission": round(commission, 2),
                        "borrow_cost": round(bcost, 2),
                    })
```

- [ ] **Step 4: Reset `entry_ts` on exit**

Find the block that resets position state after a trade closes (around lines 262–264):

```python
                    position = 0.0
                    trail_peak = 0.0
                    trail_stop_price = None
```

Replace with:

```python
                    position = 0.0
                    entry_ts = None
                    trail_peak = 0.0
                    trail_stop_price = None
```

- [ ] **Step 5: Run backtest cost tests + short regression to confirm basic integration**

Run: `cd backend && pytest tests/test_backtest_costs.py tests/test_backtest_short.py -v`
Expected: `test_backtest_costs.py` passes (7). `test_backtest_short.py` may have failures — that's expected and addressed in Task 4.

- [ ] **Step 6: Commit**

```bash
git add backend/routes/backtest.py
git commit -m "feat: wire per-share commission + borrow cost into backtest loop"
```

---

## Task 4: Update short regression test for new cost defaults

`test_backtest_short.py` uses default `StrategyRequest` — which now has `per_share_rate=0.0035`, `min_per_order=0.35`, `borrow_rate_annual=0.5`. The existing assertions (`pnl > 0`, `pnl < 0`, `stop_loss == True`) still hold directionally, but any equality-shaped assertion would break. Verify by running.

**Files:**
- Modify: `backend/tests/test_backtest_short.py` — only if any test fails

- [ ] **Step 1: Run the short tests**

Run: `cd backend && pytest tests/test_backtest_short.py -v`

- [ ] **Step 2: If any test fails, inspect the failure and patch**

All current assertions in `test_backtest_short.py` are directional (`> 0`, `< 0`, flag checks, `in` checks). They should still pass with the new cost model because commissions are small relative to the 10x price moves used in tests. If a test fails:

- Add `per_share_rate=0.0, min_per_order=0.0, borrow_rate_annual=0.0` to `_req_short` defaults and the long regression `StrategyRequest(...)` in `test_long_still_works` to preserve original zero-cost semantics. Example:

```python
def _req_short(**kwargs) -> StrategyRequest:
    defaults = dict(
        ticker="TEST", direction="short",
        buy_rules=[Rule(indicator="price", condition="below", value=999)],
        sell_rules=[Rule(indicator="price", condition="below", value=91)],
        initial_capital=10000.0, position_size=1.0,
        per_share_rate=0.0, min_per_order=0.0, borrow_rate_annual=0.0,
    )
    return StrategyRequest(**{**defaults, **kwargs})
```

And update `test_long_still_works`:

```python
    req = StrategyRequest(
        ticker="TEST", direction="long",
        buy_rules=[Rule(indicator="price", condition="below", value=999)],
        sell_rules=[Rule(indicator="price", condition="above", value=109)],
        initial_capital=10000.0, position_size=1.0,
        per_share_rate=0.0, min_per_order=0.0, borrow_rate_annual=0.0,
    )
```

This keeps the test's intent — verify direction semantics without cost noise.

- [ ] **Step 3: Re-run**

Run: `cd backend && pytest tests/test_backtest_short.py -v`
Expected: all pass.

- [ ] **Step 4: Commit (if changes were needed)**

```bash
git add backend/tests/test_backtest_short.py
git commit -m "test: pin short regression to zero-cost defaults"
```

Skip if step 1 already passed.

---

## Task 5: Empirical slippage endpoint

The journal stores `price` and `expected_price` per fill (not a precomputed `slippage_pct`), so the endpoint derives the signed slippage-% per row. Convention: positive = worse than expected (against the trader). For `buy` and `cover`, a fill above expected is worse (+); for `sell` and `short`, a fill below expected is worse (+).

**Files:**
- Create: `backend/routes/slippage.py`
- Create: `backend/tests/test_slippage_endpoint.py`
- Modify: `backend/main.py` — register router

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_slippage_endpoint.py`:

```python
"""Tests for GET /api/slippage/{symbol}."""
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

import json
import pytest
from fastapi.testclient import TestClient
from main import app
import journal


@pytest.fixture
def fake_journal(tmp_path, monkeypatch):
    path = tmp_path / "trade_journal.json"
    monkeypatch.setattr(journal, "JOURNAL_PATH", path)
    # Re-patch the route's reference too if it imports JOURNAL_PATH directly
    import routes.slippage as slip_mod
    monkeypatch.setattr(slip_mod, "JOURNAL_PATH", path)

    def write(trades):
        path.write_text(json.dumps({"trades": trades}, indent=2))

    return write


def test_returns_null_when_no_data(fake_journal):
    fake_journal([])
    resp = TestClient(app).get("/api/slippage/AAPL")
    assert resp.status_code == 200
    assert resp.json() == {"empirical_pct": None, "fill_count": 0}


def test_filters_by_symbol(fake_journal):
    fake_journal([
        {"symbol": "AAPL", "side": "buy", "price": 100.1, "expected_price": 100.0},
        {"symbol": "MSFT", "side": "buy", "price": 200.2, "expected_price": 200.0},
    ])
    resp = TestClient(app).get("/api/slippage/AAPL")
    body = resp.json()
    assert body["fill_count"] == 1
    assert abs(body["empirical_pct"] - 0.1) < 1e-6  # (100.1 - 100.0) / 100.0 * 100


def test_signed_for_sell_and_short(fake_journal):
    # sell fill BELOW expected → worse for long seller → positive
    # short fill BELOW expected → worse for short seller → positive
    fake_journal([
        {"symbol": "X", "side": "sell", "price": 99.0, "expected_price": 100.0},
        {"symbol": "X", "side": "short", "price": 99.0, "expected_price": 100.0},
    ])
    body = TestClient(app).get("/api/slippage/X").json()
    # Both rows are +1.0% — mean 1.0
    assert abs(body["empirical_pct"] - 1.0) < 1e-6
    assert body["fill_count"] == 2


def test_includes_favorable_fills(fake_journal):
    # buy below expected = favorable (negative)
    fake_journal([
        {"symbol": "X", "side": "buy", "price": 99.0, "expected_price": 100.0},
    ])
    body = TestClient(app).get("/api/slippage/X").json()
    assert body["empirical_pct"] < 0
    assert body["fill_count"] == 1


def test_skips_rows_missing_expected_price(fake_journal):
    fake_journal([
        {"symbol": "X", "side": "buy", "price": 100.0, "expected_price": None},
        {"symbol": "X", "side": "buy", "price": 101.0, "expected_price": 100.0},
    ])
    body = TestClient(app).get("/api/slippage/X").json()
    assert body["fill_count"] == 1
    assert abs(body["empirical_pct"] - 1.0) < 1e-6


def test_symbol_case_insensitive(fake_journal):
    fake_journal([
        {"symbol": "AAPL", "side": "buy", "price": 100.1, "expected_price": 100.0},
    ])
    body = TestClient(app).get("/api/slippage/aapl").json()
    assert body["fill_count"] == 1
```

- [ ] **Step 2: Run — expect failure (route doesn't exist)**

Run: `cd backend && pytest tests/test_slippage_endpoint.py -v`
Expected: 404 on every test or import error.

- [ ] **Step 3: Implement the endpoint**

Create `backend/routes/slippage.py`:

```python
"""GET /api/slippage/{symbol} — empirical slippage from trade journal."""
import json
from fastapi import APIRouter
from journal import JOURNAL_PATH

router = APIRouter()

# "worse than expected" sign per side
_WORSE_IF_FILL_IS = {
    "buy": "above",    # long entry — higher fill is worse
    "cover": "above",  # short exit — higher fill is worse
    "sell": "below",   # long exit — lower fill is worse
    "short": "below",  # short entry — lower fill is worse
}


def _signed_slippage_pct(side: str, price: float, expected: float) -> float | None:
    direction = _WORSE_IF_FILL_IS.get(side)
    if direction is None or expected is None or expected == 0:
        return None
    raw_pct = (price - expected) / expected * 100
    return raw_pct if direction == "above" else -raw_pct


@router.get("/api/slippage/{symbol}")
def get_empirical_slippage(symbol: str):
    if not JOURNAL_PATH.exists():
        return {"empirical_pct": None, "fill_count": 0}
    try:
        trades = json.loads(JOURNAL_PATH.read_text()).get("trades", [])
    except (json.JSONDecodeError, OSError):
        return {"empirical_pct": None, "fill_count": 0}

    values: list[float] = []
    sym_u = symbol.upper()
    for t in trades:
        if t.get("symbol", "").upper() != sym_u:
            continue
        price = t.get("price")
        expected = t.get("expected_price")
        side = t.get("side")
        if price is None or expected is None:
            continue
        slip = _signed_slippage_pct(side, price, expected)
        if slip is None:
            continue
        values.append(slip)

    if not values:
        return {"empirical_pct": None, "fill_count": 0}
    return {
        "empirical_pct": round(sum(values) / len(values), 4),
        "fill_count": len(values),
    }
```

- [ ] **Step 4: Register the router**

In `backend/main.py`, find the import block with the other route imports (search for `from routes.bots`). Add:

```python
from routes.slippage import router as slippage_router
```

Then find the block with `app.include_router(bots_router)` (line 49) and append:

```python
app.include_router(slippage_router)
```

- [ ] **Step 5: Run — expect pass**

Run: `cd backend && pytest tests/test_slippage_endpoint.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/routes/slippage.py backend/tests/test_slippage_endpoint.py backend/main.py
git commit -m "feat: GET /api/slippage/{symbol} — empirical slippage from journal"
```

---

## Task 6: Frontend types

**Files:**
- Modify: `frontend/src/shared/types/index.ts`

- [ ] **Step 1: Extend `StrategyRequest`**

Find the `StrategyRequest` interface (~line 89). After the `commission_pct?: number` line add:

```ts
  per_share_rate?: number      // IBKR Fixed per-share commission, default 0.0035
  min_per_order?: number       // IBKR Fixed min per order, default 0.35
  borrow_rate_annual?: number  // % per year, applied only when direction === 'short'
```

- [ ] **Step 2: Extend `Trade`**

Find the `Trade` interface (~line 140). After the `commission?: number` line add:

```ts
  borrow_cost?: number   // only on exit legs of short trades; 0 or undefined otherwise
```

- [ ] **Step 3: Extend `SavedStrategy` for persistence**

Find the `SavedStrategy` interface (~line 119). After the `commission: number | ''` line add:

```ts
  perShareRate?: number
  minPerOrder?: number
  borrowRateAnnual?: number
```

Mark them optional so older localStorage snapshots (pre-migration) still parse.

- [ ] **Step 4: Verify types compile**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors (or only pre-existing errors unrelated to these fields).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/types/index.ts
git commit -m "types: add per-share commission + borrow fields to StrategyRequest/Trade/SavedStrategy"
```

---

## Task 7: `useEmpiricalSlippage` hook

**Files:**
- Create: `frontend/src/shared/hooks/useEmpiricalSlippage.ts`

- [ ] **Step 1: Write the hook**

Create `frontend/src/shared/hooks/useEmpiricalSlippage.ts`:

```ts
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

export interface EmpiricalSlippage {
  empirical_pct: number | null
  fill_count: number
}

/** Fetch empirical per-symbol slippage from the live trade journal.
 *  Returns `{empirical_pct: null, fill_count: 0}` when no journal rows exist. */
export function useEmpiricalSlippage(symbol: string) {
  return useQuery<EmpiricalSlippage>({
    queryKey: ['slippage', symbol.toUpperCase()],
    queryFn: async () => {
      const { data } = await api.get(`/api/slippage/${symbol.toUpperCase()}`)
      return data
    },
    enabled: !!symbol,
    staleTime: 60 * 1000,
  })
}
```

- [ ] **Step 2: Compile check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/shared/hooks/useEmpiricalSlippage.ts
git commit -m "feat: useEmpiricalSlippage hook"
```

---

## Task 8: StrategyBuilder — Capital & Fees rewrite

Replaces the Commission % input with per-share/min fields, adds empirical-slippage auto-populate + badge, adds a conditional Short Costs section, wires new fields into save/load/payload, and fires a one-time migration toast for pre-existing saved strategies.

**Files:**
- Modify: `frontend/src/features/strategy/StrategyBuilder.tsx`

### 8a — State + persistence

- [ ] **Step 1: Add hook import and state for the three new fields + slippage source tracking**

At the top of `StrategyBuilder.tsx`, add to the import block:

```ts
import { useEmpiricalSlippage } from '../../shared/hooks/useEmpiricalSlippage'
```

Below the existing `const [slippage, setSlippage] = ...` / `const [commission, setCommission] = ...` lines (~lines 62–63), add:

```ts
  const [perShareRate, setPerShareRate] = useState<number>(saved?.perShareRate ?? 0.0035)
  const [minPerOrder, setMinPerOrder] = useState<number>(saved?.minPerOrder ?? 0.35)
  const [borrowRateAnnual, setBorrowRateAnnual] = useState<number>(saved?.borrowRateAnnual ?? 0.5)
  const [slippageSource, setSlippageSource] = useState<'empirical' | 'default' | 'manual'>('default')
  const { data: empiricalSlip } = useEmpiricalSlippage(ticker)
```

- [ ] **Step 2: Auto-populate slippage when empirical data changes**

Directly below the new state lines, add an effect that updates slippage whenever the empirical reading changes, unless the user has edited manually:

```ts
  useEffect(() => {
    if (slippageSource === 'manual') return
    if (empiricalSlip?.empirical_pct != null && empiricalSlip.fill_count > 0) {
      setSlippage(empiricalSlip.empirical_pct)
      setSlippageSource('empirical')
    } else {
      setSlippage(0.01)
      setSlippageSource('default')
    }
  }, [empiricalSlip?.empirical_pct, empiricalSlip?.fill_count, slippageSource])
```

- [ ] **Step 3: Update `currentSnapshot` + `loadSavedStrategy` + persistence effect**

In `currentSnapshot` (~line 73), add the new fields to the returned object:

```ts
      slippage, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual,
```

In `loadSavedStrategy` (~line 94) — after `setSlippage(s.slippage); setCommission(s.commission)`:

```ts
    setPerShareRate(s.perShareRate ?? 0.0035)
    setMinPerOrder(s.minPerOrder ?? 0.35)
    setBorrowRateAnnual(s.borrowRateAnnual ?? 0.5)
    setSlippageSource('manual')  // loading a saved strategy pins the stored slippage
```

In the `useEffect` that writes `STRATEGY_STORAGE_KEY` (~line 122), add the new fields to the saved payload and to the dependency array:

```ts
  useEffect(() => {
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({
      buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss,
      trailingEnabled, trailingConfig, dynamicSizing, tradingHours, slippage, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual,
    }))
  }, [buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss,
      trailingEnabled, trailingConfig, dynamicSizing, tradingHours, slippage, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual])
```

### 8b — Request payload

- [ ] **Step 4: Include the new fields in the backtest request**

In `runBacktest` (~line 136), update the `StrategyRequest` object. Remove `commission_pct` from the payload (field stays on the type for back-compat but frontend no longer sends it), and add the three new fields:

```ts
      const req: StrategyRequest = {
        ticker, start, end, interval,
        buy_rules: buyRules, sell_rules: sellRules,
        buy_logic: buyLogic, sell_logic: sellLogic,
        initial_capital: capital, position_size: posSize / 100,
        stop_loss_pct: stopLoss !== '' && stopLoss > 0 ? stopLoss : undefined,
        trailing_stop: trailingEnabled ? trailingConfig : undefined,
        dynamic_sizing: dynamicSizing.enabled ? dynamicSizing : undefined,
        trading_hours: tradingHours.enabled ? tradingHours : undefined,
        slippage_pct: slippage !== '' && slippage !== 0 ? slippage : undefined,
        per_share_rate: perShareRate,
        min_per_order: minPerOrder,
        borrow_rate_annual: direction === 'short' ? borrowRateAnnual : 0,
        source: dataSource, debug, direction,
        ma_type: maSettings?.type,
        sg8_window: maSettings?.sg8Window,
        sg8_poly: maSettings?.sg8Poly,
        sg21_window: maSettings?.sg21Window,
        sg21_poly: maSettings?.sg21Poly,
        predictive_sg: maSettings?.predictiveSg,
        use_sg8: maSettings?.showSg8 ?? true,
        use_sg21: maSettings?.showSg21 ?? true,
      }
```

Note: `slippage` can now be negative (empirical favorable fill), so the check is `!== 0` instead of `> 0`.

### 8c — Capital & Fees UI

- [ ] **Step 5: Replace the Commission row with per-share + min fields, add slippage badge, add Short Costs section**

Find the Capital & Fees group (~lines 173–191). Replace the entire `<div style={styles.settingsGroup}>` block for Capital & Fees with:

```tsx
        {/* Column 1: Capital & Fees */}
        <div style={styles.settingsGroup}>
          <div style={styles.groupTitle}>Capital &amp; Fees</div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Capital ($)</label>
            <input type="number" value={capital} onChange={e => setCapital(+e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>% of Capital</label>
            <input type="number" value={posSize} step={1} min={1} max={100} onChange={e => setPosSize(+e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Slippage (%)</label>
            <input
              type="number"
              value={slippage}
              step={0.005}
              placeholder="0"
              onChange={e => {
                const v = e.target.value
                if (v === '') {
                  setSlippageSource('default')
                  setSlippage(empiricalSlip?.empirical_pct ?? 0.01)
                } else {
                  setSlippage(+v)
                  setSlippageSource('manual')
                }
              }}
              style={styles.settingsInput}
            />
            <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 6 }}>
              {slippageSource === 'empirical' && empiricalSlip
                ? `empirical: ${empiricalSlip.fill_count} fills${(empiricalSlip.empirical_pct ?? 0) < 0 ? ' ⚠ favorable' : ''}`
                : slippageSource === 'default'
                ? 'default: 0.01%'
                : 'manual'}
            </span>
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Rate per share ($)</label>
            <input type="number" value={perShareRate} step={0.0005} min={0} onChange={e => setPerShareRate(+e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Min per order ($)</label>
            <input type="number" value={minPerOrder} step={0.05} min={0} onChange={e => setMinPerOrder(+e.target.value)} style={styles.settingsInput} />
          </div>

          {direction === 'short' && (
            <>
              <div style={{ ...styles.groupTitle, marginTop: 12 }}>Short Costs</div>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Borrow rate (%/yr)</label>
                <input type="number" value={borrowRateAnnual} step={0.1} min={0} onChange={e => setBorrowRateAnnual(+e.target.value)} style={styles.settingsInput} />
              </div>
            </>
          )}
        </div>
```

### 8d — Migration toast

- [ ] **Step 6: Fire the one-time commission-migration toast**

At the top of the `StrategyBuilder` component body (directly after `const saved = useState(() => loadStrategy())[0]` on line 42), add:

```tsx
  useEffect(() => {
    const NOTIFY_KEY = 'commission_migration_notified'
    if (localStorage.getItem(NOTIFY_KEY)) return
    const legacy = saved && (saved.commission !== undefined) && saved.perShareRate === undefined
    if (!legacy) return
    alert(
      'Commission model updated — now using IBKR per-share ($0.0035/share, $0.35 min). ' +
      'Adjust in Settings if needed.'
    )
    localStorage.setItem(NOTIFY_KEY, '1')
  }, [saved])
```

`alert` is used instead of a real toast component to avoid pulling in new UI deps — swap for the project's toast helper later if one exists. The guard keys off "has legacy `commission` field but no `perShareRate`" so brand-new users don't see it.

### 8e — Verification

- [ ] **Step 7: Build + manual smoke**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

Then start the app (`./start.sh`) and check:
- Load the Strategy Builder — Capital & Fees shows Slippage, Rate per share, Min per order
- Changing ticker updates the slippage value + badge text
- Toggle direction to "short" → Short Costs section appears with Borrow rate (%/yr)
- Clearing the Slippage field reverts to empirical/default
- Run a backtest, confirm request succeeds (network tab shows `per_share_rate`, `min_per_order`, `borrow_rate_annual` in the payload)

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/strategy/StrategyBuilder.tsx
git commit -m "feat: StrategyBuilder — per-share commission + empirical slippage + short borrow UI"
```

---

## Task 9: Results — Borrow column + Cost Breakdown

Adds a Borrow column to the Trades tab (shows `sell.borrow_cost` for shorts, `—` for longs) and a Cost Breakdown block on the Summary tab summing per-trade commission, borrow, and slippage into an aggregate "cost drag" % of starting capital.

**Files:**
- Modify: `frontend/src/features/strategy/Results.tsx`

- [ ] **Step 1: Add `Borrow` header cell to the Trades tab**

Find the Trades table header block (~lines 421–433). After the `Comm` `<span>` (line 431) and before the `Exit` `<span>` (line 432), insert:

```tsx
              <span style={{ ...styles.tradeCell, width: 50, color: '#8b949e', fontSize: 10 }}>Borrow</span>
```

- [ ] **Step 2: Add `Borrow` data cell to each trade row**

Find the Trades row rendering (~lines 440–463). After the `Comm` `<span>` (line 457–459) and before the `Exit` `<span>` (line 460), insert:

```tsx
                  <span style={{ ...styles.tradeCell, width: 50, color: (sell.borrow_cost ?? 0) > 0 ? '#f0883e' : '#484f58' }}>
                    {(sell.borrow_cost ?? 0) > 0 ? `$${sell.borrow_cost!.toFixed(2)}` : '—'}
                  </span>
```

- [ ] **Step 3: Add Cost Breakdown block to the Summary tab**

Find the `{activeTab === 'summary' && (...)}` block (~line 330). Add a new `<div>` inside it, immediately before the closing `</div>` of the outer wrapper at line 368. Insert this before `</div>` on line 368:

```tsx
          {(() => {
            const buys = trades.filter(t => t.type === 'buy' || t.type === 'short')
            const sellsList = trades.filter(t => t.type === 'sell' || t.type === 'cover')
            const totalComm = [...buys, ...sellsList].reduce((s, t) => s + (t.commission ?? 0), 0)
            const totalBorrow = sellsList.reduce((s, t) => s + (t.borrow_cost ?? 0), 0)
            const totalSlip = [...buys, ...sellsList].reduce((s, t) => s + (t.slippage ?? 0), 0)
            const totalAll = totalComm + totalBorrow + totalSlip
            const dragPct = summary.initial_capital > 0 ? (totalAll / summary.initial_capital) * 100 : 0
            const hasShorts = sellsList.some(t => t.type === 'cover')
            if (summary.num_trades === 0) return null
            return (
              <div style={{ display: 'flex', flexDirection: 'column', padding: '12px 16px', borderTop: '1px solid #21262d', gap: 4 }}>
                <div style={{ marginBottom: 4 }}>
                  <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>Cost Breakdown</span>
                </div>
                <CostRow label="Total commission" value={totalComm} />
                {hasShorts && <CostRow label="Total borrow cost" value={totalBorrow} />}
                <CostRow label="Total slippage" value={totalSlip} />
                <CostRow label="Total all-in costs" value={totalAll} bold />
                <CostRow label="Cost drag" value={dragPct} suffix="%" color="#f0883e" bold />
              </div>
            )
          })()}
```

- [ ] **Step 4: Add the `CostRow` helper component**

Near the top of `Results.tsx` (alongside `StatRow` — search for `function StatRow` to locate it), add:

```tsx
function CostRow({ label, value, suffix, color, bold }: {
  label: string; value: number; suffix?: string; color?: string; bold?: boolean
}) {
  const formatted = suffix === '%' ? `${value.toFixed(2)}%` : `$${value.toFixed(2)}`
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
      <span style={{ color: '#8b949e', fontWeight: bold ? 600 : 400 }}>{label}</span>
      <span style={{ color: color ?? '#e6edf3', fontWeight: bold ? 700 : 500 }}>{formatted}</span>
    </div>
  )
}
```

- [ ] **Step 5: Compile + manual check**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

Then run a short-direction backtest in the app and verify:
- Trades tab shows a "Borrow" column, populated for shorts, `—` for longs
- Summary tab shows Cost Breakdown block with commission/borrow/slippage/total/drag
- Long-only strategies hide the borrow row

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/strategy/Results.tsx
git commit -m "feat: Results — Borrow column + Cost Breakdown summary block"
```

---








