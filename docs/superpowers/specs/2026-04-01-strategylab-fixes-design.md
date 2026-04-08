# StrategyLab Fixes — Design Spec
**Date:** 2026-04-01

## Overview

Four issues found during initial use of StrategyLab:

1. MACD/RSI indicator panes don't scroll/zoom with the main chart
2. Strategy tester math explodes with extreme returns (root cause: ambiguous position size UX + no backend validation)
3. Equity curve is too small to be useful (100px in a 140px panel)
4. Buy/sell markers disappear after a backtest (root cause: NaN bug produces 0 trades; also skipped in overlay mode)

---

## 1. Indicator Pane Sync

**File:** `frontend/src/components/Chart.tsx`

### Problem
The main chart, MACD chart, and RSI chart are three separate `createChart()` instances with no coordination. Scrolling or zooming the main chart does not move the indicator panes.

### Solution
After all three charts are initialized, subscribe to `visibleLogicalRangeChange` on the main chart's time scale and push the new range to the MACD and RSI time scales. The refs (`chartRef`, `macdChartRef`, `rsiChartRef`) are already available — only the subscription wiring is missing.

**Sync direction:** main → sub-panes only (no feedback loop). Sub-pane scroll events do not propagate back to the main chart.

**Lifecycle:** the subscription is set up in a dedicated `useEffect` that depends on `[showMacd, showRsi]`. It cleans up the subscription on unmount or when panes are toggled off.

---

## 2. Strategy Math Fixes

### 2a. Position Size UX

**Files:** `frontend/src/components/StrategyBuilder.tsx`, `backend/main.py`

**Problem:** The input is labeled "Position size" with a raw decimal (default 1.0, max=1 as HTML hint only). Users naturally interpret this as a dollar amount. Entering `10000` as "deploy $10,000" sends a 10,000× multiplier to the backend, causing capital to go massively negative on the first buy, which spirals into nonsensical returns.

**Solution:**
- Change the input to "% of Capital", range 1–100, default 100, step 1
- Before sending the API request, divide by 100: `position_size = posSize / 100`
- Backend adds a guard: `position_size = max(0.01, min(1.0, position_size))`

### 2b. NaN Rule Values

**Files:** `frontend/src/components/StrategyBuilder.tsx`

**Problem:** When the user clears a value input field, `parseFloat('')` returns `NaN`. JSON serializes `NaN` as `null`. The backend receives `rule.value = None`, the value condition is skipped, and zero trades execute. The equity curve is flat, markers disappear, and there's no error message.

**Solution:** Before calling the API, validate that every rule with a value-based condition (`above`, `below`, `crosses_above`, `crosses_below`) has a finite numeric value. If any rule fails validation, show an inline error ("Rule is missing a value") and abort the request.

---

## 3. Tabbed Results Panel

**Files:** `frontend/src/components/Results.tsx`

### Problem
The results panel is a fixed 140px container. The equity chart gets 100px — too small to read meaningfully. The metrics, equity curve, and trade list are all crammed into a horizontal strip.

### Solution
Replace the fixed layout with a tabbed panel (~220px tall). Three tabs:

| Tab | Content |
|-----|---------|
| **Summary** | 7 metrics grid (return, B&H, final value, trades, win rate, Sharpe, max DD) |
| **Equity Curve** | lightweight-charts line chart at ~180px height |
| **Trades** | Scrollable list of completed trades with date, P&L, P&L% |

- Default active tab: **Summary**
- Tab state is local to `Results` — `App.tsx` requires no changes
- Panel height: `220px` (fixed, `flexShrink: 0`)

---

## 4. Buy/Sell Markers Reliability

**File:** `frontend/src/components/Chart.tsx`

### Problem
Markers are already implemented for normal (candlestick) mode but two issues prevent them from showing:

1. The NaN bug (fixed in §2b) causes 0 trades, so there's nothing to render
2. In SPY/QQQ overlay mode the chart uses a line series instead of candlesticks, and the markers block is inside the `else` branch — so markers are never attached

### Solution
- **Normal mode:** no code change needed — markers work once trades are non-empty
- **Overlay mode:** after creating the main ticker line series, call `createSeriesMarkers(mainLineSeries, markers)` using the same markers array. Arrows will appear on the % change line chart.

---

## Files Changed

| File | Changes |
|------|---------|
| `frontend/src/components/Chart.tsx` | Add time scale sync effect; add markers in overlay mode |
| `frontend/src/components/StrategyBuilder.tsx` | % of Capital input; NaN validation before API call |
| `frontend/src/components/Results.tsx` | Replace fixed layout with tabbed panel |
| `backend/main.py` | Clamp `position_size` to [0.01, 1.0] |

---

## Out of Scope

- RSI overbought/oversold reference lines reflecting user-defined values (deferred)
- SPY/QQQ overlay keeping candlesticks (separate known issue, not in this batch)
- Volume checkbox wiring (separate known issue, not in this batch)
- Intraday timeframes (separate known issue, not in this batch)
