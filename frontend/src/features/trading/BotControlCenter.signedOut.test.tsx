/**
 * The bots panel when the sign-in expires: the bot list first loads, then the
 * sign-in proxy answers 401. The panel must say "Signed out", not "Could not
 * reach bot API", and must not paint the bots as down.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, waitFor, act } from '@testing-library/react'
import { createElement } from 'react'
import { AxiosError, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { BotSummary } from '../../shared/types'

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({
      setData: vi.fn(),
      applyOptions: vi.fn(),
      priceScale: vi.fn(() => ({ width: vi.fn(() => 50) })),
    })),
    timeScale: vi.fn(() => ({
      fitContent: vi.fn(),
      scrollToPosition: vi.fn(),
      setVisibleLogicalRange: vi.fn(),
      subscribeVisibleLogicalRangeChange: vi.fn(),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
      applyOptions: vi.fn(),
    })),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
    subscribeCrosshairMove: vi.fn(),
    unsubscribeCrosshairMove: vi.fn(),
    resize: vi.fn(),
  })),
  BaselineSeries: 'BaselineSeries',
  LineSeries: 'LineSeries',
  ColorType: { Solid: 'Solid' },
}))

const { api } = await import('../../api/client')
const { isSignedOut, resetSignedOut } = await import('../../shared/auth/signedOut')
const { default: BotControlCenter } = await import('./BotControlCenter')

const bot: BotSummary = {
  bot_id: 'bot-1',
  strategy_name: 'TestStrat',
  symbol: 'AAPL',
  interval: '5m',
  allocated_capital: 1000,
  status: 'running',
  trades_count: 5,
  total_pnl: 50,
  backtest_summary: null,
  data_source: 'alpaca-iex',
  avg_cost_bps: null,
  has_position: false,
  direction: 'long',
  broker: 'alpaca',
  max_spread_bps: null,
  equity_snapshots: [],
  last_tick: '2020-01-01T00:00:00Z',
}

let expired = false
const originalAdapter = api.defaults.adapter

function respond(config: InternalAxiosRequestConfig, status: number, data: unknown): AxiosResponse {
  return { status, statusText: String(status), data, headers: {}, config } as AxiosResponse
}

const adapter = (async (config: InternalAxiosRequestConfig) => {
  if (expired) {
    throw new AxiosError('Request failed with status code 401', 'ERR_BAD_REQUEST', config, {}, respond(config, 401, '<html/>'))
  }
  if (config.url === '/api/bots') {
    return respond(config, 200, { fund: { bot_fund: 5000, allocated: 1000, available: 4000 }, bots: [bot] })
  }
  throw new AxiosError('Not found', 'ERR_BAD_REQUEST', config, {}, respond(config, 404, { detail: 'not found' }))
}) as AxiosAdapter

beforeEach(() => {
  expired = false
  api.defaults.adapter = adapter
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'))
})

afterEach(() => {
  cleanup()
  api.defaults.adapter = originalAdapter
  resetSignedOut()
  vi.restoreAllMocks()
})

describe('BotControlCenter — sign-in expired', () => {
  it('shows "Signed out" instead of an error, and keeps the bots grey, not down', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(createElement(QueryClientProvider, { client }, createElement(BotControlCenter)))
    await screen.findAllByText('AAPL', { exact: false })

    expired = true
    await act(async () => { await client.invalidateQueries({ queryKey: ['bots'] }) })

    await waitFor(() => expect(isSignedOut()).toBe(true))
    expect(await screen.findByTestId('bots-signed-out')).toBeInTheDocument()
    expect(screen.queryByText('Could not reach bot API')).toBeNull()
    expect(screen.queryByText(/network error|server error/i)).toBeNull()
    // The last known bot is still listed, with a grey (not red) heartbeat.
    const dots = screen.getAllByTitle(/^Signed out: live status not known/)
    expect(dots.length).toBeGreaterThan(0)
    for (const dot of dots) expect(dot.getAttribute('style')).not.toContain('var(--gh-red)')
    client.clear()
  })

  it('control: a non-auth failure still shows the bot API error', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    api.defaults.adapter = (async (config: InternalAxiosRequestConfig) => {
      throw new AxiosError('boom', 'ERR_BAD_RESPONSE', config, {}, respond(config, 502, {}))
    }) as AxiosAdapter
    render(createElement(QueryClientProvider, { client }, createElement(BotControlCenter)))
    expect(await screen.findByText('Could not reach bot API')).toBeInTheDocument()
    expect(isSignedOut()).toBe(false)
    expect(screen.queryByTestId('bots-signed-out')).toBeNull()
    client.clear()
  })
})
