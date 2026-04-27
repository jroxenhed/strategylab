/**
 * useOHLCV hook tests — verifies correct parameter passing, data
 * transformation, and disabled/enabled states via mocked axios.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { InternalAxiosRequestConfig } from 'axios'
import { api } from '../api/client'
import { useOHLCV, useInstanceIndicators, useProviders, useSearch } from '../shared/hooks/useOHLCV'

/* ── helpers ─────────────────────────────────────────────────────── */

function ok<T>(data: T) {
  return { data, status: 200, statusText: 'OK', headers: {}, config: {} as InternalAxiosRequestConfig }
}

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return createElement(QueryClientProvider, { client }, children)
  }
}

const SAMPLE_BARS = [
  { time: '2024-01-02', open: 100, high: 105, low: 99, close: 104, volume: 1000 },
  { time: '2024-01-03', open: 104, high: 108, low: 103, close: 107, volume: 1200 },
]

/* ── useOHLCV ────────────────────────────────────────────────────── */

describe('useOHLCV', () => {
  let getSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
  })
  afterEach(() => {
    getSpy.mockRestore()
  })

  it('fetches OHLCV data with correct URL and params', async () => {
    getSpy.mockResolvedValueOnce(ok({ data: SAMPLE_BARS }))

    const { result } = renderHook(
      () => useOHLCV('AAPL', '2024-01-01', '2024-01-31', '1d', 'yahoo', false, true),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(getSpy).toHaveBeenCalledWith('/api/ohlcv/AAPL', {
      params: {
        start: '2024-01-01',
        end: '2024-01-31',
        interval: '1d',
        source: 'yahoo',
        extended_hours: false,
      },
    })
    expect(result.current.data).toEqual(SAMPLE_BARS)
  })

  it('does not fetch when ticker is empty', async () => {
    const { result } = renderHook(
      () => useOHLCV('', '2024-01-01', '2024-01-31', '1d'),
      { wrapper: createWrapper() },
    )

    // Should stay in idle state — query never fires
    expect(result.current.fetchStatus).toBe('idle')
    expect(getSpy).not.toHaveBeenCalled()
  })

  it('does not fetch when enabled=false', async () => {
    const { result } = renderHook(
      () => useOHLCV('AAPL', '2024-01-01', '2024-01-31', '1d', 'yahoo', false, false),
      { wrapper: createWrapper() },
    )

    expect(result.current.fetchStatus).toBe('idle')
    expect(getSpy).not.toHaveBeenCalled()
  })

  it('propagates fetch errors', async () => {
    getSpy.mockRejectedValueOnce(new Error('Network Error'))

    const { result } = renderHook(
      () => useOHLCV('AAPL', '2024-01-01', '2024-01-31', '1d'),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error).toBeInstanceOf(Error)
  })
})

/* ── useInstanceIndicators ───────────────────────────────────────── */

describe('useInstanceIndicators', () => {
  let postSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    postSpy = vi.spyOn(api, 'post')
  })
  afterEach(() => {
    postSpy.mockRestore()
  })

  it('posts enabled instances and returns indicator data', async () => {
    const instances = [
      { id: 'rsi-1', type: 'rsi' as const, params: { period: 14 }, enabled: true, pane: 'sub' as const },
      { id: 'ma-1', type: 'ma' as const, params: { period: 20 }, enabled: false, pane: 'main' as const },
    ]
    const responseData = {
      'rsi-1': { rsi: [{ time: '2024-01-02', value: 55 }] },
    }
    postSpy.mockResolvedValueOnce(ok(responseData))

    const { result } = renderHook(
      () => useInstanceIndicators('AAPL', '2024-01-01', '2024-01-31', '1d', instances),
      { wrapper: createWrapper() },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    // Should only send the enabled instance
    expect(postSpy).toHaveBeenCalledWith('/api/indicators/AAPL', expect.objectContaining({
      instances: [{ id: 'rsi-1', type: 'rsi', params: { period: 14 } }],
    }))
    expect(result.current.data).toEqual(responseData)
  })

  it('does not fetch when no instances are enabled', async () => {
    const instances = [
      { id: 'rsi-1', type: 'rsi' as const, params: { period: 14 }, enabled: false, pane: 'sub' as const },
    ]

    const { result } = renderHook(
      () => useInstanceIndicators('AAPL', '2024-01-01', '2024-01-31', '1d', instances),
      { wrapper: createWrapper() },
    )

    expect(result.current.fetchStatus).toBe('idle')
    expect(postSpy).not.toHaveBeenCalled()
  })
})

/* ── useProviders ────────────────────────────────────────────────── */

describe('useProviders', () => {
  let getSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
  })
  afterEach(() => {
    getSpy.mockRestore()
  })

  it('returns the providers array', async () => {
    getSpy.mockResolvedValueOnce(ok({ providers: ['yahoo', 'alpaca'] }))

    const { result } = renderHook(() => useProviders(), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(['yahoo', 'alpaca'])
  })
})

/* ── useSearch ───────────────────────────────────────────────────── */

describe('useSearch', () => {
  let getSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    getSpy = vi.spyOn(api, 'get')
  })
  afterEach(() => {
    getSpy.mockRestore()
  })

  it('does not fetch for single-character queries', async () => {
    const { result } = renderHook(() => useSearch('A'), { wrapper: createWrapper() })
    expect(result.current.fetchStatus).toBe('idle')
    expect(getSpy).not.toHaveBeenCalled()
  })

  it('fetches for queries of 2+ characters', async () => {
    getSpy.mockResolvedValueOnce(ok([{ symbol: 'AAPL', name: 'Apple Inc.' }]))

    const { result } = renderHook(() => useSearch('AA'), { wrapper: createWrapper() })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(getSpy).toHaveBeenCalledWith('/api/search', { params: { q: 'AA' } })
  })
})
