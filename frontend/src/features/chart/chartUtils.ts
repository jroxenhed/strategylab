export function toLineData(arr: { time: string; value: number | null }[], toET: (t: any) => any) {
  return arr.map(d => d.value !== null
    ? { time: toET(d.time as any) as any, value: d.value as number }
    : { time: toET(d.time as any) as any }
  )
}

const INTERVAL_SECONDS: Record<string, number> = {
  '1m': 60, '2m': 120, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '60m': 3600,
}

export function snapTimestamp(
  ts: string | number,
  viewInterval: string,
  toET: (t: any) => any,
): string | number {
  if (typeof ts !== 'number') {
    return ts
  }
  const etTs = toET(ts) as number
  const secs = INTERVAL_SECONDS[viewInterval]
  if (secs) {
    return etTs - (etTs % secs)
  }
  // Daily+ intervals use "YYYY-MM-DD" string keys in candleTimeIndex.
  // Convert the ET-shifted unix timestamp to a date string.
  const d = new Date(etTs * 1000)
  const y = d.getUTCFullYear()
  const m = String(d.getUTCMonth() + 1).padStart(2, '0')
  const day = String(d.getUTCDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

interface TradeForSnap {
  type: string
  date: string | number
  pnl?: number
  stop_loss?: boolean
  trailing_stop?: boolean
  [key: string]: any
}

const UP = '#26a641'
const DOWN = '#f85149'

export function aggregateMarkers(
  trades: TradeForSnap[],
  candleTimeIndex: Map<string | number, number>,
  viewInterval: string,
  backtestInterval: string,
  toET: (t: any) => any,
  subPane = false,
) {
  if (viewInterval === backtestInterval) return null

  const groups = new Map<string | number, TradeForSnap[]>()
  for (const t of trades) {
    const snapped = snapTimestamp(t.date, viewInterval, toET)
    const existing = groups.get(snapped)
    if (existing) existing.push(t)
    else groups.set(snapped, [t])
  }

  const markers: any[] = []
  for (const [time, group] of groups) {
    const idx = candleTimeIndex.get(time)
    if (idx === undefined) continue

    if (group.length === 1) {
      const t = group[0]
      const isEntry = t.type === 'buy' || t.type === 'short'
      const isShortEntry = t.type === 'short'
      const isCover = t.type === 'cover'
      if (isEntry) {
        markers.push({
          time,
          position: subPane ? 'inBar' as const : (isShortEntry ? 'aboveBar' as const : 'belowBar' as const),
          color: '#e5c07b',
          shape: subPane ? 'circle' as const : (isShortEntry ? 'arrowDown' as const : 'arrowUp' as const),
          text: isShortEntry ? 'SH' : 'B',
        })
      } else {
        const win = (t.pnl ?? 0) >= 0
        markers.push({
          time,
          position: subPane ? 'inBar' as const : (isCover ? 'belowBar' as const : 'aboveBar' as const),
          color: win ? UP : DOWN,
          shape: subPane ? 'circle' as const : (isCover ? 'arrowUp' as const : 'arrowDown' as const),
          text: t.stop_loss ? 'SL' : t.trailing_stop ? 'TSL' : (isCover ? 'COV' : 'S'),
        })
      }
    } else {
      const netPnl = group.reduce((sum, t) => sum + (t.pnl ?? 0), 0)
      markers.push({
        time,
        position: subPane ? 'inBar' as const : 'aboveBar' as const,
        color: netPnl >= 0 ? UP : DOWN,
        shape: subPane ? 'circle' as const : 'arrowDown' as const,
        text: `${group.length}T`,
      })
    }
  }

  markers.sort((a, b) => {
    if (typeof a.time === 'number' && typeof b.time === 'number') return a.time - b.time
    return String(a.time).localeCompare(String(b.time))
  })

  return markers
}
