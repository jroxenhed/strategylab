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
} from '../api/trading'

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
