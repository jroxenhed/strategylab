# Paper Trading Polish

Targeted improvements to the Paper Trading page: journal table enhancements, bot card heartbeat, and positions poll alignment.

## Approach

Approach B — all journal/positions work is frontend-only. Heartbeat adds a small `last_tick` field to the bot runner backend so the frontend can detect a truly alive bot (not just "last action").

## 1. Journal Table Enhancements

### New columns

Column order: Time, Symbol, Side, Qty, **Expected**, Price, P&L, **Gain %**, Slippage, Source, Reason

- **Expected** — `expected_price` formatted as `$200.64`, or `—` if null. Before Price so you read: expected → actual → slippage.
- **Gain %** — for exit rows with a paired entry: `(pnl / (entry_price * qty)) * 100`. Shown as `+0.41%` / `-0.29%`, green/red matching P&L color. `—` for entries and unpaired exits.

### Reason column color fix

`reasonColor()` becomes `reasonColor(reason, pnl)`:

| Reason | Color |
|---|---|
| `entry` | `#e5c07b` (orange) — matches Side column, chart markers |
| `signal` | green (`#26a641`) if winning exit, red (`#f85149`) if losing — matches Side column P&L logic |
| `stop_loss` | `#f85149` (red) — unchanged |
| `trailing_stop` | `#d29922` (amber) — unchanged |
| `manual` | `#8b949e` (grey) — unchanged |
| null/other | `#8b949e` (grey) — unchanged |

### Summary row

A thin row pinned below the column headers, muted styling. Shows aggregates for the visible (filtered) trades:

| Column | Summary value |
|---|---|
| Qty | Total qty across all visible trades |
| P&L | Sum of all exit P&Ls |
| Gain % | Average gain % across exits |
| Slippage | Average slippage % |
| All other cells | Blank |

Recomputed when the symbol filter changes.

### Filter field relocation

Move the filter input from the far right of the header to just after the title + count badge + reload button. Removes `marginLeft: 'auto'` from the filter.

## 2. Journal Auto-Refresh & CSV Export

### Auto-refresh

`setInterval(reload, 5000)` in the existing `useEffect`. Manual refresh button stays for immediate reload.

### CSV export

Download button in the journal header, positioned far-right with `marginLeft: 'auto'` (where the filter used to be). Generates CSV from the currently filtered + computed data including Expected and Gain % columns.

Implementation: `Blob` + `URL.createObjectURL` + programmatic click. No backend change.

Filename: `trades-{symbol|all}-{YYYY-MM-DD}.csv`

## 3. Bot Card Heartbeat

### Backend

`BotState` gets `last_tick: str | None`. The bot runner loop updates it with an ISO timestamp every iteration, before signal evaluation. Persisted to `bots.json`.

### Frontend

Small dot on the bot card near the existing status indicator:

- **Green** — `last_tick` within 2x the bot's polling interval
- **Red** — stale beyond that threshold
- **Grey** — bot is stopped (no heartbeat expected)

No new API call — `last_tick` arrives via the existing `fetchBotDetail` poll (every 2s when running).

## 4. Positions Poll Interval

`PositionsTable.tsx`: change `setInterval(load, 30_000)` to `setInterval(load, 5_000)`. Matches journal refresh cadence.

Add a small "Updated Xs ago" text in the section header (next to the count badge). Tracks time since last successful fetch, updates every second via a separate `setInterval`. Shows seconds when < 60s, minutes otherwise.

## Files affected

| File | Changes |
|---|---|
| `frontend/src/features/trading/TradeJournal.tsx` | New columns (Expected, Gain %), reason color fix, summary row, filter relocation, auto-refresh, CSV export button |
| `frontend/src/features/trading/BotCard.tsx` | Heartbeat dot using `last_tick` from detail |
| `frontend/src/features/trading/PositionsTable.tsx` | Poll interval 30s → 5s, "Updated Xs ago" indicator |
| `frontend/src/api/trading.ts` | No changes — `JournalTrade` already has `expected_price` |
| `backend/bot_runner.py` | Update `last_tick` each loop iteration |
| `backend/bot_manager.py` | Add `last_tick: str | None = None` to `BotState` (line 75) |
| `frontend/src/shared/types/index.ts` | Add `last_tick` to `BotDetail`/`BotState` type if needed |

## Out of scope

- Sparkline after 1 round-trip (user will wait for more trades)
- Backend journal enrichment (P&L pairing stays client-side)
