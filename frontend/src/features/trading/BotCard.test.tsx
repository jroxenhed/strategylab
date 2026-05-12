/**
 * Tests for BotCard — compact variant (compact=true) and default (expanded) variant.
 *
 * Mocks:
 *   - lightweight-charts  →  stub (no canvas needed)
 *   - ../../api/bots      →  fetchBotDetail returns a resolved promise immediately
 *   - ../../shared/hooks/useOHLCV  →  useBroker returns a stable adaptiveInterval
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { BotSummary, BotConfig } from '../../shared/types'

// ---------------------------------------------------------------------------
// Mock: lightweight-charts (used by MiniSparkline)
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
// Mock: API
// ---------------------------------------------------------------------------

vi.mock('../../api/bots', () => ({
  fetchBotDetail: vi.fn(() => Promise.resolve({
    config: {},
    state: {
      status: 'stopped',
      trades_count: 0,
      equity_snapshots: [],
      activity_log: [],
    },
  })),
}))

// ---------------------------------------------------------------------------
// Mock: useBroker (avoids react-query network calls for broker endpoint)
// ---------------------------------------------------------------------------

vi.mock('../../shared/hooks/useOHLCV', () => ({
  useBroker: vi.fn(() => ({
    broker: 'alpaca',
    available: [],
    health: {},
    heartbeatWarmup: false,
    anyBrokerUnhealthy: false,
    adaptiveInterval: (ms: number) => ms,
    isLoading: false,
    switchBroker: vi.fn(),
  })),
  useOHLCV: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
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

const { default: BotCard } = await import('./BotCard')

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrapper({ children }: { children: React.ReactNode }) {
  return createElement(QueryClientProvider, { client: makeQueryClient() }, children)
}

function makeSummary(overrides: Partial<BotSummary> = {}): BotSummary {
  return {
    bot_id: 'bot-1',
    strategy_name: 'TestStrat',
    symbol: 'AAPL',
    interval: '5m',
    allocated_capital: 1000,
    status: 'stopped',
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
    ...overrides,
  }
}

const NO_OP = vi.fn()

function renderCard(summaryOverrides: Partial<BotSummary> = {}, cardProps: Record<string, unknown> = {}) {
  const summary = makeSummary(summaryOverrides)
  return render(
    createElement(BotCard, {
      summary,
      onStart: NO_OP,
      onStop: NO_OP,
      onBacktest: NO_OP,
      onDelete: NO_OP,
      onManualBuy: NO_OP,
      onUpdate: NO_OP,
      onResetPnl: NO_OP,
      compact: false,
      ...cardProps,
    }),
    { wrapper },
  )
}

function renderCompactCard(summaryOverrides: Partial<BotSummary> = {}, cardProps: Record<string, unknown> = {}) {
  return renderCard(summaryOverrides, { compact: true, ...cardProps })
}

// ---------------------------------------------------------------------------
// Tests — default (expanded) layout
// ---------------------------------------------------------------------------

describe('BotCard default layout', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders without throwing', () => {
    expect(() => renderCard()).not.toThrow()
  })

  it('displays symbol and strategy name', () => {
    renderCard()
    expect(screen.getByText('AAPL', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('TestStrat', { exact: false })).toBeInTheDocument()
  })

  // -- Stat labels --

  it('renders Allocated stat label', () => {
    renderCard()
    expect(screen.getByText('Allocated')).toBeInTheDocument()
  })

  it('renders Trades stat label', () => {
    renderCard()
    expect(screen.getByText('Trades')).toBeInTheDocument()
  })

  it('renders P&L stat label', () => {
    renderCard()
    expect(screen.getAllByText('P&L').length).toBeGreaterThanOrEqual(1)
  })

  it('renders Status stat label', () => {
    renderCard()
    expect(screen.getByText('Status')).toBeInTheDocument()
  })

  it('renders Spread cap stat label', () => {
    renderCard()
    expect(screen.getByText('Spread cap')).toBeInTheDocument()
  })

  // -- Slippage conditional --

  it('does not render Slippage stat when avg_cost_bps is null', () => {
    renderCard({ avg_cost_bps: null })
    expect(screen.queryByText('Slippage')).not.toBeInTheDocument()
  })

  it('renders Slippage stat when avg_cost_bps is provided', () => {
    renderCard({ avg_cost_bps: 3.2 })
    expect(screen.getByText('Slippage')).toBeInTheDocument()
  })

  it('shows slippage value with bps suffix', () => {
    renderCard({ avg_cost_bps: 3.2 })
    expect(screen.getByText('3.2 bps')).toBeInTheDocument()
  })

  // -- P&L division-by-zero guard --

  it('shows 0.0% when allocated_capital is 0', () => {
    renderCard({ allocated_capital: 0, total_pnl: 100 })
    // pnlPct is 0.0 when allocated_capital === 0
    expect(screen.getByText(/\(0\.0%\)/)).toBeInTheDocument()
  })

  it('computes percentage correctly when allocated_capital > 0', () => {
    renderCard({ allocated_capital: 1000, total_pnl: 100 })
    expect(screen.getByText(/\(10\.0%\)/)).toBeInTheDocument()
  })

  // -- Delete button visibility --

  it('shows Delete button when stopped', () => {
    renderCard({ status: 'stopped' })
    expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument()
  })

  it('does not show Delete button when running', () => {
    renderCard({ status: 'running' })
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
  })

  // -- Backtest disabled when running --

  it('Backtest button is disabled when running', () => {
    renderCard({ status: 'running' })
    const btn = screen.getByRole('button', { name: /backtest/i })
    expect(btn).toBeDisabled()
  })

  it('Backtest button is enabled when stopped', () => {
    renderCard({ status: 'stopped' })
    const btn = screen.getByRole('button', { name: /backtest/i })
    expect(btn).not.toBeDisabled()
  })

  // -- Buy button disabled states --

  it('Buy button is disabled when not running', () => {
    renderCard({ status: 'stopped', has_position: false })
    const btn = screen.getByRole('button', { name: /buy/i })
    expect(btn).toBeDisabled()
  })

  it('Buy button is disabled when running but has_position is true', () => {
    renderCard({ status: 'running', has_position: true })
    const btn = screen.getByRole('button', { name: /buy/i })
    expect(btn).toBeDisabled()
  })

  it('Buy button is enabled when running and no position', () => {
    renderCard({ status: 'running', has_position: false })
    const btn = screen.getByRole('button', { name: /buy/i })
    expect(btn).not.toBeDisabled()
  })

  it('shows Short label instead of Buy for short direction', () => {
    renderCard({ direction: 'short', status: 'stopped' })
    expect(screen.queryByRole('button', { name: /^buy$/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /short/i })).toBeInTheDocument()
  })

  // -- Inline Allocated edit --

  it('shows Allocated input when stopped and value is clicked', async () => {
    renderCard({ status: 'stopped', allocated_capital: 1000 })
    // Click the Allocated value span
    const allocSpan = screen.getByTitle('Click to edit')
    await userEvent.click(allocSpan)
    expect(screen.getByRole('spinbutton')).toBeInTheDocument()
  })

  it('does not show Allocated input when running and value is clicked', async () => {
    renderCard({ status: 'running', allocated_capital: 1000 })
    // No clickable title when running
    expect(screen.queryByTitle('Click to edit')).not.toBeInTheDocument()
  })

  // -- Column flex layout --

  it('left column hugs content and right column fills remaining space', () => {
    const { container } = renderCard()
    const allDivs = Array.from(container.querySelectorAll('div'))
    expect(allDivs.some(el => el.style.flex === '0 0 35%')).toBe(true)
    expect(allDivs.some(el => el.style.flex.startsWith('1'))).toBe(true)
  })

  // -- Buttons fire correct callbacks --

  it('Backtest button calls onBacktest when stopped', async () => {
    const onBacktest = vi.fn()
    renderCard({ status: 'stopped' }, { onBacktest })
    await userEvent.click(screen.getByRole('button', { name: /backtest/i }))
    expect(onBacktest).toHaveBeenCalledOnce()
  })

  it('Start button calls onStart when stopped', async () => {
    const onStart = vi.fn()
    renderCard({ status: 'stopped' }, { onStart })
    await userEvent.click(screen.getByRole('button', { name: /^start$/i }))
    expect(onStart).toHaveBeenCalledOnce()
  })

  it('Stop button calls onStop when running', async () => {
    const onStop = vi.fn()
    renderCard({ status: 'running' }, { onStop })
    await userEvent.click(screen.getByRole('button', { name: /^stop$/i }))
    expect(onStop).toHaveBeenCalledOnce()
  })

  it('Delete button calls onDelete when stopped', async () => {
    const onDelete = vi.fn()
    renderCard({ status: 'stopped' }, { onDelete })
    await userEvent.click(screen.getByRole('button', { name: /delete/i }))
    expect(onDelete).toHaveBeenCalledOnce()
  })
})

// ---------------------------------------------------------------------------
// Tests — compact layout
// ---------------------------------------------------------------------------

describe('BotCard compact layout', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders without throwing', () => {
    expect(() => renderCompactCard()).not.toThrow()
  })

  it('displays symbol in compact view', () => {
    renderCompactCard()
    expect(screen.getByText('AAPL')).toBeInTheDocument()
  })

  it('does not render default action buttons (Backtest, Start/Stop) directly', () => {
    renderCompactCard({ status: 'stopped' })
    // In compact mode, actions are behind the kebab menu — not rendered by default
    expect(screen.queryByRole('button', { name: /^start$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^stop$/i })).not.toBeInTheDocument()
  })

  // -- Kebab menu: open on click --

  it('opens kebab menu when button is clicked', async () => {
    renderCompactCard({ status: 'stopped' })
    const kebabBtn = screen.getByTitle('Actions')
    await userEvent.click(kebabBtn)
    expect(screen.getByRole('button', { name: /^start$/i })).toBeInTheDocument()
  })

  it('closes kebab menu when clicking outside', async () => {
    renderCompactCard({ status: 'stopped' })
    const kebabBtn = screen.getByTitle('Actions')
    await userEvent.click(kebabBtn)
    // Menu is open
    expect(screen.getByRole('button', { name: /^start$/i })).toBeInTheDocument()
    // Click outside
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('button', { name: /^start$/i })).not.toBeInTheDocument()
  })

  it('closes menu after clicking an action', async () => {
    const onStart = vi.fn()
    renderCompactCard({ status: 'stopped' }, { onStart })
    await userEvent.click(screen.getByTitle('Actions'))
    await userEvent.click(screen.getByRole('button', { name: /^start$/i }))
    expect(onStart).toHaveBeenCalledOnce()
    // Menu should close after action
    expect(screen.queryByRole('button', { name: /^start$/i })).not.toBeInTheDocument()
  })

  // -- Menu items based on state --

  it('shows Start in menu when stopped', async () => {
    renderCompactCard({ status: 'stopped' })
    await userEvent.click(screen.getByTitle('Actions'))
    expect(screen.getByRole('button', { name: /^start$/i })).toBeInTheDocument()
  })

  it('shows Stop in menu when running', async () => {
    renderCompactCard({ status: 'running' })
    await userEvent.click(screen.getByTitle('Actions'))
    expect(screen.getByRole('button', { name: /^stop$/i })).toBeInTheDocument()
  })

  // -- Delete only when stopped --

  it('shows Delete in menu only when stopped', async () => {
    renderCompactCard({ status: 'stopped' })
    await userEvent.click(screen.getByTitle('Actions'))
    expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument()
  })

  it('does not show Delete in menu when running', async () => {
    renderCompactCard({ status: 'running' })
    await userEvent.click(screen.getByTitle('Actions'))
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
  })

  // -- Backtest disabled when running --

  it('Backtest menu item is disabled when running', async () => {
    renderCompactCard({ status: 'running' })
    await userEvent.click(screen.getByTitle('Actions'))
    const btn = screen.getByRole('button', { name: /backtest/i })
    expect(btn).toBeDisabled()
  })

  it('Backtest menu item is enabled when stopped', async () => {
    renderCompactCard({ status: 'stopped' })
    await userEvent.click(screen.getByTitle('Actions'))
    const btn = screen.getByRole('button', { name: /backtest/i })
    expect(btn).not.toBeDisabled()
  })

  // -- Buy/Short disabled states in menu --

  it('Buy menu item is disabled when not running', async () => {
    renderCompactCard({ status: 'stopped', has_position: false })
    await userEvent.click(screen.getByTitle('Actions'))
    const btn = screen.getByRole('button', { name: /^buy$/i })
    expect(btn).toBeDisabled()
  })

  it('Buy menu item is disabled when running but has_position=true', async () => {
    renderCompactCard({ status: 'running', has_position: true })
    await userEvent.click(screen.getByTitle('Actions'))
    const btn = screen.getByRole('button', { name: /^buy$/i })
    expect(btn).toBeDisabled()
  })

  it('Buy menu item is enabled when running and no position', async () => {
    renderCompactCard({ status: 'running', has_position: false })
    await userEvent.click(screen.getByTitle('Actions'))
    const btn = screen.getByRole('button', { name: /^buy$/i })
    expect(btn).not.toBeDisabled()
  })

  it('shows Short instead of Buy in menu for short direction', async () => {
    renderCompactCard({ direction: 'short', status: 'stopped' })
    await userEvent.click(screen.getByTitle('Actions'))
    expect(screen.queryByRole('button', { name: /^buy$/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^short$/i })).toBeInTheDocument()
  })

  // -- stopPropagation: clicking kebab does NOT expand activity log --

  it('clicking kebab button does not toggle the activity log', async () => {
    renderCompactCard({ status: 'stopped' })
    // Activity log is not visible initially
    expect(screen.queryByText('No activity yet.')).not.toBeInTheDocument()
    // Click the kebab
    await userEvent.click(screen.getByTitle('Actions'))
    // Log still not expanded (menu opened, not card)
    expect(screen.queryByText('No activity yet.')).not.toBeInTheDocument()
  })

  it('clicking the menu container does not propagate to the card row', async () => {
    renderCompactCard({ status: 'stopped' })
    // The outer row toggles 'expanded'; clicking the menu wrapper must not trigger it.
    // We verify by checking the log remains absent after clicking the menu wrapper.
    const actionsBtn = screen.getByTitle('Actions')
    const menuContainer = actionsBtn.closest('div') as HTMLElement
    fireEvent.click(menuContainer)
    // Log should NOT appear (stopPropagation prevents the outer onClick).
    expect(screen.queryByText('No activity yet.')).not.toBeInTheDocument()
  })

  // -- P&L division-by-zero --

  it('shows 0.0% P&L percentage when allocated_capital is 0', () => {
    renderCompactCard({ allocated_capital: 0, total_pnl: 50 })
    expect(screen.getByText('(0.0%)')).toBeInTheDocument()
  })

  it('computes correct P&L percentage', () => {
    renderCompactCard({ allocated_capital: 500, total_pnl: 25 })
    expect(screen.getByText('(5.0%)')).toBeInTheDocument()
  })

  // -- Column flex layout --

  it('left column hugs content and right column fills remaining space', () => {
    const { container } = renderCompactCard()
    const allDivs = Array.from(container.querySelectorAll('div'))
    expect(allDivs.some(el => el.style.flex === '0 0 35%')).toBe(true)
    expect(allDivs.some(el => el.style.flex.startsWith('1'))).toBe(true)
  })

  // -- Show Log / Hide Log toggle --

  it('Show Log menu item expands the activity log', async () => {
    renderCompactCard({ status: 'stopped' })
    await userEvent.click(screen.getByTitle('Actions'))
    await userEvent.click(screen.getByRole('button', { name: /show log/i }))
    expect(screen.getByText('No activity yet.')).toBeInTheDocument()
  })

  it('menu shows Hide Log when log is expanded', async () => {
    renderCompactCard({ status: 'stopped' })
    // First open and click Show Log
    await userEvent.click(screen.getByTitle('Actions'))
    await userEvent.click(screen.getByRole('button', { name: /show log/i }))
    // Re-open menu
    await userEvent.click(screen.getByTitle('Actions'))
    expect(screen.getByRole('button', { name: /hide log/i })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// F84 — BotCard defensive path: state: undefined in fetchBotDetail response
// ---------------------------------------------------------------------------

describe('BotCard state: undefined defensive path', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders without crashing when fetchBotDetail resolves with state: undefined', async () => {
    // Override the module-level mock for this test only: state is undefined.
    // BotCard must fall back to summary.equity_snapshots, summary.activity_log, summary.last_tick.
    const minimalConfig: BotConfig = {
      bot_id: 'test-bot',
      strategy_name: 'test',
      symbol: 'SPY',
      interval: '1d',
      buy_rules: [],
      sell_rules: [],
      buy_logic: 'AND',
      sell_logic: 'AND',
      allocated_capital: 10000,
      position_size: 100,
    }
    const { fetchBotDetail } = await import('../../api/bots')
    vi.mocked(fetchBotDetail).mockResolvedValueOnce({
      config: minimalConfig,
      state: undefined,
    })

    const summary = makeSummary({
      equity_snapshots: [],
      last_tick: undefined,
    })

    expect(() =>
      render(
        createElement(BotCard, {
          summary,
          onStart: NO_OP,
          onStop: NO_OP,
          onBacktest: NO_OP,
          onDelete: NO_OP,
          onManualBuy: NO_OP,
          onUpdate: NO_OP,
          onResetPnl: NO_OP,
          compact: false,
        }),
        { wrapper },
      )
    ).not.toThrow()

    // Verify the component mounts and shows the symbol (basic smoke check)
    expect(screen.getByText('AAPL', { exact: false })).toBeInTheDocument()
  })
})
