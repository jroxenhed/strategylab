# Shorting Support

Add short-selling to both the backtester and live bot runner, plus a bot card layout refresh.

## Data Model

Add `direction: "long" | "short"` (default `"long"`) to:
- `StrategyRequest` in `backtest.py`
- `BotConfig` in `bot_manager.py`

Default ensures all existing bots and saved strategies continue to work without migration.

Trade records (backtest results, trade journal, bot activity log) include `direction` in every entry. Trade types for shorts use `"short"` / `"cover"` instead of `"buy"` / `"sell"`.

## Backtest Engine

The rule engine (`eval_rules`) is direction-agnostic. All inversion happens in the execution layer of `run_backtest()`:

| Aspect | Long | Short |
|---|---|---|
| Entry slippage | `price * (1 + slippage_pct/100)` (higher fill) | `price * (1 - slippage_pct/100)` (lower fill) |
| Exit slippage | `price * (1 - slippage_pct/100)` (lower fill) | `price * (1 + slippage_pct/100)` (higher fill) |
| Fixed stop-loss | Exit if `low <= entry * (1 - sl_pct/100)` | Exit if `high >= entry * (1 + sl_pct/100)` |
| Trailing stop: track | Peak (highest price via `high` or `close`) | Trough (lowest price via `low` or `close`) |
| Trailing stop: exit | Price drops X% from peak | Price rises X% from trough |
| Trailing source swap | `source: "high"` tracks highs, `"close"` tracks closes | Mirrored: `"high"` maps to `low`, `"close"` stays `close` |
| PnL | `(exit - entry) * shares` | `(entry - exit) * shares` |
| Equity while holding | `capital + position * price` | `capital + position * (2 * entry - price)` |

Dynamic sizing, trading hours, debug trace, and EMA overlay logic are unchanged.

## Bot Runner

**Entry orders:**
- Long: `OrderSide.BUY`
- Short: `OrderSide.SELL` (Alpaca treats this as sell-short when no position exists)

**Stop-loss management for shorts:**
Bot-managed via polling only (no OTO bracket). Alpaca's OTO bracket semantics don't cleanly support a stop above entry for shorts. The bot already manages trailing stops via polling, so this is consistent. Trade-off: no server-side safety net if the bot process dies while in a short position.

**Exit:**
`close_position()` works for both directions — Alpaca handles buy-to-cover automatically.

**PnL:**
- Long: `(sell_fill - entry_price) * qty`
- Short: `(entry_price - cover_fill) * qty`

**Trailing stop tracking:**
- Long: `trail_peak = max(trail_peak, source_price)`, exit if `price <= trail_peak * (1 - pct/100)`
- Short: `trail_trough = min(trail_trough, source_price)`, exit if `price >= trail_trough * (1 + pct/100)`

**Slippage tracking:**
- Long entry: `slippage = fill - expected` (positive = worse)
- Short entry: `slippage = expected - fill` (positive = worse, lower fill is worse)

**Position detection:**
Currently checks `alpaca_qty > 0`. For shorts, Alpaca returns negative qty. Use `abs(alpaca_qty) > 0`.

**Manual entry button:**
"Manual Buy" becomes "Manual Entry". For short bots, sends a sell-short order.

## Frontend: Strategy Builder

- Direction toggle at the top of the strategy form: "Long" / "Short" (default Long)
- When direction is "short", labels change: "Buy Rules" -> "Entry Rules", "Sell Rules" -> "Exit Rules"
- Direction included in `StrategyRequest` payload to backtest endpoint
- Backtest trade table shows "short"/"cover" types for short strategies

## Frontend: Add Bot Bar

- Direction dropdown or toggle alongside existing fields (symbol, interval, strategy, data source)

## Frontend: Bot Card Layout Refresh

**Two-column layout (replaces current full-width stacked layout):**

```
+--[Left column, auto width]---+--[Right column, flex: 1]--+
| Strategy name [LONG badge]   |                           |
| Allocated $1,000  Trades 6   |   Mini sparkline chart    |
| P&L +$81  Status Running     |   (price + equity overlay)|
| [Stop] [Buy] [Show Log]     |                           |
+------------------------------+---------------------------+
| [expand: activity log only]                              |
+----------------------------------------------------------+
```

- Left column: `flex: 0 auto` — takes the space it needs for stats/buttons
- Right column: `flex: 1` — mini chart fills remaining width, grows with window
- Chart height: ~60-80px, shows price line + equity curve overlaid in distinct colors
- Chart is always visible (no expand required)
- Expand toggle now only reveals the activity log

**Direction styling:**
- Subtle background tint: long = `rgba(0, 200, 0, 0.03)`, short = `rgba(200, 0, 0, 0.03)`
- Direction badge pill next to strategy name ("LONG" / "SHORT")

## Frontend: Trade Results

- Direction column or badge in the trades table
- Trade type labels: "short"/"cover" for short-direction trades

## Not in Scope

- Borrow cost estimation (paper trading doesn't charge; real account concern for later)
- Bot grouping by ticker (just direction badges for now)
- Portfolio equity chart (separate TODO)
- Pre-market/extended hours for shorts (same behavior as longs)
