# A1 — Portfolio Summary Strip: Design Spec

## Goal

Add a portfolio summary strip to BotControlCenter that shows aggregate bot stats and a combined P&L sparkline, visually aligned with the per-bot sparklines below.

## Layout

Placed between `AddBotBar` and the bot card list. Only visible when at least 1 bot has equity snapshots (i.e., has closed at least one trade).

Two-column layout mirroring BotCard:

```
┌──────────────────────────────────────────────────────────────────────┐
│ Portfolio                                                            │
│                                                                      │
│ P&L: +$1,234.56 (4.2%)   Allocated: $29,500   ┌──────────────────┐ │
│ Bots: 3 running / 5 total   Win rate: 60%      │ ~~sparkline~~~   │ │
│                                                  └──────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

Left column — summary stats:
- **Total P&L** ($) with % of total allocated, colored green/red
- **Total Allocated** — sum of all bots' `allocated_capital`
- **Bot count** — `N running / M total`
- **Profitable bots** — `N / M bots` where N = bots with `total_pnl > 0`, M = bots with `trades_count > 0`. Displayed as a count ratio, not a percentage, since 2–5 bots makes percentages misleadingly coarse.

Right column — combined sparkline using the existing `MiniSparkline` component.

## Combined equity data

Pure frontend aggregation from `BotSummary.equity_snapshots[]` — no new backend endpoint.

Algorithm:
1. Filter bots to those with defined, non-empty `equity_snapshots`.
2. Collect all `{time, value, bot_id}` triples from those bots.
3. Sort by time.
4. Maintain a running map of `bot_id → last_known_value` (staircase carry-forward).
5. At each timestamp, update the relevant bot's value in the map, then sum all values → that's the portfolio point.
6. Post-walk dedup on the output array: if consecutive entries share the same timestamp, keep the last one (by that point both bot updates have been applied to the map).

This produces a staircase curve where each step corresponds to a bot closing a trade. Honest representation — no interpolation.

## Time alignment

The portfolio sparkline shares the same `alignedRange` that the bot cards use. When sparkline scale is "aligned", all sparklines (portfolio + per-bot) span the same time axis. When "local", the portfolio sparkline uses `fitContent()` (same as per-bot local mode).

## Visual distinction

Same card styling as BotCard (dark background, border, border-radius) but with a subtle label "Portfolio" in the header area. Slightly different background tint (neutral/blue) to distinguish from the directional long/short tints on bot cards.

## Component structure

New `PortfolioStrip` component in `features/trading/PortfolioStrip.tsx`. Receives:
- `bots: BotSummary[]`
- `alignedRange?: { from: number; to: number }`

All stat computation and equity merging happens inside the component via `useMemo`.
