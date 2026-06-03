/**
 * API client smoke tests — verifies URL construction, error handling,
 * and response parsing for the axios-based client + trading API layer.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { api } from '../api/client'
import {
  fetchAccount,
  fetchPositions,
  fetchOrders,
  fetchJournal,
  fetchBroker,
  setBroker,
  fetchWatchlist,
  saveWatchlist,
} from '../api/trading'
import { seedFromLocalStorageIfAny } from '../shared/utils/seedFromLocalStorage'
import { WATCHLIST_KEY } from '../features/watchlist/watchlistStorage'

/* ── helpers ─────────────────────────────────────────────────────── */

function ok<T>(data: T) {
  return { data, status: 200, statusText: 'OK', headers: {}, config: {} as InternalAxiosRequestConfig }
}

function axiosError(status: number, message: string): AxiosError {
  const err = new Error(message) as AxiosError
  err.isAxiosError = true
  err.response = {
    data: { detail: message },
    status,
    statusText: 'Error',
    headers: {},
    config: {} as InternalAxiosRequestConfig,
  }
  return err
}

/* ── base client ────────────────────────────────────────────────── */

describe('api client base URL', () => {
  it('defaults to localhost:8000 when VITE_API_URL is unset', () => {
    // The axios instance is created at import time; baseURL is baked in.
    expect(api.defaults.baseURL).toBe('http://localhost:8000')
  })
})

/* ── trading API: fetchAccount ──────────────────────────────────── */

describe('fetchAccount', () => {
  let getSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
  })
  afterEach(() => {
    getSpy.mockRestore()
  })

  it('calls GET /api/trading/account and returns the data', async () => {
    const account = { equity: 10000, cash: 5000, buying_power: 20000 }
    getSpy.mockResolvedValueOnce(ok(account))

    const result = await fetchAccount()
    expect(getSpy).toHaveBeenCalledWith('/api/trading/account', { signal: undefined })
    expect(result).toEqual(account)
  })

  it('propagates axios errors from the server', async () => {
    getSpy.mockRejectedValueOnce(axiosError(401, 'Unauthorized'))
    await expect(fetchAccount()).rejects.toThrow('Unauthorized')
  })
})

/* ── trading API: fetchPositions ────────────────────────────────── */

describe('fetchPositions', () => {
  let getSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
  })
  afterEach(() => {
    getSpy.mockRestore()
  })

  it('sends broker param and unwraps the StaleAware envelope', async () => {
    const raw = {
      positions: [{ symbol: 'AAPL', qty: 10, side: 'long' }],
      stale_brokers: ['ibkr'],
    }
    getSpy.mockResolvedValueOnce(ok(raw))

    const result = await fetchPositions('alpaca')
    expect(getSpy).toHaveBeenCalledWith('/api/trading/positions', {
      params: { broker: 'alpaca' },
      signal: undefined,
    })
    expect(result.rows).toHaveLength(1)
    expect(result.stale_brokers).toEqual(['ibkr'])
  })

  it('defaults missing arrays to empty', async () => {
    getSpy.mockResolvedValueOnce(ok({}))
    const result = await fetchPositions()
    expect(result.rows).toEqual([])
    expect(result.stale_brokers).toEqual([])
  })
})

/* ── trading API: fetchOrders ───────────────────────────────────── */

describe('fetchOrders', () => {
  let getSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
  })
  afterEach(() => {
    getSpy.mockRestore()
  })

  it('sends broker param and unwraps the StaleAware envelope', async () => {
    const raw = {
      orders: [{ id: 'o1', symbol: 'TSLA', status: 'filled' }],
      stale_brokers: [],
    }
    getSpy.mockResolvedValueOnce(ok(raw))

    const result = await fetchOrders('all')
    expect(result.rows).toHaveLength(1)
    expect(result.rows[0].symbol).toBe('TSLA')
  })
})

/* ── trading API: fetchJournal ──────────────────────────────────── */

describe('fetchJournal', () => {
  let getSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
  })
  afterEach(() => {
    getSpy.mockRestore()
  })

  it('passes symbol as a query param when provided', async () => {
    getSpy.mockResolvedValueOnce(ok({ trades: [] }))
    await fetchJournal('AAPL', 'alpaca')
    expect(getSpy).toHaveBeenCalledWith('/api/trading/journal', {
      params: { broker: 'alpaca', symbol: 'AAPL' },
      signal: undefined,
    })
  })

  it('omits symbol param when not provided', async () => {
    getSpy.mockResolvedValueOnce(ok({ trades: [] }))
    await fetchJournal(undefined, 'all')
    expect(getSpy).toHaveBeenCalledWith('/api/trading/journal', {
      params: { broker: 'all' },
      signal: undefined,
    })
  })

  it('resolves to the { trades, total } shape', async () => {
    const trade = { symbol: 'AAPL', side: 'buy' }
    getSpy.mockResolvedValueOnce(ok({ trades: [trade], total: 5 }))
    const result = await fetchJournal(undefined, 'all')
    expect(result).toEqual({ trades: [trade], total: 5 })
  })

  it('falls back to total 0 when the response omits total', async () => {
    getSpy.mockResolvedValueOnce(ok({ trades: [] }))
    const result = await fetchJournal(undefined, 'all')
    expect(result).toEqual({ trades: [], total: 0 })
  })
})

/* ── fetchWatchlist (F284) ──────────────────────────────────────── */

describe('fetchWatchlist', () => {
  let getSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
  })
  afterEach(() => {
    getSpy.mockRestore()
  })

  it('flattens groups + ungrouped into a single deduped string[]', async () => {
    getSpy.mockResolvedValueOnce(ok({
      groups: [
        { id: 'g1', name: 'Tech', tickers: ['AAPL', 'MSFT'], collapsed: false },
      ],
      ungrouped: ['TSLA', 'NVDA'],
    }))
    const result = await fetchWatchlist()
    // groups come first, then ungrouped
    expect(result).toEqual(['AAPL', 'MSFT', 'TSLA', 'NVDA'])
  })

  it('deduplicates tickers that appear in both groups and ungrouped (first wins)', async () => {
    getSpy.mockResolvedValueOnce(ok({
      groups: [{ id: 'g1', name: 'A', tickers: ['AAPL', 'MSFT'], collapsed: false }],
      ungrouped: ['MSFT', 'SPY'],  // MSFT is a dup
    }))
    const result = await fetchWatchlist()
    expect(result).toEqual(['AAPL', 'MSFT', 'SPY'])
    expect(result.filter(t => t === 'MSFT')).toHaveLength(1)
  })

  it('returns empty array when both groups and ungrouped are empty', async () => {
    getSpy.mockResolvedValueOnce(ok({ groups: [], ungrouped: [] }))
    const result = await fetchWatchlist()
    expect(result).toEqual([])
  })

  it('handles missing groups/ungrouped keys gracefully', async () => {
    getSpy.mockResolvedValueOnce(ok({}))
    const result = await fetchWatchlist()
    expect(result).toEqual([])
  })
})

/* ── saveWatchlist (F284) ───────────────────────────────────────── */

describe('saveWatchlist', () => {
  let getSpy: ReturnType<typeof vi.spyOn>
  let postSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
    postSpy = vi.spyOn(api, 'post')
  })
  afterEach(() => {
    getSpy.mockRestore()
    postSpy.mockRestore()
  })

  it('preserves existing groups and sets ungrouped to symbols not already in a group', async () => {
    // Current backend state has one group with AAPL + MSFT
    getSpy.mockResolvedValueOnce(ok({
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL', 'MSFT'], collapsed: false }],
      ungrouped: [],
    }))
    postSpy.mockResolvedValueOnce(ok({}))

    // Scanner wants to save AAPL, MSFT, TSLA, NVDA
    await saveWatchlist(['AAPL', 'MSFT', 'TSLA', 'NVDA'])

    expect(postSpy).toHaveBeenCalledWith('/api/trading/watchlist', {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL', 'MSFT'], collapsed: false }],
      // AAPL + MSFT excluded (already in group); TSLA + NVDA go to ungrouped
      ungrouped: ['TSLA', 'NVDA'],
    })
  })

  it('is idempotent: two saves in a row produce the same result', async () => {
    const currentState = {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL'], collapsed: false }],
      ungrouped: ['SPY'],
    }
    // Both GET calls return the same state
    getSpy.mockResolvedValue(ok(currentState))
    postSpy.mockResolvedValue(ok({}))

    await saveWatchlist(['AAPL', 'SPY', 'TSLA'])
    const firstCall = postSpy.mock.calls[0][1]

    postSpy.mockClear()
    await saveWatchlist(['AAPL', 'SPY', 'TSLA'])
    const secondCall = postSpy.mock.calls[0][1]

    expect(firstCall).toEqual(secondCall)
  })

  it('throws when GET fails — aborts the save, no POST fired (K-02)', async () => {
    getSpy.mockRejectedValueOnce(new Error('offline'))

    await expect(saveWatchlist(['TSLA', 'NVDA'])).rejects.toThrow('offline')
    expect(postSpy).not.toHaveBeenCalled()
  })

  it('idempotent (server round-trip variant): second GET returns first POST result, POST body is equal (K-07)', async () => {
    // First GET: existing state before first save
    const initialState = {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL'], collapsed: false }],
      ungrouped: ['SPY'],
    }
    // Second GET: what the server would return after the first POST
    // (TSLA was added to ungrouped; groups unchanged)
    const afterFirstSave = {
      groups: [{ id: 'g1', name: 'Tech', tickers: ['AAPL'], collapsed: false }],
      ungrouped: ['SPY', 'TSLA'],
    }

    getSpy
      .mockResolvedValueOnce(ok(initialState))
      .mockResolvedValueOnce(ok(afterFirstSave))
    postSpy.mockResolvedValue(ok({}))

    // First save: adds TSLA (not yet in state)
    await saveWatchlist(['AAPL', 'SPY', 'TSLA'])
    const firstCall = postSpy.mock.calls[0][1]

    postSpy.mockClear()
    // Second save with same symbols: server now knows SPY+TSLA in ungrouped,
    // AAPL in group — POST body should be identical to first save
    await saveWatchlist(['AAPL', 'SPY', 'TSLA'])
    const secondCall = postSpy.mock.calls[0][1]

    expect(firstCall).toEqual(secondCall)
  })
})

/* ── broker API ─────────────────────────────────────────────────── */

describe('broker API', () => {
  let getSpy: ReturnType<typeof vi.spyOn>
  let putSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
    putSpy = vi.spyOn(api, 'put')
  })
  afterEach(() => {
    getSpy.mockRestore()
    putSpy.mockRestore()
  })

  it('fetchBroker returns BrokerInfo', async () => {
    const info = { active: 'alpaca', available: ['alpaca', 'ibkr'], health: {}, heartbeat_warmup: false }
    getSpy.mockResolvedValueOnce(ok(info))

    const result = await fetchBroker()
    expect(result.active).toBe('alpaca')
    expect(result.available).toContain('ibkr')
  })

  it('setBroker PUTs the new broker', async () => {
    const info = { active: 'ibkr', available: ['alpaca', 'ibkr'], health: {}, heartbeat_warmup: false }
    putSpy.mockResolvedValueOnce(ok(info))

    const result = await setBroker('ibkr')
    expect(putSpy).toHaveBeenCalledWith('/api/broker', { broker: 'ibkr' })
    expect(result.active).toBe('ibkr')
  })
})

/* ── seedFromLocalStorage normalization (DI-05b) ────────────────── */

describe('seedFromLocalStorageIfAny — legacy shape normalization', () => {
  const SEED_ATTEMPTED_KEY = 'strategylab-seed-attempted-v2'

  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('normalizes a flat string[] localStorage shape and POSTs valid WatchlistState', async () => {
    // Legacy shape: flat string[] stored in localStorage
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify(['AAPL', 'MSFT']))

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ seeded: true }),
    } as Response)

    await seedFromLocalStorageIfAny()

    // Should have POSTed to the watchlist/seed endpoint
    const watchlistCall = fetchSpy.mock.calls.find(c => String(c[0]).includes('/watchlist/seed'))
    expect(watchlistCall).toBeDefined()
    const body = JSON.parse((watchlistCall![1] as RequestInit).body as string)
    // Migrated shape: groups=[], ungrouped=[...] (not a raw string[])
    expect(body).toHaveProperty('groups')
    expect(body).toHaveProperty('ungrouped')
    expect(Array.isArray(body.ungrouped)).toBe(true)
    expect(body.ungrouped).toEqual(['AAPL', 'MSFT'])

    // Seed-attempted flag set on success
    expect(localStorage.getItem(SEED_ATTEMPTED_KEY)).toBe('1')
  })

  it('sets attempted flag (no POST) for unrecognisable legacy shape — prevents infinite 422 loop (DI-05b)', async () => {
    // Unrecognisable legacy shape that would cause a 422 from extra='forbid'
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify({ symbols: ['AAPL', 'MSFT'] }))

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ seeded: true }),
    } as Response)

    await seedFromLocalStorageIfAny()

    // Must NOT have POSTed to watchlist/seed (would have 422'd)
    const watchlistCall = fetchSpy.mock.calls.find(c => String(c[0]).includes('/watchlist/seed'))
    expect(watchlistCall).toBeUndefined()

    // But attempted flag must still be set to stop the infinite retry loop
    expect(localStorage.getItem(SEED_ATTEMPTED_KEY)).toBe('1')
  })
})
