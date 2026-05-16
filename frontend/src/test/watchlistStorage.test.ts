/**
 * Unit tests for watchlistStorage (F247b / API migration).
 *
 * Covers:
 *   - migrateLegacy: flat string[] → new schema
 *   - loadWatchlist: API success path
 *   - loadWatchlist: API failure → localStorage fallback paths
 *   - loadWatchlist: corrupt JSON → empty + wasCorrupt
 *   - loadWatchlist: new-format JSON → round-trips unchanged
 *   - saveWatchlist: writes localStorage mirror + POSTs to API
 *   - dropDuplicate: deduplication with target-wins semantics
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
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
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// fetch mock helpers
// ---------------------------------------------------------------------------

function mockFetchOk(body: unknown) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(body),
  } as Response)
}

function mockFetchFail() {
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Network error'))
}

function mockFetchNotOk() {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: false,
    json: () => Promise.resolve({}),
  } as Response)
}

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
// loadWatchlist — API success path
// ---------------------------------------------------------------------------
describe('loadWatchlist — API success', () => {
  it('returns state from API response and mirrors to localStorage', async () => {
    const apiState: WatchlistState = { groups: [], ungrouped: ['AAPL', 'SPY'] }
    mockFetchOk(apiState)
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state.ungrouped).toEqual(['AAPL', 'SPY'])
    // mirror written to localStorage
    const mirror = JSON.parse(localStorage.getItem('watchlist-symbols')!)
    expect(mirror.ungrouped).toEqual(['AAPL', 'SPY'])
  })

  it('returns empty state when API returns unrecognisable shape (falls back to empty localStorage)', async () => {
    mockFetchOk({ random: 'junk' })
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state).toEqual(emptyState())
  })
})

// ---------------------------------------------------------------------------
// loadWatchlist — API failure → localStorage fallback
// ---------------------------------------------------------------------------
describe('loadWatchlist — API failure, localStorage fallback', () => {
  it('falls back to localStorage when fetch throws', async () => {
    mockFetchFail()
    const fallback: WatchlistState = { groups: [], ungrouped: ['MSFT'] }
    localStorage.setItem('watchlist-symbols', JSON.stringify(fallback))
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state.ungrouped).toEqual(['MSFT'])
  })

  it('falls back to localStorage when API returns non-ok status', async () => {
    mockFetchNotOk()
    const fallback: WatchlistState = { groups: [], ungrouped: ['TSLA'] }
    localStorage.setItem('watchlist-symbols', JSON.stringify(fallback))
    const { state } = await loadWatchlist()
    expect(state.ungrouped).toEqual(['TSLA'])
  })
})

// ---------------------------------------------------------------------------
// loadWatchlist — corrupt JSON (localStorage fallback path)
// ---------------------------------------------------------------------------
describe('loadWatchlist — corrupt JSON in localStorage fallback', () => {
  it('returns empty state and wasCorrupt=true for invalid JSON', async () => {
    mockFetchFail()
    localStorage.setItem('watchlist-symbols', '{not valid json')
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(true)
    expect(state).toEqual(emptyState())
  })

  it('returns empty state and wasCorrupt=true for unrecognizable object', async () => {
    mockFetchFail()
    localStorage.setItem('watchlist-symbols', JSON.stringify({ foo: 'bar' }))
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(true)
    expect(state).toEqual(emptyState())
  })

  it('returns empty state and wasCorrupt=true for array of non-strings', async () => {
    mockFetchFail()
    localStorage.setItem('watchlist-symbols', JSON.stringify([1, 2, 3]))
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(true)
    expect(state).toEqual(emptyState())
  })

  it('returns empty state and wasCorrupt=false when localStorage is also empty', async () => {
    mockFetchFail()
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state).toEqual(emptyState())
  })
})

// ---------------------------------------------------------------------------
// loadWatchlist — legacy migration (via localStorage fallback path)
// ---------------------------------------------------------------------------
describe('loadWatchlist — legacy migration', () => {
  it('migrates flat string[] silently (wasCorrupt=false)', async () => {
    mockFetchFail()
    localStorage.setItem('watchlist-symbols', JSON.stringify(['AAPL', 'TSLA']))
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state.groups).toEqual([])
    expect(state.ungrouped).toEqual(['AAPL', 'TSLA'])
  })
})

// ---------------------------------------------------------------------------
// loadWatchlist — new-format round-trip (via API path)
// ---------------------------------------------------------------------------
describe('loadWatchlist — new-format round-trip', () => {
  it('round-trips a valid state object unchanged via API', async () => {
    const original: WatchlistState = {
      groups: [
        { id: 'abc', name: 'Tech', tickers: ['AAPL', 'MSFT'], collapsed: false },
      ],
      ungrouped: ['SPY'],
    }
    mockFetchOk(original)
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state.ungrouped).toEqual(['SPY'])
    expect(state.groups).toHaveLength(1)
    expect(state.groups[0].name).toBe('Tech')
    expect(state.groups[0].tickers).toEqual(['AAPL', 'MSFT'])
    expect(state.groups[0].collapsed).toBe(false)
  })

  it('persists collapsed:true and restores it via API', async () => {
    const original: WatchlistState = {
      groups: [{ id: 'g1', name: 'Energy', tickers: ['XOM'], collapsed: true }],
      ungrouped: [],
    }
    mockFetchOk(original)
    const { state } = await loadWatchlist()
    expect(state.groups[0].collapsed).toBe(true)
  })

  it('strips malformed group entries, keeps valid ones (via localStorage fallback)', async () => {
    mockFetchFail()
    const raw = {
      groups: [
        { id: 'g1', name: 'Good', tickers: ['AAPL'], collapsed: false },
        { badGroup: true }, // malformed — no id/name/tickers/collapsed
      ],
      ungrouped: ['SPY'],
    }
    localStorage.setItem('watchlist-symbols', JSON.stringify(raw))
    const { state, wasCorrupt } = await loadWatchlist()
    expect(wasCorrupt).toBe(false)
    expect(state.groups).toHaveLength(1)
    expect(state.groups[0].name).toBe('Good')
    expect(state.ungrouped).toEqual(['SPY'])
  })
})

// ---------------------------------------------------------------------------
// saveWatchlist — writes localStorage mirror and POSTs to API
// ---------------------------------------------------------------------------
describe('saveWatchlist', () => {
  it('writes state to localStorage immediately', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true } as Response)
    const state: WatchlistState = { groups: [], ungrouped: ['AAPL'] }
    await saveWatchlist(state)
    const stored = JSON.parse(localStorage.getItem('watchlist-symbols')!)
    expect(stored.ungrouped).toEqual(['AAPL'])
    fetchSpy.mockRestore()
  })

  it('POSTs to /watchlist with JSON body', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true } as Response)
    const state: WatchlistState = { groups: [], ungrouped: ['MSFT'] }
    await saveWatchlist(state)
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/watchlist'),
      expect.objectContaining({ method: 'POST' })
    )
    fetchSpy.mockRestore()
  })

  it('does not throw when fetch fails (network error)', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
    const state: WatchlistState = { groups: [], ungrouped: ['SPY'] }
    await expect(saveWatchlist(state)).resolves.toBeUndefined()
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
