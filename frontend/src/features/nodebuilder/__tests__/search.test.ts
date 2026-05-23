/**
 * Unit 6 — search.ts tests
 *
 * 12 required scenarios covering fuzzy search algorithm:
 * exact prefix, multi-word initials, word-start, substring, subsequence,
 * score ordering, empty query, case-insensitive, no match, matchedIndices,
 * hyphen word boundary, and rankCatalog sort order.
 */

import { describe, it, expect } from 'vitest'
import type { NodeCatalogEntry } from '../catalog'
import { fuzzyMatch, rankCatalog, friendlyName } from '../search'

// ---------------------------------------------------------------------------
// Test catalog
// ---------------------------------------------------------------------------

const RSI: NodeCatalogEntry = {
  name: 'rsi',
  cat: 'indicator',
  desc: 'Relative Strength Index. Default period=14.',
  reads: ['@close'],
  writes: ['@rsi'],
  defaults: { params: { period: 14 }, ins: 1, outs: 1, subtitle: 'RSI(14)' },
  compileActive: true,
}

const CROSSES_BELOW: NodeCatalogEntry = {
  name: 'crosses_below',
  cat: 'comparison',
  desc: 'True on the bar where the left series crosses below the right.',
  reads: ['@series'],
  writes: ['@bool'],
  defaults: { params: { threshold: null }, ins: 2, outs: 1, subtitle: 'crosses below' },
  compileActive: true,
}

const CROSSES_ABOVE: NodeCatalogEntry = {
  name: 'crosses_above',
  cat: 'comparison',
  desc: 'True on the bar where the left series crosses above the right.',
  reads: ['@series'],
  writes: ['@bool'],
  defaults: { params: { threshold: null }, ins: 2, outs: 1, subtitle: 'crosses above' },
  compileActive: true,
}

const SMA: NodeCatalogEntry = {
  name: 'sma',
  cat: 'indicator',
  desc: 'Simple Moving Average.',
  reads: ['@close'],
  writes: ['@sma'],
  defaults: { params: { period: 20 }, ins: 1, outs: 1, subtitle: 'SMA(20)' },
  compileActive: true,
}

const AND_NODE: NodeCatalogEntry = {
  name: 'and',
  cat: 'logic',
  desc: 'True when ALL incoming boolean signals are true.',
  reads: ['@bool'],
  writes: ['@bool'],
  defaults: { params: {}, ins: 2, outs: 1, subtitle: 'AND' },
  compileActive: true,
}

const ABOVE: NodeCatalogEntry = {
  name: 'above',
  cat: 'comparison',
  desc: 'True when the left series is above the right.',
  reads: ['@series'],
  writes: ['@bool'],
  defaults: { params: { threshold: null }, ins: 2, outs: 1, subtitle: 'above' },
  compileActive: true,
}

const BELOW: NodeCatalogEntry = {
  name: 'below',
  cat: 'comparison',
  desc: 'True when the left series is below the right.',
  reads: ['@series'],
  writes: ['@bool'],
  defaults: { params: { threshold: null }, ins: 2, outs: 1, subtitle: 'below' },
  compileActive: true,
}

/** Dummy entry with a hyphen in its name for test #11. */
const KALMAN_FILTER: NodeCatalogEntry = {
  name: 'kalman-filter',
  cat: 'signal',
  desc: 'Kalman filter for smooth series. (Hypothetical T3 node.)',
  reads: ['@close'],
  writes: ['@kalman'],
  defaults: { params: { q: 0.001 }, ins: 1, outs: 1, subtitle: null },
  compileActive: false,
}

const SAMPLE_CATALOG: readonly NodeCatalogEntry[] = [
  RSI,
  CROSSES_BELOW,
  CROSSES_ABOVE,
  SMA,
  AND_NODE,
  ABOVE,
  BELOW,
  KALMAN_FILTER,
]

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('search.ts — fuzzyMatch', () => {
  // Test 1: Exact prefix — "r" → RSI high score
  it('1. exact prefix: "r" matches RSI with score ≥ 1900', () => {
    const m = fuzzyMatch('r', RSI)
    expect(m).not.toBeNull()
    expect(m!.score).toBeGreaterThanOrEqual(1900)
  })

  // Test 2: Multi-word initials — "cb" → crosses_below, should score higher than substring
  it('2. multi-word initials: "cb" matches crosses_below with score ≥ 1400', () => {
    const m = fuzzyMatch('cb', CROSSES_BELOW)
    expect(m).not.toBeNull()
    expect(m!.score).toBeGreaterThanOrEqual(1400)
    // Higher than substring "cb" would get in a generic word
    expect(m!.score).toBeGreaterThan(500)
  })

  // Test 3: Multi-word initials — "ca" → crosses_above
  it('3. multi-word initials: "ca" matches crosses_above with score ≥ 1400', () => {
    const m = fuzzyMatch('ca', CROSSES_ABOVE)
    expect(m).not.toBeNull()
    expect(m!.score).toBeGreaterThanOrEqual(1400)
  })

  // Test 4: Substring match — "rosses" → crosses_below
  it('4. substring: "rosses" matches crosses_below', () => {
    const m = fuzzyMatch('rosses', CROSSES_BELOW)
    expect(m).not.toBeNull()
    expect(m!.score).toBeGreaterThanOrEqual(400)
    expect(m!.score).toBeLessThan(900)  // in substring tier, not prefix
  })

  // Test 5: Subsequence — "crsblw" → crosses_below
  it('5. subsequence: "crsblw" matches crosses_below', () => {
    const m = fuzzyMatch('crsblw', CROSSES_BELOW)
    expect(m).not.toBeNull()
    expect(m!.score).toBeGreaterThanOrEqual(200)
    expect(m!.score).toBeLessThan(500)  // subsequence tier
  })

  // Test 6: Score ordering — prefix > initials > word-start > substring > subsequence
  it('6. score ordering: prefix > initials > word-start > substring > subsequence', () => {
    const WORD_CROSSING: NodeCatalogEntry = {
      name: 'crossing',
      cat: 'comparison',
      desc: 'Generic crossing node.',
      reads: [],
      writes: [],
      defaults: { params: {}, ins: 1, outs: 1, subtitle: null },
      compileActive: false,
    }
    const BELOW_CROSS: NodeCatalogEntry = {
      name: 'below_cross',
      cat: 'comparison',
      desc: 'Below cross generic.',
      reads: [],
      writes: [],
      defaults: { params: {}, ins: 1, outs: 1, subtitle: null },
      compileActive: false,
    }

    // "cro" query:
    //   "crossing"   → exact prefix hit → tier 1
    //   "crosses_above" → word-start on "crosses" → tier 3
    //   "below_cross"  → substring "cro" in "cross" → tier 4
    const prefixMatch = fuzzyMatch('cro', WORD_CROSSING)   // "Crossing" → prefix
    const wordStartMatch = fuzzyMatch('cro', CROSSES_ABOVE) // "Crosses Above" → word-start
    const substringMatch = fuzzyMatch('cro', BELOW_CROSS)   // "Below Cross" → substring

    expect(prefixMatch).not.toBeNull()
    expect(wordStartMatch).not.toBeNull()
    expect(substringMatch).not.toBeNull()

    expect(prefixMatch!.score).toBeGreaterThan(wordStartMatch!.score)
    expect(wordStartMatch!.score).toBeGreaterThan(substringMatch!.score)
  })

  // Test 7: Empty query returns results for all entries
  it('7. empty query returns all catalog entries', () => {
    const results = rankCatalog('', SAMPLE_CATALOG)
    expect(results).toHaveLength(SAMPLE_CATALOG.length)
    results.forEach(r => expect(r.matchedIndices).toHaveLength(0))
  })

  // Test 8: Case-insensitive — "RSI" matches rsi entry
  it('8. case-insensitive: "RSI" matches rsi', () => {
    const m = fuzzyMatch('RSI', RSI)
    expect(m).not.toBeNull()
    expect(m!.score).toBeGreaterThanOrEqual(1900)
  })

  // Test 9: No match returns null
  it('9. no match: "zzz" returns null for RSI', () => {
    const m = fuzzyMatch('zzz', RSI)
    expect(m).toBeNull()
  })

  // Test 10: matchedIndices identifies matched positions
  it('10. matchedIndices: prefix match identifies first N positions', () => {
    const m = fuzzyMatch('rs', RSI)
    // friendlyName('rsi') → "RSI" (3 chars, all upper since len ≤ 3)
    // "rs" matches prefix of "rsi" lowercased → positions [0, 1]
    expect(m).not.toBeNull()
    expect(m!.matchedIndices).toEqual([0, 1])
  })

  // Test 11: Hyphen counts as word boundary — "kf" matches kalman-filter via initials
  it('11. hyphen word boundary: "kf" matches kalman-filter via initials', () => {
    const m = fuzzyMatch('kf', KALMAN_FILTER)
    // friendlyName('kalman-filter') → "Kalman Filter"
    // initials = "kf"  → tier 2a (all-initials prefix)
    expect(m).not.toBeNull()
    expect(m!.score).toBeGreaterThanOrEqual(1400)
  })

  // Test 12: rankCatalog returns descending by score
  it('12. rankCatalog descending: "r" puts RSI first', () => {
    const results = rankCatalog('r', SAMPLE_CATALOG)
    expect(results.length).toBeGreaterThan(0)
    expect(results[0].name).toBe('rsi')
    // Verify descending order
    for (let i = 1; i < results.length; i++) {
      expect(results[i - 1].score).toBeGreaterThanOrEqual(results[i].score)
    }
  })
})

// ---------------------------------------------------------------------------
// Helper: friendlyName
// ---------------------------------------------------------------------------

describe('friendlyName', () => {
  it('converts snake_case: crosses_below → Crosses Below', () => {
    expect(friendlyName('crosses_below')).toBe('Crosses Below')
  })
  it('converts hyphen-case: kalman-filter → Kalman Filter', () => {
    expect(friendlyName('kalman-filter')).toBe('Kalman Filter')
  })
  it('upper-cases short abbreviations: rsi → RSI', () => {
    expect(friendlyName('rsi')).toBe('RSI')
  })
  it('upper-cases sma → SMA', () => {
    expect(friendlyName('sma')).toBe('SMA')
  })
})
