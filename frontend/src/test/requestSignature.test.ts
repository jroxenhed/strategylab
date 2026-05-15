/**
 * Unit tests for cacheWriteKey + requestSignature (F189).
 *
 * Key invariant: cacheWriteKey strips a full StrategyRequest to exactly the
 * 17 cache-key fields, and requestSignature(cacheWriteKey(req)) ===
 * requestSignature(req) — so write-side and read-side always compare the same shape.
 */
import { describe, it, expect } from 'vitest'
import { cacheWriteKey, requestSignature } from '../shared/types/requestSignature'
import type { StrategyRequest } from '../shared/types/strategy'

const BASE: StrategyRequest = {
  ticker: 'AAPL',
  start: '2024-01-01',
  end: '2024-12-31',
  interval: '1d',
  buy_rules: [],
  sell_rules: [],
  buy_logic: 'AND',
  sell_logic: 'AND',
  initial_capital: 10000,
  position_size: 1,
  source: 'yahoo',
}

describe('cacheWriteKey', () => {
  it('strips non-key fields (initial_capital, position_size, source)', () => {
    const key = cacheWriteKey(BASE)
    expect('initial_capital' in key).toBe(false)
    expect('position_size' in key).toBe(false)
    expect('source' in key).toBe(false)
  })

  it('includes all 17 cache-key fields', () => {
    const key = cacheWriteKey(BASE)
    const expectedFields = [
      'ticker', 'start', 'end', 'interval',
      'buy_rules', 'sell_rules', 'buy_logic', 'sell_logic',
      'long_buy_rules', 'long_sell_rules', 'long_buy_logic', 'long_sell_logic',
      'short_buy_rules', 'short_sell_rules', 'short_buy_logic', 'short_sell_logic',
      'regime',
    ]
    for (const f of expectedFields) {
      expect(f in key).toBe(true)
    }
  })

  it('coerces absent optional fields to null', () => {
    const key = cacheWriteKey(BASE)
    expect(key.long_buy_rules).toBeNull()
    expect(key.regime).toBeNull()
  })
})

describe('requestSignature / write-read parity', () => {
  it('signature reads exactly the cacheWriteKey projection', () => {
    // Structural invariant: requestSignature == JSON.stringify(cacheWriteKey(req)).
    // The write side persists the FULL request (so downstream consumers re-send
    // every field to the backend), but the read predicate must only compare the
    // 17 cache-key fields — that's exactly what cacheWriteKey projects.
    expect(requestSignature(BASE)).toBe(JSON.stringify(cacheWriteKey(BASE)))
  })

  it('different tickers produce different signatures', () => {
    const a = { ...BASE, ticker: 'AAPL' }
    const b = { ...BASE, ticker: 'TSLA' }
    expect(requestSignature(a)).not.toBe(requestSignature(b))
  })

  it('non-key fields do not affect the signature', () => {
    const a = { ...BASE, initial_capital: 1000 }
    const b = { ...BASE, initial_capital: 99999 }
    expect(requestSignature(a)).toBe(requestSignature(b))
  })

  it('adding a field to buy_rules changes the signature', () => {
    const withRule: StrategyRequest = {
      ...BASE,
      buy_rules: [{ indicator: 'rsi', condition: 'below', value: 30, muted: false, negated: false }],
    }
    expect(requestSignature(withRule)).not.toBe(requestSignature(BASE))
  })
})
