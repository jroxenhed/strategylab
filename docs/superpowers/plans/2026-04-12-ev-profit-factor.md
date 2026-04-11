# EV/trade + Profit Factor with Decomposition Waterfall — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface expected value per trade and profit factor as headline numbers on the backtest summary tab, with a 3-row waterfall that visually decomposes how wins and losses net out to EV.

**Architecture:** Backend adds a pure helper `_edge_stats(gains, losses, num_sells)` next to the existing `_side_stats`, computes four new fields (`gross_profit`, `gross_loss`, `ev_per_trade`, `profit_factor`), and wires them into the existing summary dict returned from `POST /api/backtest`. Frontend extends `BacktestResult['summary']`, adds an EV/PF header row, and renders an inline `EvWaterfall` component above the existing StatRows + histogram. No new routes, no new Pydantic models (the route returns a plain dict — the spec's mention of `StrategyResponse` in `models.py` is inaccurate; that model does not exist).

**Tech Stack:** Python 3 + FastAPI (backend), pytest (backend tests), React + TypeScript + Vite (frontend). No Jest/Vitest in the frontend — frontend verification is manual browser checks.

**Spec:** `docs/superpowers/specs/2026-04-12-ev-profit-factor-design.md`

---

## File Structure

### Backend

- **Modify** `backend/routes/backtest.py`
  - Add `_edge_stats(gains, losses, num_sells) -> dict` helper next to existing `_side_stats` (around line 13–23).
  - Call it at line ~278 (after `pnl_distribution` is computed) and spread its four keys into the `summary` dict returned at line ~316.
- **Modify** `backend/tests/test_backtest_stats.py`
  - Import `_edge_stats` from `routes.backtest`.
  - Add four new tests: mixed, all_wins, all_losses, with_breakeven.

No change to `backend/models.py` — `StrategyResponse` does not exist; the route returns a dict.

### Frontend

- **Modify** `frontend/src/shared/types/index.ts`
  - Add four fields to `BacktestResult['summary']`: `gross_profit: number`, `gross_loss: number`, `ev_per_trade: number | null`, `profit_factor: number | null`.
- **Modify** `frontend/src/features/strategy/Results.tsx`
  - Restructure the P&L Distribution block (lines 156–184) to add an EV/PF header row and an inline `EvWaterfall` component above the existing flex row of StatRows + histogram.
  - `EvWaterfall` lives inline in the same file as a small function component (≤50 lines), matching the style of the existing `StatRow` helper at the bottom of the file.

No test file changes in frontend — project has no frontend test harness. Verification is manual browser check.

---

## Task 1: Backend — add `_edge_stats` helper with first test (TDD)

**Files:**
- Modify: `backend/tests/test_backtest_stats.py`
- Modify: `backend/routes/backtest.py:13-23` (add helper directly after `_side_stats`)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_backtest_stats.py`:

```python
from routes.backtest import _edge_stats


def test_edge_stats_mixed():
    # 10 wins averaging $100, 5 losses averaging -$50
    gains = [100.0] * 10
    losses = [-50.0] * 5
    num_sells = 15
    result = _edge_stats(gains, losses, num_sells)
    assert result["gross_profit"] == 1000.0
    assert result["gross_loss"] == 250.0
    assert result["ev_per_trade"] == 50.0
    assert result["profit_factor"] == 4.0
```

- [ ] **Step 2: Run the test and verify it fails**

Run:
```bash
cd backend/tests && python -m pytest test_backtest_stats.py::test_edge_stats_mixed -v
```

Expected: `ImportError: cannot import name '_edge_stats' from 'routes.backtest'` (collection error).

- [ ] **Step 3: Implement `_edge_stats`**

In `backend/routes/backtest.py`, insert immediately after the `_side_stats` function (after line 23):

```python
def _edge_stats(gains: list[float], losses: list[float], num_sells: int) -> dict:
    """Expected value per trade + profit factor.

    gross_profit = sum of winning P&Ls.
    gross_loss   = absolute sum of losing P&Ls (always >= 0).
    ev_per_trade = (gross_profit - gross_loss) / num_sells, or None if num_sells == 0.
    profit_factor = gross_profit / gross_loss, or None if gross_loss == 0 (frontend renders
                    this as ∞ when gross_profit > 0, or — when there are no trades at all).
                    Python cannot serialize float('inf') to JSON, so None is the sentinel.
    """
    gross_profit = round(sum(gains), 2)
    gross_loss = round(abs(sum(losses)), 2)
    ev_per_trade = round((gross_profit - gross_loss) / num_sells, 2) if num_sells > 0 else None
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else None
    return {
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "ev_per_trade": ev_per_trade,
        "profit_factor": profit_factor,
    }
```

- [ ] **Step 4: Run the test and verify it passes**

Run:
```bash
cd backend/tests && python -m pytest test_backtest_stats.py::test_edge_stats_mixed -v
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/routes/backtest.py backend/tests/test_backtest_stats.py
git commit -m "feat(backtest): add _edge_stats helper for EV/trade + profit factor"
```

---

## Task 2: Backend — cover all_wins / all_losses / breakeven edge cases

**Files:**
- Modify: `backend/tests/test_backtest_stats.py`

- [ ] **Step 1: Write the three additional failing tests**

Append to `backend/tests/test_backtest_stats.py`:

```python
def test_edge_stats_all_wins():
    # 5 wins, no losses — profit_factor should be None (frontend renders ∞)
    gains = [100.0, 150.0, 200.0, 50.0, 100.0]
    losses: list[float] = []
    num_sells = 5
    result = _edge_stats(gains, losses, num_sells)
    assert result["gross_profit"] == 600.0
    assert result["gross_loss"] == 0.0
    assert result["profit_factor"] is None
    assert result["ev_per_trade"] == 120.0


def test_edge_stats_all_losses():
    # 5 losses, no wins — profit_factor should be 0.0 (gross_profit / gross_loss == 0 / N)
    gains: list[float] = []
    losses = [-100.0, -50.0, -75.0, -25.0, -50.0]
    num_sells = 5
    result = _edge_stats(gains, losses, num_sells)
    assert result["gross_profit"] == 0.0
    assert result["gross_loss"] == 300.0
    assert result["profit_factor"] == 0.0
    assert result["ev_per_trade"] == -60.0


def test_edge_stats_with_breakeven():
    # 2 wins of $100, 2 losses of -$100, 1 break-even trade (excluded from gains/losses).
    # Break-even dilutes EV via num_sells.
    gains = [100.0, 100.0]
    losses = [-100.0, -100.0]
    num_sells = 5
    result = _edge_stats(gains, losses, num_sells)
    assert result["gross_profit"] == 200.0
    assert result["gross_loss"] == 200.0
    assert result["ev_per_trade"] == 0.0
    assert result["profit_factor"] == 1.0


def test_edge_stats_no_trades():
    # No trades at all — both EV and PF should be None
    result = _edge_stats([], [], 0)
    assert result["gross_profit"] == 0.0
    assert result["gross_loss"] == 0.0
    assert result["ev_per_trade"] is None
    assert result["profit_factor"] is None
```

- [ ] **Step 2: Run the new tests and verify they pass**

Run:
```bash
cd backend/tests && python -m pytest test_backtest_stats.py -v
```

Expected: all 8 tests pass (4 pre-existing `_side_stats` tests + 4 new `_edge_stats` tests). The helper from Task 1 already handles these cases, so no implementation changes are needed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_backtest_stats.py
git commit -m "test(backtest): cover _edge_stats edge cases (all wins, all losses, breakeven, empty)"
```

---

## Task 3: Backend — wire `_edge_stats` into the `/api/backtest` summary dict

**Files:**
- Modify: `backend/routes/backtest.py:278` (after `pnl_distribution` is computed)
- Modify: `backend/routes/backtest.py:316-328` (summary dict in `result`)

- [ ] **Step 1: Compute edge stats right after `pnl_distribution`**

In `backend/routes/backtest.py`, find this block (currently around line 274–278):

```python
        gains = [float(t["pnl"]) for t in sell_trades if t.get("pnl", 0) > 0]
        losses = [float(t["pnl"]) for t in sell_trades if t.get("pnl", 0) < 0]
        gain_stats = _side_stats(gains)
        loss_stats = _side_stats(losses)
        pnl_distribution = [round(float(t.get("pnl", 0)), 2) for t in sell_trades]
```

Add one line immediately after:

```python
        gains = [float(t["pnl"]) for t in sell_trades if t.get("pnl", 0) > 0]
        losses = [float(t["pnl"]) for t in sell_trades if t.get("pnl", 0) < 0]
        gain_stats = _side_stats(gains)
        loss_stats = _side_stats(losses)
        pnl_distribution = [round(float(t.get("pnl", 0)), 2) for t in sell_trades]
        edge_stats = _edge_stats(gains, losses, len(sell_trades))
```

- [ ] **Step 2: Spread the four new fields into the `summary` dict**

In `backend/routes/backtest.py`, find the `summary` dict (currently around line 316–328):

```python
        result = {
            "summary": {
                "initial_capital": req.initial_capital,
                "final_value": round(final_value, 2),
                "total_return_pct": round(total_return, 2),
                "buy_hold_return_pct": round(buy_hold_return, 2),
                "num_trades": len(sell_trades),
                "win_rate_pct": round(win_rate, 2),
                "sharpe_ratio": round(sharpe, 3),
                "max_drawdown_pct": round(max_drawdown, 2),
                "gain_stats": gain_stats,
                "loss_stats": loss_stats,
                "pnl_distribution": pnl_distribution,
            },
```

Replace with:

```python
        result = {
            "summary": {
                "initial_capital": req.initial_capital,
                "final_value": round(final_value, 2),
                "total_return_pct": round(total_return, 2),
                "buy_hold_return_pct": round(buy_hold_return, 2),
                "num_trades": len(sell_trades),
                "win_rate_pct": round(win_rate, 2),
                "sharpe_ratio": round(sharpe, 3),
                "max_drawdown_pct": round(max_drawdown, 2),
                "gain_stats": gain_stats,
                "loss_stats": loss_stats,
                "pnl_distribution": pnl_distribution,
                **edge_stats,
            },
```

The `**edge_stats` spread adds `gross_profit`, `gross_loss`, `ev_per_trade`, `profit_factor`.

- [ ] **Step 3: Run the full backend test suite to ensure nothing regressed**

Run:
```bash
cd backend/tests && python -m pytest -q
```

Expected: all tests pass (including the 8 from `test_backtest_stats.py` and any short/provider/model tests).

- [ ] **Step 4: Smoke-test the live endpoint**

Start the backend if not already running:
```bash
./start.sh
```

From another terminal, call the endpoint with a small known-good request:
```bash
curl -s -X POST http://localhost:8000/api/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "AAPL",
    "start": "2023-01-01",
    "end": "2023-06-01",
    "interval": "1d",
    "buy_rules": [{"indicator": "rsi_14", "condition": "below", "value": 30}],
    "sell_rules": [{"indicator": "rsi_14", "condition": "above", "value": 70}],
    "source": "yahoo"
  }' | python -m json.tool | grep -E "gross_profit|gross_loss|ev_per_trade|profit_factor"
```

Expected: all four fields appear in the output. Values depend on the data — just verify they're present and of the right type (numbers or null).

- [ ] **Step 5: Commit**

```bash
git add backend/routes/backtest.py
git commit -m "feat(backtest): include EV/trade + profit factor in summary response"
```

---

## Task 4: Frontend — extend `BacktestResult` type

**Files:**
- Modify: `frontend/src/shared/types/index.ts:139-152`

- [ ] **Step 1: Add the four new fields to the summary type**

Find this block in `frontend/src/shared/types/index.ts`:

```typescript
export interface BacktestResult {
  summary: {
    initial_capital: number
    final_value: number
    total_return_pct: number
    buy_hold_return_pct: number
    num_trades: number
    win_rate_pct: number
    sharpe_ratio: number
    max_drawdown_pct: number
    gain_stats?: SideStats
    loss_stats?: SideStats
    pnl_distribution?: number[]
  }
```

Replace with:

```typescript
export interface BacktestResult {
  summary: {
    initial_capital: number
    final_value: number
    total_return_pct: number
    buy_hold_return_pct: number
    num_trades: number
    win_rate_pct: number
    sharpe_ratio: number
    max_drawdown_pct: number
    gain_stats?: SideStats
    loss_stats?: SideStats
    pnl_distribution?: number[]
    gross_profit?: number
    gross_loss?: number
    ev_per_trade?: number | null
    profit_factor?: number | null
  }
```

All four are marked optional (`?`) so that older cached responses or any consumer not touching them continues to type-check. `ev_per_trade` and `profit_factor` are `number | null` because the backend returns `None` (→ JSON `null`) when they're undefined.

- [ ] **Step 2: Verify the frontend still type-checks**

Run:
```bash
cd frontend && npx tsc --noEmit
```

Expected: exits 0 with no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/shared/types/index.ts
git commit -m "feat(types): add gross_profit/loss + ev_per_trade + profit_factor to summary"
```

---

## Task 5: Frontend — EV/PF header row in Results.tsx

**Files:**
- Modify: `frontend/src/features/strategy/Results.tsx:156-184`

We will restructure the P&L Distribution block so the outer container becomes a vertical column containing (1) the label + toggle header, (2) a new EV/PF row, (3) a placeholder for the waterfall (added in Task 6), and (4) the existing flex row of StatRows + histogram. The label + toggle currently lives inside the left inner column; we pull it out to the top of the block so it spans full width.

- [ ] **Step 1: Restructure the outer block**

Find the current block (lines 156–184):

```tsx
          {summary.num_trades > 0 && (summary.gain_stats || summary.loss_stats) && (
            <div style={{ display: 'flex', gap: 24, padding: '12px 16px', borderTop: '1px solid #21262d', alignItems: 'flex-start' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 180 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>P&amp;L Distribution</span>
                  <div style={{ display: 'flex', gap: 2, background: '#0d1117', border: '1px solid #21262d', borderRadius: 3, padding: 1 }}>
                    {(['mean', 'median'] as const).map(m => (
                      <button
                        key={m}
                        onClick={() => setAvgMode(m)}
                        style={{
                          fontSize: 9, padding: '1px 6px', border: 'none', cursor: 'pointer', borderRadius: 2,
                          background: avgMode === m ? '#1e3a5f' : 'transparent',
                          color: avgMode === m ? '#e6edf3' : '#8b949e',
                        }}
                      >{m}</button>
                    ))}
                  </div>
                </div>
                <StatRow label="Max gain" value={summary.gain_stats?.max} color="#26a641" />
                <StatRow label={`Avg gain (${avgMode})`} value={summary.gain_stats?.[avgMode]} color="#26a641" />
                <StatRow label="Min gain" value={summary.gain_stats?.min} color="#26a641" />
                <StatRow label="Max loss" value={summary.loss_stats?.min} color="#f85149" />
                <StatRow label={`Avg loss (${avgMode})`} value={summary.loss_stats?.[avgMode]} color="#f85149" />
                <StatRow label="Min loss" value={summary.loss_stats?.max} color="#f85149" />
              </div>
              <PnlHistogram values={summary.pnl_distribution ?? []} />
            </div>
          )}
```

Replace it with this new structure (keeps StatRows and histogram identical; adds header at top, EV/PF row, and a waterfall placeholder):

```tsx
          {summary.num_trades > 0 && (summary.gain_stats || summary.loss_stats) && (
            <div style={{ display: 'flex', flexDirection: 'column', padding: '12px 16px', borderTop: '1px solid #21262d' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 0.5 }}>P&amp;L Distribution</span>
                <div style={{ display: 'flex', gap: 2, background: '#0d1117', border: '1px solid #21262d', borderRadius: 3, padding: 1 }}>
                  {(['mean', 'median'] as const).map(m => (
                    <button
                      key={m}
                      onClick={() => setAvgMode(m)}
                      style={{
                        fontSize: 9, padding: '1px 6px', border: 'none', cursor: 'pointer', borderRadius: 2,
                        background: avgMode === m ? '#1e3a5f' : 'transparent',
                        color: avgMode === m ? '#e6edf3' : '#8b949e',
                      }}
                    >{m}</button>
                  ))}
                </div>
              </div>

              <EvPfHeader
                evPerTrade={summary.ev_per_trade ?? null}
                profitFactor={summary.profit_factor ?? null}
                grossProfit={summary.gross_profit ?? 0}
              />

              {/* Waterfall component added in Task 6 */}

              <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', marginTop: 12 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 180 }}>
                  <StatRow label="Max gain" value={summary.gain_stats?.max} color="#26a641" />
                  <StatRow label={`Avg gain (${avgMode})`} value={summary.gain_stats?.[avgMode]} color="#26a641" />
                  <StatRow label="Min gain" value={summary.gain_stats?.min} color="#26a641" />
                  <StatRow label="Max loss" value={summary.loss_stats?.min} color="#f85149" />
                  <StatRow label={`Avg loss (${avgMode})`} value={summary.loss_stats?.[avgMode]} color="#f85149" />
                  <StatRow label="Min loss" value={summary.loss_stats?.max} color="#f85149" />
                </div>
                <PnlHistogram values={summary.pnl_distribution ?? []} />
              </div>
            </div>
          )}
```

- [ ] **Step 2: Add the `EvPfHeader` component at the bottom of `Results.tsx`**

At the bottom of `frontend/src/features/strategy/Results.tsx`, just above the closing `const styles` object (after the existing `StatRow` function ends at line 309), insert:

```tsx
function EvPfHeader({
  evPerTrade,
  profitFactor,
  grossProfit,
}: {
  evPerTrade: number | null
  profitFactor: number | null
  grossProfit: number
}) {
  const evColor = evPerTrade == null ? '#8b949e' : evPerTrade > 0 ? '#26a641' : '#f85149'
  const evText =
    evPerTrade == null
      ? '—'
      : `${evPerTrade >= 0 ? '+' : ''}$${evPerTrade.toFixed(2)} / trade`

  // PF is null when gross_loss == 0. Render ∞ if there were wins, — if there were none.
  let pfColor: string
  let pfText: string
  if (profitFactor == null) {
    if (grossProfit > 0) {
      pfColor = '#26a641'
      pfText = '∞'
    } else {
      pfColor = '#8b949e'
      pfText = '—'
    }
  } else {
    pfColor = profitFactor > 1 ? '#26a641' : '#f85149'
    pfText = profitFactor.toFixed(2)
  }

  const suffix = <span style={{ fontSize: 10, color: '#8b949e', marginLeft: 4 }}>(mean)</span>

  return (
    <div style={{ display: 'flex', gap: 32, alignItems: 'baseline', marginBottom: 8 }}>
      <div>
        <span style={{ fontSize: 10, color: '#8b949e', marginRight: 6 }}>EV</span>
        <span style={{ fontSize: 16, fontWeight: 700, color: evColor }}>{evText}</span>
        {suffix}
      </div>
      <div>
        <span style={{ fontSize: 10, color: '#8b949e', marginRight: 6 }}>PF</span>
        <span style={{ fontSize: 16, fontWeight: 700, color: pfColor }}>{pfText}</span>
        {suffix}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Type-check the frontend**

Run:
```bash
cd frontend && npx tsc --noEmit
```

Expected: exits 0 with no errors.

- [ ] **Step 4: Visual check in browser**

If the dev server is not running:
```bash
./start.sh
```

Open `http://localhost:5173`, run any backtest, and confirm on the Summary tab:
- The "P&L DISTRIBUTION" label + mean/median toggle still appear at the top.
- A new row below it shows `EV +$XX.XX / trade (mean)` and `PF X.XX (mean)` in green/red.
- StatRows and histogram still render below, unchanged.
- Colors match: EV green for positive, red for non-positive.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/strategy/Results.tsx
git commit -m "feat(results): add EV/trade + profit factor header row to P&L block"
```

---

## Task 6: Frontend — inline `EvWaterfall` component

**Files:**
- Modify: `frontend/src/features/strategy/Results.tsx` (drop-in replacement for the `{/* Waterfall component added in Task 6 */}` placeholder from Task 5, plus a new `EvWaterfall` function at the bottom of the file).

The waterfall is a 4-column CSS grid with up to three rows: Wins, Losses, Net. Bar widths are proportional to the absolute contribution of each row, normalized to the larger of the two (wins vs losses) so one row always fills the bar column. The Net row has no computation column and includes a 1px top border as a separator.

- [ ] **Step 1: Replace the Task 5 placeholder with the `EvWaterfall` invocation**

In `frontend/src/features/strategy/Results.tsx`, find the placeholder line added in Task 5:

```tsx
              {/* Waterfall component added in Task 6 */}
```

Replace with:

```tsx
              <EvWaterfall
                winRatePct={summary.win_rate_pct}
                avgGain={summary.gain_stats?.mean ?? 0}
                avgLoss={Math.abs(summary.loss_stats?.mean ?? 0)}
                grossProfit={summary.gross_profit ?? 0}
                grossLoss={summary.gross_loss ?? 0}
                numSells={summary.num_trades}
                evPerTrade={summary.ev_per_trade ?? null}
              />
```

- [ ] **Step 2: Add the `EvWaterfall` function at the bottom of `Results.tsx`**

At the bottom of `frontend/src/features/strategy/Results.tsx`, after the `EvPfHeader` function and before `const styles`, insert:

```tsx
function EvWaterfall({
  winRatePct,
  avgGain,
  avgLoss,
  grossProfit,
  grossLoss,
  numSells,
  evPerTrade,
}: {
  winRatePct: number
  avgGain: number
  avgLoss: number
  grossProfit: number
  grossLoss: number
  numSells: number
  evPerTrade: number | null
}) {
  if (numSells <= 0) return null

  // Prefer gross_*/num_sells over winRate × avg to avoid rounding drift with the backend.
  const winContribution = grossProfit / numSells
  const lossContribution = grossLoss / numSells
  const netContribution = evPerTrade ?? 0
  const lossRatePct = 100 - winRatePct

  const showWins = grossProfit > 0
  const showLosses = grossLoss > 0
  const maxContribution = Math.max(winContribution, lossContribution, 0.0001)

  const barFor = (value: number, max: number) => ({
    width: `${Math.min(100, (Math.abs(value) / max) * 100)}%`,
    height: 14,
    borderRadius: 3,
  })

  const fmtSigned = (v: number) => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(2)}`
  const fmtUnsigned = (v: number) => `$${Math.abs(v).toFixed(2)}`
  const netColor = netContribution > 0 ? '#26a641' : netContribution < 0 ? '#f85149' : '#8b949e'

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '55px 1fr auto auto',
        columnGap: 12,
        rowGap: 4,
        alignItems: 'center',
        marginTop: 4,
        marginBottom: 4,
        fontFamily: 'monospace',
        fontSize: 11,
      }}
    >
      {showWins && (
        <>
          <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase' }}>Wins</span>
          <div style={{ ...barFor(winContribution, maxContribution), background: '#26a641' }} />
          <span style={{ color: '#8b949e' }}>
            {winRatePct.toFixed(1)}% × {fmtUnsigned(avgGain)} =
          </span>
          <span style={{ color: '#26a641', textAlign: 'right' }}>{fmtSigned(winContribution)}</span>
        </>
      )}

      {showLosses && (
        <>
          <span style={{ fontSize: 10, color: '#8b949e', textTransform: 'uppercase' }}>Losses</span>
          <div style={{ ...barFor(lossContribution, maxContribution), background: '#f85149' }} />
          <span style={{ color: '#8b949e' }}>
            {lossRatePct.toFixed(1)}% × {fmtUnsigned(avgLoss)} =
          </span>
          <span style={{ color: '#f85149', textAlign: 'right' }}>{fmtSigned(-lossContribution)}</span>
        </>
      )}

      <span
        style={{
          fontSize: 10,
          color: '#8b949e',
          textTransform: 'uppercase',
          borderTop: '1px solid #30363d',
          paddingTop: 4,
          marginTop: 2,
        }}
      >
        Net
      </span>
      <div
        style={{
          ...barFor(netContribution, maxContribution),
          background: netColor,
          marginTop: 6,
        }}
      />
      <span style={{ borderTop: '1px solid #30363d', paddingTop: 4, marginTop: 2 }} />
      <span
        style={{
          color: netColor,
          textAlign: 'right',
          borderTop: '1px solid #30363d',
          paddingTop: 4,
          marginTop: 2,
        }}
      >
        {fmtSigned(netContribution)}
      </span>
    </div>
  )
}
```

Notes on the layout:
- The top border on the Net row is achieved by giving each of the four Net cells its own `borderTop` + matching `paddingTop`/`marginTop`, so the separator spans the whole grid even with `rowGap`.
- `barFor` clamps to 100% and uses `Math.max(..., 0.0001)` as a floor to avoid division-by-zero when both gross values happen to be 0 (shouldn't happen once `numSells > 0` and at least one side is non-zero, but the guard keeps the component total).
- The Losses row's value cell uses `fmtSigned(-lossContribution)` so it renders with a leading `-`. `lossContribution` itself is always ≥ 0 (derived from `grossLoss = |sum(losses)|`).

- [ ] **Step 3: Type-check the frontend**

Run:
```bash
cd frontend && npx tsc --noEmit
```

Expected: exits 0 with no errors.

- [ ] **Step 4: Visual check in browser**

With the dev server running (`./start.sh` if not), open `http://localhost:5173` and run a mixed-result backtest (e.g., AAPL 1d, RSI 14 < 30 buy / > 70 sell, 2023-01-01 → 2023-12-31). Confirm:
- Three rows under the EV/PF header: Wins (green bar), Losses (red bar), Net (green or red bar).
- Bar widths are proportional; the larger of Wins/Losses fills the bar column.
- Computation column shows `{rate}% × ${avg} =` for Wins and Losses only.
- Net row has a subtle `#30363d` top border and no computation text.
- Sign of the Losses value is negative (`-$X.XX`).
- Net value sign matches the EV header.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/strategy/Results.tsx
git commit -m "feat(results): add EV decomposition waterfall (Wins / Losses / Net)"
```

---

## Task 7: Manual browser validation of all spec cases

No code changes. Verify each edge case listed in the spec and confirm it renders correctly. If any step fails, open a follow-up task; don't try to fix multiple things in one commit.

**Files:** none (verification only).

- [ ] **Step 1: Golden path — mixed result**

Run a known mixed-result backtest (e.g., the BABA 15m short example from the spec, or an AAPL RSI strategy over a long window). Confirm:
- EV header matches `(gross_profit - gross_loss) / num_trades` computed by hand from StatRows + `num_trades`.
- PF header matches `gross_profit / gross_loss` to 2 decimals.
- Waterfall shows 3 rows, sign of Net matches EV.
- Bars are proportional and the larger of wins/losses fills the bar column.

- [ ] **Step 2: All wins**

Find or construct a short/trivial backtest where every closed trade wins (e.g., a 1d AAPL sell-on-next-bar strategy during a monotone uptrend). Confirm:
- PF shows `∞` in green.
- EV shows positive dollars in green.
- Waterfall: no Losses row; Wins row fills the bar column; Net bar equals Wins bar; Net value equals Wins value.

- [ ] **Step 3: All losses**

Invert the strategy (or pick an uptrend backtest with an inverted trigger) so every closed trade is a loss. Confirm:
- PF shows `0.00` in red.
- EV shows negative dollars in red.
- Waterfall: no Wins row; Losses row fills the bar column; Net bar equals Losses bar in red; Net value equals the negative Losses value.

- [ ] **Step 4: Short strategy**

Re-run the golden-path strategy with `direction: short`. Confirm:
- EV/PF/waterfall render identically — all sign logic flows through `pnl` which is already direction-adjusted in the backtest engine.
- No layout regressions versus the long version.

- [ ] **Step 5: Zero-trades guard**

Run a backtest with rules that never fire (e.g., RSI < 1). Confirm:
- The P&L Distribution block is hidden entirely (existing guard `num_trades > 0 && (gain_stats || loss_stats)` still holds).
- No EV/PF header, no waterfall, no StatRows, no histogram.

- [ ] **Step 6: Regression check on other summary panels**

Switch to Equity Curve and Trades tabs. Confirm nothing visual changed there. Switch back to Summary and confirm the top `metricsGrid` (Return, B&H Return, Final Value, Trades, Win Rate, Sharpe, Max DD) is untouched.

- [ ] **Step 7: No commit required**

This task is verification only. If everything passes, move on to merging. If anything fails, open a new task against the failing case and fix it before finishing the branch.

---

## Self-Review

**Spec coverage:**
- Backend `_edge_stats` + 4 new summary fields → Tasks 1–3.
- Backend edge case tests (mixed, all_wins, all_losses, with_breakeven) → Tasks 1–2. Plan also adds a bonus `no_trades` test to cover the `num_sells == 0` branch of `_edge_stats`.
- `models.py` change from spec → **intentionally omitted**. `StrategyResponse` does not exist in this codebase; the route returns a dict. This is documented in the plan header and file structure section.
- Frontend type extension → Task 4.
- EV/PF header row with color rules, `(mean)` suffix, ∞/— handling → Task 5.
- EvWaterfall 3-row grid with bar proportional to contribution, computation column, separator above Net → Task 6.
- Edge cases (all wins hides Losses, all losses hides Wins, break-even dilutes EV, zero trades hides block) → Task 6 logic + Task 7 verification.
- Manual frontend testing checklist → Task 7, one bullet per spec row.
- Out of scope items (bot cards, Trades tab waterfall, alternative EV definitions) → not implemented. ✔

**Placeholder scan:** No "TBD", "implement later", "similar to Task N", or "add validation" phrases. All code blocks are complete and runnable.

**Type/name consistency:** `_edge_stats` signature (`gains, losses, num_sells`) is consistent across Task 1 definition and Task 3 caller. Returned keys (`gross_profit`, `gross_loss`, `ev_per_trade`, `profit_factor`) match across backend helper, route response, TypeScript type, and component props. `EvPfHeader` and `EvWaterfall` prop names are consistent between Task 5 and Task 6. `winRatePct` vs. `win_rate_pct` — the frontend type uses `win_rate_pct` (already established), and the component converts to camelCase internally.
