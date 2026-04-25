const INTERVAL_ORDER = ['1m', '2m', '5m', '15m', '30m', '1h', '60m', '1d', '1wk', '1mo'] as const

const INTERVAL_LABELS: Record<string, string> = {
  '1m': '1m', '2m': '2m', '5m': '5m', '15m': '15m', '30m': '30m',
  '1h': '1h', '60m': '1h', '1d': '1D', '1wk': '1W', '1mo': '1M',
}

export function getCoarserIntervals(base: string): { value: string; label: string }[] {
  const baseIdx = INTERVAL_ORDER.indexOf(base as any)
  if (baseIdx < 0) return []
  const seen = new Set<string>()
  const result: { value: string; label: string }[] = []
  for (let i = baseIdx; i < INTERVAL_ORDER.length; i++) {
    const v = INTERVAL_ORDER[i]
    const label = INTERVAL_LABELS[v] ?? v
    if (seen.has(label)) continue
    seen.add(label)
    result.push({ value: v, label })
  }
  return result
}

export function isIntraday(interval: string): boolean {
  return ['1m', '2m', '5m', '15m', '30m', '1h', '60m'].includes(interval)
}
