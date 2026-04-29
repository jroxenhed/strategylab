/**
 * Tests for PortfolioStrip.
 *
 * Mocks:
 *   - lightweight-charts  →  stub (MiniSparkline uses it)
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { createElement } from 'react'
import type { BotSummary } from '../../shared/types'

// ---------------------------------------------------------------------------
// Mock: lightweight-charts (MiniSparkline dependency)
// ---------------------------------------------------------------------------

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
  ColorType: { Solid: 'Solid' },
}))

// ---------------------------------------------------------------------------
// ResizeObserver stub
// ---------------------------------------------------------------------------

if (typeof globalThis.ResizeObserver === 'undefined') {
  ;(globalThis as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// ---------------------------------------------------------------------------
// Dynamic import (after mocks are hoisted)
// ---------------------------------------------------------------------------

const { default: PortfolioStrip } = await import('./PortfolioStrip')

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeSummary(overrides: Partial<BotSummary> = {}): BotSummary {
  return {
    bot_id: 'bot-1',
    strategy_name: 'TestStrat',
    symbol: 'AAPL',
    interval: '5m',
    allocated_capital: 1000,
    status: 'stopped',
    trades_count: 0,
    total_pnl: 0,
    backtest_summary: null,
    equity_snapshots: [],
    ...overrides,
  }
}

function renderStrip(bots: BotSummary[] = [], alignedRange?: { from: number; to: number }) {
  return render(createElement(PortfolioStrip, { bots, alignedRange }))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('PortfolioStrip', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders without throwing', () => {
    expect(() => renderStrip()).not.toThrow()
  })

  it('renders the Portfolio heading', () => {
    renderStrip()
    expect(screen.getByText('Portfolio')).toBeInTheDocument()
  })

  // -- StatCell labels --

  it('renders Allocated stat label', () => {
    renderStrip()
    expect(screen.getByText('Allocated')).toBeInTheDocument()
  })

  it('renders P&L stat label', () => {
    renderStrip()
    expect(screen.getByText('P&L')).toBeInTheDocument()
  })

  it('renders Running stat label', () => {
    renderStrip()
    expect(screen.getByText('Running')).toBeInTheDocument()
  })

  it('renders Profitable stat label', () => {
    renderStrip()
    expect(screen.getByText('Profitable')).toBeInTheDocument()
  })

  // -- Empty bots array --

  it('shows $0 allocated when bots is empty', () => {
    renderStrip([])
    expect(screen.getByText('$0')).toBeInTheDocument()
  })

  it('shows 0 / 0 running when bots is empty', () => {
    renderStrip([])
    expect(screen.getByText('0 / 0')).toBeInTheDocument()
  })

  it('shows 0 / 0 bots profitable when bots is empty', () => {
    renderStrip([])
    expect(screen.getByText('0 / 0 bots')).toBeInTheDocument()
  })

  it('shows 0.0% P&L when bots is empty', () => {
    renderStrip([])
    expect(screen.getByText(/\(0\.0%\)/)).toBeInTheDocument()
  })

  // -- Aggregate stats with bots --

  it('sums allocated capital across bots', () => {
    const bots = [
      makeSummary({ bot_id: 'b1', allocated_capital: 1000 }),
      makeSummary({ bot_id: 'b2', allocated_capital: 2000 }),
    ]
    renderStrip(bots)
    expect(screen.getByText('$3,000')).toBeInTheDocument()
  })

  it('sums total P&L across bots', () => {
    const bots = [
      makeSummary({ bot_id: 'b1', allocated_capital: 1000, total_pnl: 100 }),
      makeSummary({ bot_id: 'b2', allocated_capital: 1000, total_pnl: -50 }),
    ]
    renderStrip(bots)
    // total_pnl = 50, 5% of 2000
    expect(screen.getByText(/\(2\.5%\)/)).toBeInTheDocument()
  })

  it('counts running bots correctly', () => {
    const bots = [
      makeSummary({ bot_id: 'b1', status: 'running' }),
      makeSummary({ bot_id: 'b2', status: 'running' }),
      makeSummary({ bot_id: 'b3', status: 'stopped' }),
    ]
    renderStrip(bots)
    expect(screen.getByText('2 / 3')).toBeInTheDocument()
  })

  it('counts profitable bots (total_pnl > 0)', () => {
    const bots = [
      makeSummary({ bot_id: 'b1', total_pnl: 100, trades_count: 3 }),
      makeSummary({ bot_id: 'b2', total_pnl: -20, trades_count: 1 }),
      makeSummary({ bot_id: 'b3', total_pnl: 0, trades_count: 2 }),
    ]
    renderStrip(bots)
    // profitableCount = 1, tradedCount = 3 (trades_count > 0)
    expect(screen.getByText('1 / 3 bots')).toBeInTheDocument()
  })

  it('only counts bots with trades in the denominator of Profitable', () => {
    const bots = [
      makeSummary({ bot_id: 'b1', total_pnl: 50, trades_count: 5 }),
      makeSummary({ bot_id: 'b2', total_pnl: 0, trades_count: 0 }),  // not traded
    ]
    renderStrip(bots)
    // tradedCount = 1, profitableCount = 1
    expect(screen.getByText('1 / 1 bots')).toBeInTheDocument()
  })

  it('P&L shows 0.0% when total allocated_capital is 0', () => {
    const bots = [
      makeSummary({ bot_id: 'b1', allocated_capital: 0, total_pnl: 50 }),
    ]
    renderStrip(bots)
    expect(screen.getByText(/\(0\.0%\)/)).toBeInTheDocument()
  })

  // -- Column flex layout --

  it('left column hugs content and right column fills remaining space', () => {
    const { container } = renderStrip()
    const allDivs = Array.from(container.querySelectorAll('div'))
    expect(allDivs.some(el => el.style.flex === '0 0 35%')).toBe(true)
    expect(allDivs.some(el => el.style.flex.startsWith('1'))).toBe(true)
  })
})
