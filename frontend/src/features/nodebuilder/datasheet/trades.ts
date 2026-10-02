/**
 * Trade entry times for the Data Sheet's "jump to trades" (S25).
 * Item 4.D passes `entryTimesFromTrades(graphResult.response.trades)` to
 * DataSheet as `tradeTimes`.
 */

import type { Trade } from '../../../shared/types/strategy'
import { timeAfter } from './format'

/** Times sorted oldest first (daily strings or unix seconds). */
export function sortTimes(times: readonly (string | number)[]): (string | number)[] {
  return [...times].sort((a, b) => (timeAfter(a, b) ? 1 : timeAfter(b, a) ? -1 : 0))
}

/** Entry times (buy and short) of a trade list, oldest first. */
export function entryTimesFromTrades(trades: readonly Trade[] | null | undefined): (string | number)[] {
  if (!trades) return []
  return sortTimes(trades.filter(t => t.type === 'buy' || t.type === 'short').map(t => t.date))
}
