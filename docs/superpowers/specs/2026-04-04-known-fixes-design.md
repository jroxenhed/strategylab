# StrategyLab Known Fixes — Design Spec
**Date:** 2026-04-04

## Overview

Three fixes for known issues in StrategyLab:

1. SPY/QQQ comparison overlay replaces candlestick chart — needs to overlay instead
2. Volume checkbox is wired in the sidebar but not connected to the chart
3. No intraday timeframes (1m, 5m, 15m, 30m, 1h)

---

## Fix 1: SPY/QQQ Overlay

### Problem
When the user checks SPY or QQQ in the Compare section, the main candlestick chart is replaced with a normalized % change line chart for all tickers. The original chart is lost and the user can no longer see candles, trade markers, or indicators alongside the comparison.

### Solution
Keep the main ticker as a candlestick chart. SPY and/or QQQ appear as line series overlaid on top, using a **separate price scale** (`priceScaleId: 'overlay'`) on the right side of the same chart. This keeps the candlestick view intact and adds the comparison lines without replacing anything.

The normalized % change data (`normalizedSpy`, `normalizedQqq`) is already computed in `Chart.tsx` — only the rendering branch changes. The `showOverlay` conditional that currently switches chart mode is removed; SPY/QQQ lines are simply added when their data is present.

### Files Changed
| File | Change |
|------|--------|
| `frontend/src/components/Chart.tsx` | Remove the overlay/candlestick branch switch. Always render candlesticks. Add SPY/QQQ as `LineSeries` with `priceScaleId: 'overlay'` when enabled. |

---

## Fix 2: Volume Indicator

### Problem
The Volume checkbox exists in the sidebar and `'volume'` is listed as an `IndicatorKey`, but checking it has no effect — nothing is wired to the chart.

### Solution
When `activeIndicators` includes `'volume'`, add a `HistogramSeries` to the main chart using the existing `volume` field from OHLCV data. Bars are green on up-days, red on down-days (matching candle colors). Volume uses a dedicated hidden price scale (`priceScaleId: 'volume'`) with `scaleMargins: { top: 0.75, bottom: 0 }` so bars occupy only the bottom ~25% of the chart and don't interfere with the price axis.

No backend changes required — volume is already in the OHLCV response.

### Files Changed
| File | Change |
|------|--------|
| `frontend/src/components/Chart.tsx` | Add volume `HistogramSeries` to main chart when `activeIndicators` includes `'volume'`. Use dedicated scale with top margin. |
| `frontend/src/components/Sidebar.tsx` | Confirm Volume checkbox passes `'volume'` through `activeIndicators` (currently it may be disconnected). |

---

## Fix 3: Intraday Timeframes

### Problem
The interval dropdown only offers "Daily". No intraday options exist.

### Backend constraint
yfinance limits how far back intraday data is available:

| Interval | Max lookback |
|----------|-------------|
| 1m | 7 days |
| 5m | 60 days |
| 15m | 60 days |
| 30m | 60 days |
| 1h | 730 days |
| 1d | No limit |

### Solution

**Dropdown:** Add intervals `1m`, `5m`, `15m`, `30m`, `1h`, `1d` (rename "Daily" to `1d` to match yfinance). Display labels are human-readable (e.g. "1 min", "5 min", "Daily").

**Warning:** When the selected interval + current date range exceeds the yfinance limit, display a warning banner directly below the interval dropdown. The warning includes the specific limit and the user's current range, e.g.:

> *"5m data only supports 60 days of history. Your range is 90 days — please shorten the From date."*

No auto-correction is applied. The user adjusts the date range themselves. The Run Backtest button remains enabled — the backend will return an error or empty data, and the warning makes the cause clear.

**Backend:** Accept the new interval strings and pass them directly to yfinance. No other changes needed.

### Files Changed
| File | Change |
|------|--------|
| `frontend/src/components/Sidebar.tsx` | Expand interval dropdown with new options and human-readable labels. Add warning logic: compute date range span, compare against per-interval limit, render warning banner when exceeded. |
| `backend/main.py` | Accept new interval values (`1m`, `5m`, `15m`, `30m`, `1h`, `1d`). Rename `daily` → `1d` if needed to match yfinance. |

---

## Out of Scope

- Crosshair sync between main chart and SPY/QQQ overlay (deferred)
- Auto-adjusting date range when interval changes (user adjusts manually)
- Adding SPY/QQQ comparison to intraday intervals (no constraint, just not explicitly in scope)
- Any new indicators beyond volume
