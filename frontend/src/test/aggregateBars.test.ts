/**
 * Unit tests for aggregateBars() and aggregateLineSeries() — A8-render-resample.
 * Covers plan.md §5: normal bucket, odd remainder, single bar, empty,
 * daily passthrough, whitespace bucket, mixed null bucket, monotonicity,
 * volume-optional, and parity vs last-in-bucket semantics.
 */
import { describe, it, expect } from 'vitest'
import { aggregateBars, aggregateLineSeries } from '../features/chart/chartUtils'

// Helper: build OHLCV bars with given timestamps (already ET-shifted numeric seconds)
function bar(time: number, open: number, high: number, low: number, close: number, volume?: number) {
  return volume !== undefined ? { time, open, high, low, close, volume } : { time, open, high, low, close }
}

describe('aggregateBars — normal 5m→15m (3 bars → 1 bucket)', () => {
  // 3 consecutive 5m bars whose timestamps all fall in the same 15m bucket
  // bucket floor = ts - ts % 900
  const bucketSecs = 900 // 15m
  const t0 = 900  // 900 - 900%900 = 900 → bucket 900
  const t1 = 1200 // 1200 - 1200%900 = 1200 - 300 = 900 → same bucket
  const t2 = 1500 // 1500 - 1500%900 = 1500 - 600 = 900 → same bucket

  const bars = [
    bar(t0, 100, 110, 95, 105, 1000),
    bar(t1, 105, 115, 100, 108, 1500),
    bar(t2, 108, 120, 102, 112, 2000),
  ]

  const result = aggregateBars(bars, bucketSecs)

  it('produces exactly 1 bucket', () => {
    expect(result).toHaveLength(1)
  })

  it('open = first bar open', () => {
    expect(result[0].open).toBe(100)
  })

  it('high = max across bars', () => {
    expect(result[0].high).toBe(120)
  })

  it('low = min across bars', () => {
    expect(result[0].low).toBe(95)
  })

  it('close = last bar close', () => {
    expect(result[0].close).toBe(112)
  })

  it('volume = sum of all volumes', () => {
    expect(result[0].volume).toBe(4500)
  })

  it('time = bucket floor', () => {
    expect(result[0].time).toBe(900)
  })
})

describe('aggregateBars — odd remainder bucket', () => {
  // 7 bars with 3-bar buckets (bucketSecs = 300, bars at 0,300,600,900,1200,1500,1800)
  // → bucket 0 (bars 0,300,600), bucket 900 (bars 900,1200,1500), bucket 1800 (bar 1800 alone)
  const bucketSecs = 900
  const bars = [
    bar(0,   10, 12, 9,  11, 100),
    bar(300, 11, 13, 10, 12, 100),
    bar(600, 12, 14, 11, 13, 100),
    bar(900, 13, 15, 12, 14, 100),
    bar(1200,14, 16, 13, 15, 100),
    bar(1500,15, 17, 14, 16, 100),
    bar(1800,16, 18, 15, 17, 100),
  ]

  const result = aggregateBars(bars, bucketSecs)

  it('produces 3 buckets (2 full + 1 remainder)', () => {
    expect(result).toHaveLength(3)
  })

  it('remainder bucket (single bar) is included, not dropped', () => {
    expect(result[2].time).toBe(1800)
    expect(result[2].open).toBe(16)
    expect(result[2].close).toBe(17)
  })
})

describe('aggregateBars — single bar', () => {
  it('returns 1-element array identical to input', () => {
    const input = [bar(600, 50, 55, 48, 52, 200)]
    const result = aggregateBars(input, 300)
    expect(result).toHaveLength(1)
    expect(result[0].open).toBe(50)
    expect(result[0].close).toBe(52)
    expect(result[0].volume).toBe(200)
  })
})

describe('aggregateBars — empty array', () => {
  it('returns empty array', () => {
    expect(aggregateBars([], 300)).toEqual([])
  })
})

describe('aggregateBars — daily passthrough (string timestamps)', () => {
  it('returns input unchanged when timestamps are date strings', () => {
    const input = [
      { time: '2024-01-02', open: 100, high: 110, low: 95, close: 105 },
      { time: '2024-01-03', open: 105, high: 115, low: 100, close: 108 },
    ]
    const result = aggregateBars(input as any, 86400)
    expect(result).toBe(input)
  })
})

describe('aggregateBars — volume optional', () => {
  it('bars without volume key produce output without volume key', () => {
    const bars = [
      bar(0, 10, 12, 9, 11),
      bar(300, 11, 13, 10, 12),
    ]
    const result = aggregateBars(bars, 900)
    expect(result[0].volume).toBeUndefined()
  })
})

describe('aggregateBars — monotonicity', () => {
  it('output timestamps are strictly increasing (no duplicates)', () => {
    const bucketSecs = 300
    const bars = [
      bar(0,   1, 2, 0, 1, 10),
      bar(60,  1, 2, 0, 1, 10),
      bar(120, 1, 2, 0, 1, 10),
      bar(300, 2, 3, 1, 2, 10),
      bar(360, 2, 3, 1, 2, 10),
    ]
    const result = aggregateBars(bars, bucketSecs)
    for (let i = 1; i < result.length; i++) {
      expect(result[i].time as number).toBeGreaterThan(result[i - 1].time as number)
    }
  })
})

// ─── aggregateLineSeries tests ──────────────────────────────────────────────

describe('aggregateLineSeries — whitespace handling', () => {
  const bucketSecs = 900

  it('bucket of all nulls (whitespace entries) emits whitespace entry', () => {
    const data = [
      { time: 0 },
      { time: 300 },
      { time: 600 },
    ]
    const result = aggregateLineSeries(data, bucketSecs)
    expect(result).toHaveLength(1)
    expect(result[0].time).toBe(0)
    expect(result[0].value).toBeUndefined()
  })

  it('bucket with mixed null + non-null: emits last non-null value', () => {
    const data = [
      { time: 0 },         // whitespace
      { time: 300, value: 50 },
      { time: 600, value: 55 },
    ]
    const result = aggregateLineSeries(data, bucketSecs)
    expect(result).toHaveLength(1)
    expect(result[0].value).toBe(55)
  })
})

describe('aggregateLineSeries — daily passthrough', () => {
  it('returns input unchanged for date-string timestamps', () => {
    const input = [
      { time: '2024-01-02', value: 100 },
      { time: '2024-01-03', value: 105 },
    ]
    const result = aggregateLineSeries(input as any, 86400)
    expect(result).toBe(input)
  })
})

describe('aggregateLineSeries — empty', () => {
  it('returns empty array', () => {
    expect(aggregateLineSeries([], 300)).toEqual([])
  })
})

describe('aggregateLineSeries — parity with last-in-bucket semantics', () => {
  it('matches downsampleEquity last-in-bucket behavior for non-null data', () => {
    // downsampleEquity: for each point, floor to bucket, overwrite same key → last wins
    const bucketSecs = 900
    const data = [
      { time: 0,   value: 10 },
      { time: 300, value: 20 },
      { time: 600, value: 30 },
      { time: 900, value: 40 },
    ]
    const result = aggregateLineSeries(data, bucketSecs)
    // Bucket 0: covers t=0,300,600 → last = 30
    // Bucket 900: covers t=900 → last = 40
    expect(result).toHaveLength(2)
    expect(result[0].value).toBe(30)
    expect(result[1].value).toBe(40)
  })
})

describe('aggregateLineSeries — monotonicity', () => {
  it('output timestamps are strictly increasing', () => {
    const bucketSecs = 300
    const data = [
      { time: 0, value: 1 },
      { time: 60, value: 2 },
      { time: 120, value: 3 },
      { time: 300, value: 4 },
      { time: 360, value: 5 },
    ]
    const result = aggregateLineSeries(data, bucketSecs)
    for (let i = 1; i < result.length; i++) {
      expect(result[i].time as number).toBeGreaterThan(result[i - 1].time as number)
    }
  })
})
