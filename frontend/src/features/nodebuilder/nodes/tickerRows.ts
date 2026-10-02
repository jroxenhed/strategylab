/** Ticker card rows (S32c), kept apart from TickerNode.tsx so fast refresh works there. */

/**
 * The rows a Ticker card shows (S32c, FA4): symbol, interval and prefix;
 * never source. A group's primary Ticker has bars of its own, so it gets no
 * prefix row (UX-09), unless a prefix is stored there by mistake: then the
 * row stays so it can be cleared.
 */
export function tickerRowParams(params: Record<string, unknown>, isPrimary = false): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if ('symbol' in params) out.symbol = params.symbol
  if ('interval' in params) out.interval = params.interval
  const prefix = typeof params.prefix === 'string' ? params.prefix : ''
  if (!isPrimary || prefix !== '') out.prefix = prefix
  return out
}
