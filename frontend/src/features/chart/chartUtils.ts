/** Lightweight-charts compatible time: numeric unix seconds (intraday) or YYYY-MM-DD string (daily+). */
export type ChartTime = number | string

/** OHLCV bar with a ChartTime-typed timestamp. */
export interface OHLCVChartBar {
  time: ChartTime
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

/** Line series point with a ChartTime-typed timestamp. */
export interface LinePoint {
  time: ChartTime
  value?: number
}

export function toLineData(arr: { time: string; value: number | null }[], toET: (t: ChartTime) => ChartTime): LinePoint[] {
  return arr.map(d => d.value !== null
    ? { time: toET(d.time) as ChartTime, value: d.value as number }
    : { time: toET(d.time) as ChartTime }
  )
}

const INTERVAL_SECONDS: Record<string, number> = {
  '1m': 60, '2m': 120, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '60m': 3600, '1d': 86400,
}

/**
 * Aggregate already-toET-shifted OHLCV bars to a coarser bucket size.
 * Bucket floor: ts - (ts % bucketSecs). Input must be toET-shifted (numeric timestamps).
 * Daily data (string timestamps) passes through unchanged.
 * OHLC rule: first open, max high, min low, last close; sum volume.
 */
export function aggregateBars(
  bars: OHLCVChartBar[],
  bucketSecs: number,
): OHLCVChartBar[] {
  if (bars.length === 0) return []
  // Daily passthrough: string timestamps are not numeric, bucket math doesn't apply.
  if (typeof bars[0].time !== 'number') return bars
  if (bucketSecs <= 0) return bars

  const buckets = new Map<number, OHLCVChartBar>()
  const order: number[] = []

  for (const bar of bars) {
    const ts = bar.time as number
    const key = ts - (ts % bucketSecs)
    const existing = buckets.get(key)
    if (!existing) {
      const entry: OHLCVChartBar = {
        time: key,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      }
      if (bar.volume !== undefined) entry.volume = bar.volume
      buckets.set(key, entry)
      order.push(key)
    } else {
      if (bar.high > existing.high) existing.high = bar.high
      if (bar.low < existing.low) existing.low = bar.low
      existing.close = bar.close
      if (bar.volume !== undefined) {
        existing.volume = (existing.volume ?? 0) + bar.volume
      }
    }
  }

  return order.map(k => buckets.get(k)!)
}

/**
 * Aggregate already-toET-shifted line series data to a coarser bucket size.
 * Last non-null value in bucket is kept; whitespace if all null in bucket.
 * Daily data (string timestamps) passes through unchanged.
 */
export function aggregateLineSeries(
  data: LinePoint[],
  bucketSecs: number,
): LinePoint[] {
  if (data.length === 0) return []
  if (typeof data[0].time !== 'number') return data
  if (bucketSecs <= 0) return data

  // Map: key → last non-null value (or undefined for whitespace bucket)
  const buckets = new Map<number, LinePoint>()
  const order: number[] = []

  for (const d of data) {
    const ts = d.time as number
    const key = ts - (ts % bucketSecs)
    if (!buckets.has(key)) {
      buckets.set(key, { time: key })
      order.push(key)
    }
    // Keep last non-null value in bucket
    if (d.value !== undefined) {
      buckets.get(key)!.value = d.value
    }
  }

  return order.map(k => buckets.get(k)!)
}

export function snapTimestamp(
  ts: string | number,
  viewInterval: string,
  toET: (t: ChartTime) => ChartTime,
): string | number {
  if (typeof ts !== 'number') {
    return ts
  }
  const etTs = toET(ts) as number
  const secs = INTERVAL_SECONDS[viewInterval]
  if (secs) {
    return etTs - (etTs % secs)
  }
  // Daily+ intervals without an INTERVAL_SECONDS entry use "YYYY-MM-DD" string keys
  // in candleTimeIndex. Convert the ET-shifted unix timestamp to a date string.
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
}

const UP = '#26a641'
const DOWN = '#f85149'

export function aggregateMarkers(
  trades: TradeForSnap[],
  candleTimeIndex: Map<string | number, number>,
  viewInterval: string,
  backtestInterval: string,
  toET: (t: ChartTime) => ChartTime,
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

  const markers: Array<{
    time: string | number
    position: 'inBar' | 'aboveBar' | 'belowBar'
    color: string
    shape: 'circle' | 'arrowUp' | 'arrowDown'
    text?: string
  }> = []
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
          ...(subPane && { text: isShortEntry ? 'SH' : 'B' }),
        })
      } else {
        const win = (t.pnl ?? 0) >= 0
        markers.push({
          time,
          position: subPane ? 'inBar' as const : (isCover ? 'belowBar' as const : 'aboveBar' as const),
          color: win ? UP : DOWN,
          shape: subPane ? 'circle' as const : (isCover ? 'arrowUp' as const : 'arrowDown' as const),
          ...(subPane && { text: t.stop_loss ? 'SL' : t.trailing_stop ? 'TSL' : (isCover ? 'COV' : 'S') }),
        })
      }
    } else {
      const netPnl = group.reduce((sum, t) => sum + (t.pnl ?? 0), 0)
      markers.push({
        time,
        position: subPane ? 'inBar' as const : 'aboveBar' as const,
        color: netPnl >= 0 ? UP : DOWN,
        shape: subPane ? 'circle' as const : 'arrowDown' as const,
        ...(subPane ? { text: `${group.length}T` } : {}),
      })
    }
  }

  markers.sort((a, b) => {
    if (typeof a.time === 'number' && typeof b.time === 'number') return a.time - b.time
    return String(a.time).localeCompare(String(b.time))
  })

  return markers
}
