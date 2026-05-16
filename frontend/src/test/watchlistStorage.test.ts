/**
 * Unit tests for watchlistStorage (F247b).
 *
 * Covers:
 *   - migrateLegacy: flat string[] → new schema
 *   - loadWatchlist: corrupt JSON → empty + wasCorrupt
 *   - loadWatchlist: new-format JSON → round-trips unchanged
 *   - dropDuplicate: deduplication with target-wins semantics
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  migrateLegacy,
  loadWatchlist,
  saveWatchlist,
  dropDuplicate,
  emptyState,
} from '../features/watchlist/watchlistStorage'
import type { WatchlistState } from '../features/watchlist/watchlistStorage'

// ---------------------------------------------------------------------------
// localStorage is provided by the setup polyfill — clear between tests
// ---------------------------------------------------------------------------
beforeEach(() => {
  localStorage.clear()
})

// ---------------------------------------------------------------------------
// migrateLegacy
// ---------------------------------------------------------------------------
describe('migrateLegacy', () => {
  it('converts flat string[] to { groups: [], ungrouped }', () => {
    const result = migrateLegacy(['AAPL', 'MSFT'])
    expect(result).toEqual({ groups: [], ungrouped: ['AAPL', 'MSFT'] })
  })

  it('uppercases symbols during migration', () => {
    const result = migrateLegacy(['aapl', 'msft'])
    expect(result.ungrouped).toEqual(['AAPL', 'MSFT'])
  })

  it('empty array produces empty state', () => {
    const result = migrateLegacy([])
    expect(result).toEqual({ groups: [], ungrouped: [] })
  })
})

// ---------------------------------------------------------------------------
// loadWatchlist — corrupt JSON
// ---------------------------------------------------------------------------
describe('loadWatchlist — corrupt JSON', () => {
  it('returns empty state and wasCorrupt=true for invalid JSON', () => {
    localStorage.setItem('watchlist-symbols', '{not valid json')
    const { state, wasCorrupt } = loadWatchlist()
    expect(wasCorrupt).toBe(true)
    expect(state).toEqual(emptyState())
  })

  it('returns empty state and wasCorrupt=true for unrecognizable object', () => {
    localStorage.setItem('watchlist-symbols', JSON.stringify({ foo: 'bar' }))
    const { state, wasCorrupt } = loadWatchlist()
    expect(wasCorrupt).toBe(true)
    expect(state).toEqual(emptyState())
  })

  it('returns empty state and wasCorrupt=true for array of non-strings', () => {
    localStorage.setItem('watchlist-symbols', JSON.stringify([1, 2, 3]))
    const { state, wasCorrupt } = loadWatchlist()
    expect(wasCorrupt).toBe(true)
    expect(state).toEqual(emptyState())
  })

  it('returns empty state and wasCorrupt=false when localStorage is empty', () => {
    const { state, wasCorrupt } = loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state).toEqual(emptyState())
  })
})

// ---------------------------------------------------------------------------
// loadWatchlist — legacy migration
// ---------------------------------------------------------------------------
describe('loadWatchlist — legacy migration', () => {
  it('migrates flat string[] silently (wasCorrupt=false)', () => {
    localStorage.setItem('watchlist-symbols', JSON.stringify(['AAPL', 'TSLA']))
    const { state, wasCorrupt } = loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state.groups).toEqual([])
    expect(state.ungrouped).toEqual(['AAPL', 'TSLA'])
  })

  it('persists the migrated shape to localStorage', () => {
    localStorage.setItem('watchlist-symbols', JSON.stringify(['GOOG']))
    loadWatchlist()
    // Second load should now read the new format
    const raw = localStorage.getItem('watchlist-symbols')!
    const parsed = JSON.parse(raw)
    expect(parsed).toHaveProperty('groups')
    expect(parsed).toHaveProperty('ungrouped')
    expect(parsed.ungrouped).toContain('GOOG')
  })
})

// ---------------------------------------------------------------------------
// loadWatchlist — new-format round-trip
// ---------------------------------------------------------------------------
describe('loadWatchlist — new-format round-trip', () => {
  it('round-trips a valid state object unchanged', () => {
    const original: WatchlistState = {
      groups: [
        { id: 'abc', name: 'Tech', tickers: ['AAPL', 'MSFT'], collapsed: false },
      ],
      ungrouped: ['SPY'],
    }
    saveWatchlist(original)
    const { state, wasCorrupt } = loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state.ungrouped).toEqual(['SPY'])
    expect(state.groups).toHaveLength(1)
    expect(state.groups[0].name).toBe('Tech')
    expect(state.groups[0].tickers).toEqual(['AAPL', 'MSFT'])
    expect(state.groups[0].collapsed).toBe(false)
  })

  it('persists collapsed:true and restores it', () => {
    const original: WatchlistState = {
      groups: [{ id: 'g1', name: 'Energy', tickers: ['XOM'], collapsed: true }],
      ungrouped: [],
    }
    saveWatchlist(original)
    const { state } = loadWatchlist()
    expect(state.groups[0].collapsed).toBe(true)
  })

  it('strips malformed group entries, keeps valid ones', () => {
    const raw = {
      groups: [
        { id: 'g1', name: 'Good', tickers: ['AAPL'], collapsed: false },
        { badGroup: true }, // malformed — no id/name/tickers/collapsed
      ],
      ungrouped: ['SPY'],
    }
    localStorage.setItem('watchlist-symbols', JSON.stringify(raw))
    const { state, wasCorrupt } = loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state.groups).toHaveLength(1)
    expect(state.groups[0].name).toBe('Good')
    expect(state.ungrouped).toEqual(['SPY'])
  })
})

// ---------------------------------------------------------------------------
// dropDuplicate
// ---------------------------------------------------------------------------
describe('dropDuplicate — deduplication', () => {
  it('no duplicates: returns state unchanged (deep equality)', () => {
    const state: WatchlistState = {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL'], collapsed: false }],
      ungrouped: ['SPY'],
    }
    const result = dropDuplicate(state)
    expect(result.ungrouped).toEqual(['SPY'])
    expect(result.groups[0].tickers).toEqual(['AAPL'])
  })

  it('duplicate in ungrouped + group: without target arg, first occurrence wins (ungrouped first)', () => {
    // AAPL appears in ungrouped (first) and in g1
    const state: WatchlistState = {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL', 'MSFT'], collapsed: false }],
      ungrouped: ['AAPL', 'SPY'],
    }
    const result = dropDuplicate(state)
    // ungrouped AAPL survives; group AAPL is removed
    expect(result.ungrouped).toContain('AAPL')
    expect(result.groups[0].tickers).not.toContain('AAPL')
    expect(result.groups[0].tickers).toContain('MSFT')
  })

  it('target group wins: dup in ungrouped removed, kept in group', () => {
    const state: WatchlistState = {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL'], collapsed: false }],
      ungrouped: ['AAPL', 'SPY'],
    }
    // Winner = group g1
    const result = dropDuplicate(state, 'AAPL', 'g1')
    expect(result.ungrouped).not.toContain('AAPL')
    expect(result.groups[0].tickers).toContain('AAPL')
    expect(result.ungrouped).toContain('SPY')
  })

  it('target ungrouped wins: dup in group removed, kept in ungrouped', () => {
    const state: WatchlistState = {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL', 'MSFT'], collapsed: false }],
      ungrouped: ['AAPL'],
    }
    // Winner = ungrouped (null)
    const result = dropDuplicate(state, 'AAPL', null)
    expect(result.ungrouped).toContain('AAPL')
    expect(result.groups[0].tickers).not.toContain('AAPL')
    expect(result.groups[0].tickers).toContain('MSFT')
  })

  it('case-insensitive dedup: aapl and AAPL treated as same', () => {
    const state: WatchlistState = {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL'], collapsed: false }],
      ungrouped: ['aapl'],
    }
    const result = dropDuplicate(state)
    const total = result.ungrouped.length + result.groups[0].tickers.length
    expect(total).toBe(1)
  })

  it('non-dup symbols preserved across multiple groups', () => {
    const state: WatchlistState = {
      groups: [
        { id: 'g1', name: 'A', tickers: ['AAPL', 'MSFT'], collapsed: false },
        { id: 'g2', name: 'B', tickers: ['TSLA', 'GOOG'], collapsed: false },
      ],
      ungrouped: ['SPY'],
    }
    const result = dropDuplicate(state)
    expect(result.ungrouped).toEqual(['SPY'])
    expect(result.groups[0].tickers).toEqual(['AAPL', 'MSFT'])
    expect(result.groups[1].tickers).toEqual(['TSLA', 'GOOG'])
  })
})
