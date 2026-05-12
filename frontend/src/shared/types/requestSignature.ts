import type { StrategyRequest } from './strategy'

/**
 * Returns a stable JSON string covering the fields that determine cache validity.
 * Used by both the cache-write side and the restore predicate in App.tsx so the
 * two call sites stay in sync — adding a field here covers both automatically.
 */
export function requestSignature(req: StrategyRequest): string {
  return JSON.stringify({
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
  })
}
