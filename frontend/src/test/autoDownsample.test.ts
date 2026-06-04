/**
 * Auto-downsampling ladder tests — A8-downsample.
 * Covers coarserIntervalFor() ladder, tier escalation, null at top, daily/unknown passthrough,
 * the AUTOS_ON/AUTOS_OFF constants, calcVisibleBaseBars (D3 normalization),
 * and evaluateAutoInterval (pure synchronous helper; Chart.tsx always passes pendingSince=null).
 */
import { describe, it, expect } from 'vitest'
import {
  coarserIntervalFor,
  calcVisibleBaseBars,
  evaluateAutoInterval,
  AUTOS_ON_BARS,
  AUTOS_OFF_BARS,
  INTERVAL_SECS,
} from '../shared/utils/intervals'
import { aggregateBars, snapTimestamp } from '../features/chart/chartUtils'

describe('AUTOS constants', () => {
  it('AUTOS_OFF_BARS is less than AUTOS_ON_BARS (hysteresis invariant)', () => {
    expect(AUTOS_OFF_BARS).toBeLessThan(AUTOS_ON_BARS)
  })
})

describe('INTERVAL_SECS exported map', () => {
  it('includes 2m entry', () => {
    expect(INTERVAL_SECS['2m']).toBe(120)
  })

  it('1h and 60m are the same seconds', () => {
    expect(INTERVAL_SECS['1h']).toBe(INTERVAL_SECS['60m'])
  })
})

describe('coarserIntervalFor — null passthrough', () => {
  it('returns null for daily base interval (auto never engages on daily data)', () => {
    expect(coarserIntervalFor('1d', 10_000)).toBeNull()
  })

  it('returns null for weekly base interval', () => {
    expect(coarserIntervalFor('1wk', 10_000)).toBeNull()
  })

  it('returns null for unknown base interval', () => {
    expect(coarserIntervalFor('unknown', 10_000)).toBeNull()
  })

  it('returns null for empty base string', () => {
    expect(coarserIntervalFor('', 10_000)).toBeNull()
  })
})

describe('coarserIntervalFor — 1m base', () => {
  it('returns 5m when visible bars are just above ON threshold', () => {
    // 8001 base-equiv bars → tier1 = 5m, bars after switch = 8001/5 = ~1600 (< ON) → tier1
    expect(coarserIntervalFor('1m', 8_001)).toBe('5m')
  })

  it('returns 15m (tier 2) when tier1 would still exceed AUTOS_ON_BARS', () => {
    // 80_000 base bars → after 1m→5m: 80000/5 = 16000 > 8000 → escalate to tier2 = 15m
    expect(coarserIntervalFor('1m', 80_000)).toBe('15m')
  })

  it('returns 5m at exactly ON threshold boundary', () => {
    // 8000 bars → after 1m→5m: 8000/5 = 1600 (not > ON) → tier1
    expect(coarserIntervalFor('1m', 8_000)).toBe('5m')
  })

  it('escalation boundary: 40001 base bars escalates to tier2 (15m)', () => {
    // barsAfterTier1 = 40001/5 = 8000.2 > 8000 → tier2 = 15m
    expect(coarserIntervalFor('1m', 40_001)).toBe('15m')
  })

  it('escalation boundary: 40000 base bars stays at tier1 (5m)', () => {
    // barsAfterTier1 = 40000/5 = 8000 — NOT strictly > 8000 → tier1 = 5m
    expect(coarserIntervalFor('1m', 40_000)).toBe('5m')
  })
})

describe('coarserIntervalFor — 2m base (TS-04)', () => {
  it('returns 5m for visible bars just above ON threshold', () => {
    // 2m→5m: ratio = 300/120 = 2.5; barsAfterTier1 = 9000/2.5 = 3600 (< ON) → tier1
    expect(coarserIntervalFor('2m', 9_000)).toBe('5m')
  })

  it('returns 15m (tier 2) for very large span', () => {
    // 2m→5m: 40_000/2.5 = 16000 > 8000 → tier2 = 15m
    expect(coarserIntervalFor('2m', 40_000)).toBe('15m')
  })
})

describe('coarserIntervalFor — 5m base', () => {
  it('returns 15m for visible bars just above ON threshold', () => {
    // 9000 base bars → after 5m→15m: 9000/3 = 3000 (< ON) → tier1 = 15m
    expect(coarserIntervalFor('5m', 9_000)).toBe('15m')
  })

  it('returns 1h (tier 2) when tier1 would still exceed AUTOS_ON_BARS', () => {
    // 100_000 base bars → after 5m→15m: 100000/3 ≈ 33333 > 8000 → tier2 = 1h
    expect(coarserIntervalFor('5m', 100_000)).toBe('1h')
  })
})

describe('coarserIntervalFor — 15m base', () => {
  it('returns 1h for moderate visible bars', () => {
    // 10000 base bars → after 15m→1h: 10000/4 = 2500 (< ON) → tier1 = 1h
    expect(coarserIntervalFor('15m', 10_000)).toBe('1h')
  })

  it('returns 1d (tier 2) for very large span', () => {
    // 200_000 base bars → after 15m→1h: 200000/4 = 50000 > 8000 → tier2 = 1d
    expect(coarserIntervalFor('15m', 200_000)).toBe('1d')
  })
})

describe('coarserIntervalFor — 30m base', () => {
  it('returns 1h for moderate visible bars', () => {
    // 9000 → after 30m→1h: 9000/2 = 4500 (< ON) → tier1 = 1h
    expect(coarserIntervalFor('30m', 9_000)).toBe('1h')
  })

  it('returns 1d (tier 2) for large span', () => {
    // 30000 → after 30m→1h: 30000/2 = 15000 > 8000 → tier2 = 1d
    expect(coarserIntervalFor('30m', 30_000)).toBe('1d')
  })
})

describe('coarserIntervalFor — 1h base', () => {
  it('returns 1d for any visible bar count (only one tier)', () => {
    expect(coarserIntervalFor('1h', 9_000)).toBe('1d')
  })

  it('returns 1d even for very large span (no tier 2 for 1h)', () => {
    // tier2 is null for 1h → always returns tier1 = 1d regardless of span
    expect(coarserIntervalFor('1h', 500_000)).toBe('1d')
  })
})

describe('coarserIntervalFor — 60m alias', () => {
  it('treats 60m as equivalent to 1h and returns 1d', () => {
    expect(coarserIntervalFor('60m', 9_000)).toBe('1d')
  })
})

// ─── calcVisibleBaseBars (D3 normalization) ───────────────────────────────────

describe('calcVisibleBaseBars — D3 normalization formula', () => {
  it('at base interval: ratio=1, base-equiv bars equals raw span', () => {
    // 5m view on 5m base: viewSecs/baseSecs = 1 → no scaling
    expect(calcVisibleBaseBars(1000, '5m', '5m')).toBe(1000)
  })

  it('at coarser view (5m base, 15m view): raw span scales UP by 3', () => {
    // After 5m→15m switch: rawSpan=333 (fewer bars), but base-equiv = 333 * (900/300) = 999 ≈ 1000
    // Critical invariant: the same time window should produce ~same base-equiv bars after a switch.
    // viewSecs/baseSecs = 900/300 = 3 → 333 * 3 = 999 (≈1000, delta = rounding only)
    expect(calcVisibleBaseBars(333, '15m', '5m')).toBeCloseTo(999)
  })

  it('at coarser view (1m base, 5m view): raw span scales UP by 5', () => {
    // viewSecs/baseSecs = 300/60 = 5 → 1000 * 5 = 5000
    expect(calcVisibleBaseBars(1000, '5m', '1m')).toBe(5000)
  })

  it('inverted ratio regression: rawSpan / (viewSecs/baseSecs) would be WRONG', () => {
    // If someone accidentally inverted the formula to rawSpan / ratio,
    // calcVisibleBaseBars(1000, '15m', '5m') would return 1000/3 ≈ 333 (too few).
    // The correct result is 1000 * 3 = 3000.
    expect(calcVisibleBaseBars(1000, '15m', '5m')).toBe(3000)
    // And must NOT equal the inverted-formula result:
    expect(calcVisibleBaseBars(1000, '15m', '5m')).not.toBeCloseTo(333, 0)
  })

  it('unknown interval: falls back to rawSpan unchanged', () => {
    expect(calcVisibleBaseBars(500, 'unknown', '5m')).toBe(500)
    expect(calcVisibleBaseBars(500, '5m', 'unknown')).toBe(500)
  })
})

// ─── evaluateAutoInterval (pure synchronous helper) ────────────────────────────
// Note: pendingSince tests below exercise the pure helper's logic in isolation.
// Chart.tsx always passes pendingSince=null (R3: synchronous render, no async race).

describe('evaluateAutoInterval — coarsen path', () => {
  const now = Date.now()

  it('returns coarsen when visibleBaseBars > ON and at base interval', () => {
    const result = evaluateAutoInterval({
      visibleBaseBars: 10_000,
      viewInterval: '5m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('coarsen')
    if (result.action === 'coarsen') expect(result.target).toBe('15m')
  })

  it('FIX-C: re-escalation — coarsens further when autoActive and still above ON', () => {
    // Currently at 15m (auto-chosen from 5m base), user zoomed out more (visibleBaseBars very large)
    const result = evaluateAutoInterval({
      visibleBaseBars: 100_000,
      viewInterval: '15m',
      baseInterval: '5m',
      autoActive: true,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('coarsen')
    if (result.action === 'coarsen') expect(result.target).toBe('1h')
  })

  it('FIX-C: does NOT coarsen when autoActive but already at the right target', () => {
    // visibleBaseBars = 9000 on 5m base → coarserIntervalFor → 15m, which is already viewInterval
    const result = evaluateAutoInterval({
      visibleBaseBars: 9_000,
      viewInterval: '15m',
      baseInterval: '5m',
      autoActive: true,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('none')
  })

  it('does NOT coarsen when user manually selected a coarser view (autoActive=false, viewInterval≠base)', () => {
    // User manually set viewInterval=15m; autoActive=false; should not re-coarsen.
    const result = evaluateAutoInterval({
      visibleBaseBars: 10_000,
      viewInterval: '15m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('none')
  })
})

describe('evaluateAutoInterval — restore path', () => {
  const now = Date.now()

  it('returns restore when below OFF and autoActive', () => {
    const result = evaluateAutoInterval({
      visibleBaseBars: 5_000,
      viewInterval: '15m',
      baseInterval: '5m',
      autoActive: true,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('restore')
    if (result.action === 'restore') expect(result.target).toBe('5m')
  })

  it('does NOT restore when below OFF but user manually set view (autoActive=false)', () => {
    const result = evaluateAutoInterval({
      visibleBaseBars: 5_000,
      viewInterval: '15m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('none')
  })
})

describe('evaluateAutoInterval — daily/unknown base passthrough', () => {
  const now = Date.now()

  it('returns none for daily base regardless of bar count', () => {
    expect(evaluateAutoInterval({
      visibleBaseBars: 50_000,
      viewInterval: '1d',
      baseInterval: '1d',
      autoActive: false,
      pendingSince: null,
      now,
    }).action).toBe('none')
  })

  it('returns none for unknown base', () => {
    expect(evaluateAutoInterval({
      visibleBaseBars: 50_000,
      viewInterval: 'unknown',
      baseInterval: 'unknown',
      autoActive: false,
      pendingSince: null,
      now,
    }).action).toBe('none')
  })
})

describe('evaluateAutoInterval — pending switch lifecycle', () => {
  const now = Date.now()

  it('suppresses further coarsen evaluation while a non-expired switch is pending', () => {
    const result = evaluateAutoInterval({
      visibleBaseBars: 20_000,
      viewInterval: '5m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: now - 100,   // 100ms ago — not expired
      now,
    })
    expect(result.action).toBe('none')
  })

  it('FIX-A expiry: treats pending switch older than 5s as expired and re-evaluates', () => {
    const result = evaluateAutoInterval({
      visibleBaseBars: 10_000,
      viewInterval: '5m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: now - 6000,  // 6s ago — expired
      now,
    })
    // After expiry, re-evaluates normally → should coarsen
    expect(result.action).toBe('coarsen')
  })

  it('RACE-01 reverse-zoom: pending switch is cancelled when user zooms back in', () => {
    const result = evaluateAutoInterval({
      visibleBaseBars: 4_000,   // below OFF threshold — user zoomed back in
      viewInterval: '5m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: now - 200,  // pending but not expired
      now,
    })
    expect(result.action).toBe('none')
  })
})

describe('evaluateAutoInterval — hysteresis no-thrash', () => {
  const now = Date.now()

  it('simulates a switch: after coarsen+restore cycle, does not re-coarsen at OFF threshold', () => {
    // After restoring to base, user is at 5999 bars (just below OFF) — should stay at 'none'.
    const result = evaluateAutoInterval({
      visibleBaseBars: 5_999,
      viewInterval: '5m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('none')
  })

  it('does not trigger in the dead zone between OFF and ON (6001 bars)', () => {
    const result = evaluateAutoInterval({
      visibleBaseBars: 6_001,
      viewInterval: '5m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('none')
  })

  it('triggers coarsen only above ON threshold (8001 bars)', () => {
    const result = evaluateAutoInterval({
      visibleBaseBars: 8_001,
      viewInterval: '5m',
      baseInterval: '5m',
      autoActive: false,
      pendingSince: null,
      now,
    })
    expect(result.action).toBe('coarsen')
  })
})

// ─── FIX-1: 1d render-tier snap invariant ──────────────────────────────────────
// Locks the invariant that snapTimestamp('1d') and aggregateBars('1d') produce
// numerically matching bucket keys so markers/regime/signals are not silently
// dropped when a 15m/30m/1h base is zoomed to the 1d auto render tier.

describe('1d snap invariant — aggregateBars bucket keys match snapTimestamp', () => {
  // Identity toET: passes numeric timestamps through unchanged (unit-test isolation).
  const identityToET = (t: any) => t

  it('aggregateBars to 1d buckets produces UTC-midnight numeric keys', () => {
    // Mon 2024-01-15 09:30 ET → Unix 1705325400 (numeric)
    const barTs = 1705325400 // intraday timestamp (ET-shifted or UTC; bucket math is the same)
    const bucketSecs = INTERVAL_SECS['1d'] // 86400
    const bars = [{ time: barTs, open: 100, high: 105, low: 99, close: 102, volume: 1000 }]
    const result = aggregateBars(bars, bucketSecs)
    expect(result).toHaveLength(1)
    // Bucket floor: ts - (ts % 86400)
    const expectedKey = barTs - (barTs % 86400)
    expect(result[0].time).toBe(expectedKey)
    expect(typeof result[0].time).toBe('number')
  })

  it('snapTimestamp("1d") floors to the same numeric bucket key as aggregateBars', () => {
    const barTs = 1705325400
    const bucketSecs = INTERVAL_SECS['1d']
    const expectedBucketKey = barTs - (barTs % bucketSecs)

    // snapTimestamp with identityToET should produce the same floor
    const snapped = snapTimestamp(barTs, '1d', identityToET)
    expect(snapped).toBe(expectedBucketKey)
    expect(typeof snapped).toBe('number')
  })

  it('aggregateBars and snapTimestamp agree on the same bucket for multiple intraday bars', () => {
    // Three intraday bars on the same calendar day → one aggregated bar
    const ts1 = 1705325400 // 09:30
    const ts2 = 1705329000 // 10:30
    const ts3 = 1705332600 // 11:30
    const bucketSecs = INTERVAL_SECS['1d']
    const bars = [
      { time: ts1, open: 100, high: 105, low: 99, close: 102 },
      { time: ts2, open: 102, high: 106, low: 101, close: 104 },
      { time: ts3, open: 104, high: 108, low: 103, close: 107 },
    ]
    const aggregated = aggregateBars(bars, bucketSecs)
    expect(aggregated).toHaveLength(1)
    const aggKey = aggregated[0].time

    // All three source bars must snap to the same bucket key
    const snap1 = snapTimestamp(ts1, '1d', identityToET)
    const snap2 = snapTimestamp(ts2, '1d', identityToET)
    const snap3 = snapTimestamp(ts3, '1d', identityToET)
    expect(snap1).toBe(aggKey)
    expect(snap2).toBe(aggKey)
    expect(snap3).toBe(aggKey)
  })

  it('before FIX-1: snapTimestamp("1d") would have returned a date string (regression guard)', () => {
    // If '1d' were missing from INTERVAL_SECONDS, snapTimestamp would fall through to the
    // date-string branch and return 'YYYY-MM-DD', mismatching the numeric aggregateBars key.
    // This test confirms the numeric path is taken.
    const snapped = snapTimestamp(1705325400, '1d', identityToET)
    expect(typeof snapped).toBe('number')
    // Must NOT be a date string
    expect(typeof snapped).not.toBe('string')
  })
})
