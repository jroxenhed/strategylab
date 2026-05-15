import type { StrategyRequest } from './strategy'

/**
 * The 17 fields that determine cache validity. Adding a field to this interface
 * is the single point of change — both `cacheWriteKey` (write-side projection,
 * if a caller wants the plain object) and `requestSignature` (read predicate)
 * use this shape, so write/read symmetry is enforced structurally.
 *
 * The persisted cache payload stores the FULL request, not this projection —
 * downstream code (WFA / Optimizer panels) re-sends the request body to the
 * backend and needs all fields. The signature is only used as the equality
 * predicate when deciding whether a cached result still matches the UI state.
 */
export interface CacheKey {
  ticker: StrategyRequest['ticker']
  start: StrategyRequest['start']
  end: StrategyRequest['end']
  interval: StrategyRequest['interval']
  buy_rules: StrategyRequest['buy_rules'] | null
  sell_rules: StrategyRequest['sell_rules'] | null
  buy_logic: StrategyRequest['buy_logic'] | null
  sell_logic: StrategyRequest['sell_logic'] | null
  long_buy_rules: StrategyRequest['long_buy_rules'] | null
  long_sell_rules: StrategyRequest['long_sell_rules'] | null
  long_buy_logic: StrategyRequest['long_buy_logic'] | null
  long_sell_logic: StrategyRequest['long_sell_logic'] | null
  short_buy_rules: StrategyRequest['short_buy_rules'] | null
  short_sell_rules: StrategyRequest['short_sell_rules'] | null
  short_buy_logic: StrategyRequest['short_buy_logic'] | null
  short_sell_logic: StrategyRequest['short_sell_logic'] | null
  regime: StrategyRequest['regime'] | null
}

/** Projects a StrategyRequest into the 17-field CacheKey shape. */
export function cacheWriteKey(req: StrategyRequest): CacheKey {
  return {
    ticker: req.ticker,
    start: req.start,
    end: req.end,
    interval: req.interval,
    buy_rules: req.buy_rules ?? null,
    sell_rules: req.sell_rules ?? null,
    buy_logic: req.buy_logic ?? null,
    sell_logic: req.sell_logic ?? null,
    long_buy_rules: req.long_buy_rules ?? null,
    long_sell_rules: req.long_sell_rules ?? null,
    long_buy_logic: req.long_buy_logic ?? null,
    long_sell_logic: req.long_sell_logic ?? null,
    short_buy_rules: req.short_buy_rules ?? null,
    short_sell_rules: req.short_sell_rules ?? null,
    short_buy_logic: req.short_buy_logic ?? null,
    short_sell_logic: req.short_sell_logic ?? null,
    regime: req.regime ?? null,
  }
}

/** Stable JSON of the 17 cache-key fields. Equality predicate on both sides. */
export function requestSignature(req: StrategyRequest): string {
  return JSON.stringify(cacheWriteKey(req))
}
