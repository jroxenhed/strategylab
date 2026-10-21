/**
 * BotCard (F453 item 6, F454): the "Graph ↗" button on a graph bot's tag,
 * and the "Rules" button that shows the bot's strategy as plain text.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, act } from '@testing-library/react'
import { createElement } from 'react'
import type { BotSummary } from '../../../shared/types'
import type { DescribeResponse } from '../../../api/describe'

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn(() => ({
    addSeries: vi.fn(() => ({ setData: vi.fn(), applyOptions: vi.fn(), priceScale: vi.fn(() => ({ width: vi.fn(() => 50) })) })),
    timeScale: vi.fn(() => ({
      fitContent: vi.fn(), scrollToPosition: vi.fn(), setVisibleLogicalRange: vi.fn(),
      subscribeVisibleLogicalRangeChange: vi.fn(), unsubscribeVisibleLogicalRangeChange: vi.fn(), applyOptions: vi.fn(),
    })),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    applyOptions: vi.fn(), remove: vi.fn(), subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(), resize: vi.fn(),
  })),
  BaselineSeries: 'BaselineSeries',
  ColorType: { Solid: 'Solid' },
}))

vi.mock('../../../api/bots', () => ({
  fetchBotDetail: vi.fn(() => Promise.resolve({ config: {}, state: { status: 'stopped', trades_count: 0, equity_snapshots: [], activity_log: [] } })),
}))

const describeBot = vi.fn()
vi.mock('../../../api/describe', async importOriginal => {
  const real = await importOriginal<typeof import('../../../api/describe')>()
  return { ...real, describeBot: (...args: unknown[]) => describeBot(...args) }
})

if (typeof globalThis.ResizeObserver === 'undefined') {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

const { default: BotCard } = await import('../BotCard')
const { OPEN_GRAPH_EVENT } = await import('../../nodebuilder/graphLinks')

function makeSummary(overrides: Partial<BotSummary> = {}): BotSummary {
  return {
    bot_id: 'bot-1',
    strategy_name: 'TestStrat',
    symbol: 'SPY',
    interval: '5m',
    allocated_capital: 1000,
    status: 'stopped',
    trades_count: 0,
    total_pnl: 0,
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

const GRAPH_BOT: Partial<BotSummary> = {
  kind: 'graph', graph_id: 'g-123', graph_group: null, graph_name: 'RSI dip', graph_rev: 2, graph_latest_rev: 2,
}

function renderCard(overrides: Partial<BotSummary> = {}, compact = false) {
  const noop = vi.fn()
  return render(createElement(BotCard, {
    summary: makeSummary(overrides),
    onStart: noop, onStop: noop, onBacktest: noop, onDelete: noop, onManualBuy: noop, onUpdate: noop, onResetPnl: noop,
    compact,
    adaptiveInterval: (ms: number) => ms,
  }))
}

const TEXT: DescribeResponse = {
  groups: [],
  lines: ['Buy SPY (5m) when RSI(14) is below 40.', 'Sell when RSI(14) is above 60.', 'Stop: 0.5 % below the entry price.'],
  warnings: [],
}

afterEach(() => {
  cleanup()
  describeBot.mockReset()
})

describe('BotCard Graph ↗ button (F453 item 6)', () => {
  it('opens the bot graph the same way the name link does', () => {
    const seen: unknown[] = []
    const on = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener(OPEN_GRAPH_EVENT, on)
    try {
      renderCard({ ...GRAPH_BOT, graph_group: 'long_leg' })
      const btn = screen.getByTestId('botcard-graph-open')
      expect(btn.textContent).toBe('Graph ↗')
      expect(screen.queryByText('via Graph')).toBeNull()
      fireEvent.click(btn)
      fireEvent.click(screen.getByRole('link', { name: /Open graph RSI dip at group long_leg/ }))
    } finally {
      window.removeEventListener(OPEN_GRAPH_EVENT, on)
    }
    expect(seen).toEqual([
      { graphId: 'g-123', group: 'long_leg', spawn: false },
      { graphId: 'g-123', group: 'long_leg', spawn: false },
    ])
  })

  it('stays a plain "via Graph" tag when there is no graph to open', () => {
    renderCard({ ...GRAPH_BOT, graph_latest_rev: null })
    expect(screen.queryByTestId('botcard-graph-open')).toBeNull()
    expect(screen.getByText('via Graph')).toBeInTheDocument()
    cleanup()
    renderCard({ ...GRAPH_BOT, graph_id: null })
    expect(screen.queryByTestId('botcard-graph-open')).toBeNull()
    expect(screen.getByText('via Graph')).toBeInTheDocument()
  })

  it('is a styled button with hover and focus styles (UX-6)', () => {
    renderCard(GRAPH_BOT)
    const btn = screen.getByTestId('botcard-graph-open')
    expect(btn.tagName).toBe('BUTTON')
    expect(btn.className).toBe('botcard-graph-open')
  })

  it('the compact card has the same button; a deleted graph shows none there (UX-6)', () => {
    const seen: unknown[] = []
    const on = (e: Event) => seen.push((e as CustomEvent).detail)
    window.addEventListener(OPEN_GRAPH_EVENT, on)
    try {
      renderCard(GRAPH_BOT, true)
      const btn = screen.getByTestId('botcard-compact-graph-open')
      expect(btn.textContent).toBe('Graph ↗')
      expect(btn.className).toBe('botcard-graph-open')
      fireEvent.click(btn)
    } finally {
      window.removeEventListener(OPEN_GRAPH_EVENT, on)
    }
    expect(seen).toEqual([{ graphId: 'g-123', group: null, spawn: false }])
    cleanup()
    renderCard({ ...GRAPH_BOT, graph_latest_rev: null }, true)
    expect(screen.queryByTestId('botcard-compact-graph-open')).toBeNull()
    expect(screen.queryByText('via Graph')).toBeNull()
  })

  it('a rule bot has no graph tag', () => {
    renderCard()
    expect(screen.queryByTestId('botcard-graph-open')).toBeNull()
    expect(screen.queryByText('via Graph')).toBeNull()
  })
})

describe('BotCard Rules button (F454)', () => {
  it('loads the rules on click and shows one line per sentence', async () => {
    describeBot.mockResolvedValue({ ...TEXT, warnings: ["Node 'wr1' runs Python code; the text shows only its name."] })
    renderCard(GRAPH_BOT)
    expect(describeBot).not.toHaveBeenCalled()
    expect(screen.queryByTestId('botcard-rules')).toBeNull()
    await act(async () => { fireEvent.click(screen.getByTestId('botcard-rules-btn')) })
    expect(describeBot).toHaveBeenCalledTimes(1)
    expect(describeBot.mock.calls[0][0]).toBe('bot-1')
    expect(screen.getAllByTestId('botcard-rules-line').map(e => e.textContent)).toEqual(TEXT.lines)
    expect(screen.getAllByTestId('botcard-rules-warning')).toHaveLength(1)
    // UX-7: open, the button says what a click does, and points at the panel.
    const btn = screen.getByTestId('botcard-rules-btn')
    expect(btn.textContent).toBe('Hide Rules')
    expect(btn.getAttribute('aria-controls')).toBe(screen.getByTestId('botcard-rules').id)
    // Clicking again closes it.
    fireEvent.click(btn)
    expect(screen.queryByTestId('botcard-rules')).toBeNull()
    expect(screen.getByTestId('botcard-rules-btn').textContent).toBe('Rules')
    expect(screen.getByTestId('botcard-rules-btn').getAttribute('aria-controls')).toBeNull()
  })

  it('is on rule bots too', async () => {
    describeBot.mockResolvedValue({ groups: [], lines: ['Buy AAPL (1d) when RSI(14) is below 30.'], warnings: [] })
    renderCard({ symbol: 'AAPL', interval: '1d' })
    await act(async () => { fireEvent.click(screen.getByTestId('botcard-rules-btn')) })
    expect(screen.getByTestId('botcard-rules-line').textContent).toBe('Buy AAPL (1d) when RSI(14) is below 30.')
  })

  it('a 404 reads "Bot not found." on the bot card (IN-2)', async () => {
    const { AxiosError } = await import('axios')
    const err = new AxiosError('Request failed')
    err.response = { status: 404, data: { detail: 'Not Found' }, statusText: '', headers: {}, config: {} } as never
    describeBot.mockRejectedValue(err)
    renderCard(GRAPH_BOT)
    await act(async () => { fireEvent.click(screen.getByTestId('botcard-rules-btn')) })
    expect(screen.getByTestId('botcard-rules-error').textContent).toBe('Could not show the rules. Bot not found.')
  })

  it('shows a plain error when the load fails', async () => {
    describeBot.mockRejectedValue(new Error('The bot has no graph.'))
    renderCard(GRAPH_BOT)
    await act(async () => { fireEvent.click(screen.getByTestId('botcard-rules-btn')) })
    expect(screen.getByTestId('botcard-rules-error').textContent).toBe('Could not show the rules. The bot has no graph.')
  })

  it('cancels a load still on its way when closed', async () => {
    describeBot.mockReturnValue(new Promise(() => {}))
    renderCard(GRAPH_BOT)
    await act(async () => { fireEvent.click(screen.getByTestId('botcard-rules-btn')) })
    const signal = describeBot.mock.calls[0][1] as AbortSignal
    expect(screen.getByText('Loading the rules…')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Close rules' }))
    expect(signal.aborted).toBe(true)
    expect(screen.queryByTestId('botcard-rules')).toBeNull()
  })

  it('the compact card has Rules in its menu', async () => {
    describeBot.mockResolvedValue(TEXT)
    renderCard(GRAPH_BOT, true)
    fireEvent.click(screen.getByTitle('Actions'))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Rules' })) })
    expect(describeBot).toHaveBeenCalledTimes(1)
    expect(screen.getAllByTestId('botcard-rules-line')).toHaveLength(3)
  })
})
