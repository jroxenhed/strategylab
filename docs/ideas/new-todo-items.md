# New TODO Items — Draft

## A — Charts & Indicators

- [ ] **A13** Multi-timeframe indicator overlay — indicator sidebar gets optional timeframe selector (1D, 1W). When set, fetches higher-TF OHLCV independently, computes indicator on those bars, and paints as a stepped overlay on the intraday chart (each daily value draws flat across all intraday bars within that day, steps at next daily close). Solves the current workaround of using MA(500) on 30m as a proxy for daily MA. Same data-fetch pipeline becomes the backend foundation for B21 regime filter. [medium]

  **Implementation traps (from claude.ai discussion):**
  - **Lookahead bias on alignment:** A daily MA value must map to all intraday bars of the *following* trading day, not the same day. Using the current day's incomplete bar leaks forward-looking data into the overlay. Step the value at market open using the prior completed daily close.
  - **Weekend/holiday gaps:** Daily bars skip non-trading days but intraday bars don't know that. Monday's 30-min bars must map to Friday's daily close — don't interpolate phantom Saturday/Sunday bars.
  - **Fetch caching:** If multiple indicators use the same higher timeframe, fetch the daily OHLCV once and share across indicator computes. Don't re-fetch per indicator instance.
  - **Relationship to A8b:** "View as" (A8b) already decouples display from backtest interval via aggregation. The data direction is opposite (A8b aggregates intraday up to coarser bars, A13 paints daily down onto intraday), but check whether any of the alignment/mapping logic is reusable.

## B — Strategy Engine & Rules

- [ ] **B21** Regime filter — higher-timeframe directional gate on strategy. Dedicated config section (separate from entry/exit rules): pick a timeframe (1D/1W), indicator, and condition. Regime evaluates before entry logic runs:
  - MA rising → long mode (buy rules active, short rules skipped)
  - MA falling → short mode (short rules active, buy rules skipped)
  - Flat/ambiguous → no trades
  
  Strategy carries both long and short rule sets; regime decides which is active per bar. Backtester fetches higher-TF bars alongside trade-interval bars, computes regime indicator once, creates per-bar regime lookup. On Alpaca (position-netting), regime is mutually exclusive by design — no simultaneous long/short conflict. Bot runner evaluates regime on each tick using cached daily bars.
  
  Prereqs: A13 data pipeline (higher-TF fetch + alignment). Related: B20 (multi-TF confirmation) shares the data layer but is a different feature — B20 confirms entries within one direction, B21 gates direction itself.
  
  This is the key prerequisite for "set and forget" bot deployment — without it, directional bots bleed when the regime flips and nobody's watching. [large]

  **Implementation traps (from claude.ai discussion):**
  - **Mid-position regime flip:** If the bot is long and the daily MA rolls over, what happens? Three options with different risk profiles: (a) immediately close the position — safest but may exit prematurely on a one-day dip, (b) let normal sell rules handle the exit — respects the strategy but could hold into a reversal, (c) prevent new entries but let current position play out — middle ground. Needs a deliberate design choice, not an accident.
  - **Regime flap zone:** When price oscillates around the daily MA, the regime will flip back and forth rapidly. The bot could enter long, get flipped to short next day, whipsaw repeatedly. Mitigation options: require N consecutive days in the new regime before switching, use a band (MA ± ATR) instead of a single line, or enforce a cooldown period after a regime change.
  - **Backtest vs live regime evaluation:** In backtesting, the daily close is known and alignment is clean. In live trading (`bot_runner._tick()`), the bot needs to evaluate regime on each tick using the most recent *completed* daily bar. How often does it refresh the daily data? Every tick is wasteful; once at market open is clean but needs a "daily bar cache" with TTL logic.
  - **Strategy schema change:** Currently a strategy has one direction (long/short) and one set of buy/sell rules. B21 needs the strategy to carry two rule sets (long entry/exit + short entry/exit) plus the regime config. This touches `StrategyRequest`, saved strategy JSON, the strategy builder UI, and the backtest engine. Big surface area — consider whether v1 could keep a single rule set and just gate on/off (regime allows longs → trade, regime says short → sit flat), deferring dual rule sets to v2.
  - **Alpaca netting safety:** Even though the regime is mutually exclusive by design, add a hard guard in the bot runner: before entering a new direction, verify no existing position in the opposite direction. Belt and suspenders — regime logic bugs shouldn't create accidental hedges that Alpaca nets into surprises.
  - **UI complexity:** The strategy builder currently has BUY/SELL panels with a LONG/SHORT toggle. Adding a regime section + dual rule sets could make the UI overwhelming. Consider a progressive disclosure approach: regime filter is a collapsible section that only appears when enabled, and dual rules only show when regime is configured.
