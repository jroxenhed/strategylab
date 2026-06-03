/**
 * Tests for StrategyComparison — F249b
 *
 * Verifies the lightweight-charts v5 mandated pattern:
 *   createChart(el, { autoSize: true, NO width, NO height })
 * Pairing v5 with explicit width+height causes a 60Hz repaint loop (F218).
 *
 * Mocks:
 *   - lightweight-charts        → stub capturing constructor options
 *   - ../../api/client          → api.post stub returning a minimal BacktestResult
 *   - ./savedStrategies         → loadSavedStrategies returns one saved strategy
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'
import { createElement } from 'react'
import { createChart } from 'lightweight-charts'

// ---------------------------------------------------------------------------
// Mock: lightweight-charts — stub that captures constructor options
// ---------------------------------------------------------------------------

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn((_container: HTMLElement, _opts?: Record<string, unknown>) => ({
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
  LineSeries: 'LineSeries',
  LineStyle: { Dashed: 1 },
  ColorType: { Solid: 'Solid' },
}))

// ---------------------------------------------------------------------------
// Mock: api client — api.post returns a minimal BacktestResult
// ---------------------------------------------------------------------------

const minimalBacktestResult = {
  summary: {
    total_return_pct: 10,
    sharpe_ratio: 1.2,
    win_rate_pct: 55,
    num_trades: 20,
    max_drawdown_pct: -8,
    profit_factor: 1.5,
    ev_per_trade: 50,
    buy_hold_return_pct: 8,
    final_value: 11000,
  },
  equity_curve: [
    { time: '2022-01-03', value: 10000 },
    { time: '2022-06-30', value: 11000 },
  ],
  baseline_curve: [
    { time: '2022-01-03', value: 10000 },
    { time: '2022-06-30', value: 10800 },
  ],
  trades: [],
}

const mockApiPost = vi.fn().mockResolvedValue({ data: minimalBacktestResult })

vi.mock('../../api/client', () => ({
  api: {
    post: (...args: unknown[]) => mockApiPost(...args),
    defaults: { baseURL: '' },
  },
}))

// ---------------------------------------------------------------------------
// Mock: savedStrategies — return one minimal saved strategy
// ---------------------------------------------------------------------------

const mockStrategy = {
  name: 'Test Strategy',
  savedAt: '2022-01-01',
  buyRules: [],
  sellRules: [],
  buyLogic: 'AND' as const,
  sellLogic: 'AND' as const,
  capital: 10000,
  posSize: 100,
  stopLoss: '' as const,
  trailingEnabled: false,
  trailingConfig: { pct: 5, activate_on_profit: false, activate_pct: 0 },
  dynamicSizing: { enabled: false, target_vol: 0.15, lookback: 20, min_size: 0.1, max_size: 1.0 },
  tradingHours: { enabled: false, start: '09:30', end: '16:00' },
  slippageBps: 2 as const,
  commission: '' as const,
  direction: 'long' as const,
}

vi.mock('./savedStrategies', () => ({
  loadSavedStrategies: vi.fn().mockResolvedValue([mockStrategy]),
}))

// ResizeObserver stub is in src/test/setup.ts (shared vitest setup file).

// ---------------------------------------------------------------------------
// Dynamic import (after mocks are hoisted)
// ---------------------------------------------------------------------------

const { default: StrategyComparison } = await import('./StrategyComparison')

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderComparison() {
  return render(
    createElement(StrategyComparison, {
      ticker: 'AAPL',
      start: '2022-01-01',
      end: '2022-12-31',
      interval: '1d',
      dataSource: 'yahoo' as const,
      capital: 10000,
    }),
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('StrategyComparison — createChart options (F249b)', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    try { localStorage.clear() } catch { /* jsdom */ }
  })

  it('initialises createChart with autoSize: true and no width/height', async () => {
    renderComparison()

    // Wait for loadSavedStrategies to resolve and render the strategy checkbox
    const checkbox = await screen.findByRole('checkbox', { name: /Test Strategy/i })
    expect(checkbox).toBeInTheDocument()

    // Select the strategy and click Run Comparison
    await act(async () => {
      checkbox.click()
    })
    const runBtn = screen.getByRole('button', { name: /run comparison/i })
    await act(async () => {
      runBtn.click()
    })

    // Wait for results to appear (summary metric "Return %" renders after data loads)
    await screen.findByText('Return %')

    const mockCreateChart = vi.mocked(createChart)
    expect(mockCreateChart).toHaveBeenCalled()

    for (const [, opts] of mockCreateChart.mock.calls) {
      const options = opts as Record<string, unknown> | undefined
      expect(options?.autoSize).toBe(true)
      expect(options).not.toHaveProperty('width')
      expect(options).not.toHaveProperty('height')
    }
  })

  it('never calls chart.applyOptions with a width or height property', async () => {
    renderComparison()

    const checkbox = await screen.findByRole('checkbox', { name: /Test Strategy/i })
    await act(async () => { checkbox.click() })
    const runBtn = screen.getByRole('button', { name: /run comparison/i })
    await act(async () => { runBtn.click() })
    await screen.findByText('Return %')

    const mockCreateChart = vi.mocked(createChart)
    const allInstances = mockCreateChart.mock.results
      .filter(r => r.type === 'return')
      .map(r => r.value as ReturnType<typeof createChart>)

    for (const inst of allInstances) {
      const applyOpts = inst.applyOptions as ReturnType<typeof vi.fn>
      for (const [callArg] of applyOpts.mock.calls) {
        const arg = callArg as Record<string, unknown> | undefined
        expect(arg).not.toHaveProperty('width')
        expect(arg).not.toHaveProperty('height')
      }
    }
  })
})
