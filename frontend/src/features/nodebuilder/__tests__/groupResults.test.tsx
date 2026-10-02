/**
 * Per-group result tabs and the Combined tab (W5 item 5.E, spec S33), and
 * the Output Group capital share text (S32a).
 *
 * The backend is built in parallel, so the response is a fixture in the
 * plan's W5 contract shape (groups + combined, legacy fields kept).
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { GraphBacktestResult } from '../../../api/nodebuilder'
import type { GroupResult, StrategyRequest, Trade } from '../../../shared/types/strategy'
import {
  COMBINED,
  capitalShareText,
  displayedGroupResult,
  effectiveGroupKey,
  groupReturnText,
  groupTabKeys,
  mergedTrades,
  stripStatusText,
  totalGroupWeight,
  weightTooltip,
} from '../groupResults'

vi.mock('lightweight-charts', () => {
  const ts = {
    subscribeVisibleLogicalRangeChange: vi.fn(), unsubscribeVisibleLogicalRangeChange: vi.fn(),
    getVisibleLogicalRange: vi.fn(() => null), setVisibleLogicalRange: vi.fn(), fitContent: vi.fn(),
    applyOptions: vi.fn(), getVisibleRange: vi.fn(() => null),
  }
  const series = () => ({ setData: vi.fn(), applyOptions: vi.fn(), priceScale: vi.fn(() => ({ applyOptions: vi.fn() })) })
  return {
    createChart: vi.fn(() => ({
      addSeries: vi.fn(() => series()), removeSeries: vi.fn(), applyOptions: vi.fn(), remove: vi.fn(),
      timeScale: vi.fn(() => ts), priceScale: vi.fn(() => ({ applyOptions: vi.fn(), width: vi.fn(() => 50) })),
      subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(),
    })),
    BaselineSeries: 'BaselineSeries', LineSeries: 'LineSeries', HistogramSeries: 'HistogramSeries',
    ColorType: { Solid: 'Solid' },
  }
})
vi.mock('../../strategy/OptimizerPanel', () => ({ default: () => <div data-testid="optimizer-stub" /> }))
vi.mock('../../strategy/WalkForwardPanel', () => ({ default: () => <div data-testid="wfa-stub" /> }))
vi.mock('../../strategy/SensitivityPanel', () => ({ default: () => <div data-testid="sensitivity-stub" /> }))

import Results, { type ResultsTab } from '../../strategy/Results'

afterEach(() => cleanup())

// ---------------------------------------------------------------------------
// Fixture (plan W5 contract)
// ---------------------------------------------------------------------------

function trade(type: Trade['type'], date: string, price: number, pnl?: number): Trade {
  return { type, date, price, shares: 10, ...(pnl !== undefined ? { pnl, pnl_pct: pnl / 10 } : {}) }
}

function group(name: string, direction: GroupResult['direction'], symbol: string, ret: number, trades: Trade[]): GroupResult {
  return {
    name, node_id: `n_${name}`, path: `/${name}`, symbol, interval: '1d', direction, weight: 1, capital: 5000,
    summary: {
      initial_capital: 5000, final_value: 5000 * (1 + ret / 100), total_return_pct: ret, buy_hold_return_pct: 3,
      num_trades: trades.filter(t => t.type === 'sell' || t.type === 'cover').length, win_rate_pct: 50,
      sharpe_ratio: 1.1, max_drawdown_pct: -4, open_position: null, exit_connected: true,
    },
    trades,
    equity_curve: [{ time: '2024-01-02', value: 5000 }, { time: '2024-01-03', value: 5000 * (1 + ret / 100) }],
  }
}

function response(groupCount = 2): GraphBacktestResult {
  const long = group('long_leg', 'long', 'AAPL', 18.2, [
    trade('buy', '2024-01-02', 100), trade('sell', '2024-01-10', 110, 100),
    trade('buy', '2024-02-02', 105), trade('sell', '2024-02-10', 103, -20),
    // Open at the end: an entry with no exit.
    trade('buy', '2024-03-02', 108),
  ])
  long.summary.open_position = { direction: 'long', entry_price: 108, unrealized_pct: 1.2 }
  const short = group('short_leg', 'short', 'MSFT', -4.1, [
    trade('short', '2024-01-05', 300), trade('cover', '2024-01-12', 310, -100),
  ])
  const groups = groupCount === 2 ? [long, short] : [long]
  return {
    summary: groupCount === 1 ? long.summary : {},
    trades: groupCount === 1 ? long.trades as unknown as GraphBacktestResult['trades'] : [],
    equity_curve: [{ time: '2024-01-02', value: 10000 }],
    baseline_curve: [],
    cook_id: 'ck_test',
    groups,
    combined: {
      summary: {
        initial_capital: 10000, final_value: 11240.5, total_return_pct: 12.4, max_drawdown_pct: -6.1,
        sharpe_ratio: 1.21, num_trades: 24, exposure_pct: 61.2, gross_deployed_pct: 48.0,
      },
      equity_curve: [{ time: '2024-01-02', value: 10000 }, { time: '2024-01-03', value: 11240.5 }],
    },
  }
}

/** A stand-in for App: holds `graphResult.displayedGroup` and renders Results. */
function Harness({ res, tab = 'summary', lastRequest = null, onState }: {
  res: GraphBacktestResult
  tab?: ResultsTab
  lastRequest?: StrategyRequest | null
  onState?: (s: { displayedGroup: string; lastRequest: StrategyRequest | null }) => void
}) {
  const [displayedGroup, setDisplayedGroup] = useState('main')
  const [activeTab, setActiveTab] = useState<ResultsTab>(tab)
  onState?.({ displayedGroup, lastRequest })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={client}>
      <Results
        result={{ summary: { initial_capital: 10000, final_value: 1, total_return_pct: 99, buy_hold_return_pct: 0, num_trades: 0, win_rate_pct: 0, sharpe_ratio: 0, max_drawdown_pct: 0 }, trades: [], equity_curve: [] }}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        bucket={null}
        onBucketChange={() => {}}
        lastRequest={null}
        showBaseline={false}
        onShowBaselineChange={() => {}}
        logScale={false}
        onLogScaleChange={() => {}}
        viewInterval="1d"
        backtestInterval="1d"
        graphInfo={{ headerText: 'pair @ rev 3', stale: false }}
        graphGroups={{ response: res, displayedGroup, onSelect: setDisplayedGroup }}
      />
    </QueryClientProvider>
  )
}

function returnTile(): string {
  const label = screen.getAllByText('Return').find(el => el.parentElement?.textContent?.startsWith('Return'))!
  return label.parentElement!.lastElementChild!.textContent ?? ''
}

// ---------------------------------------------------------------------------
// The strip
// ---------------------------------------------------------------------------

describe('group strip (S33)', () => {
  it('renders Combined, long_leg, short_leg in that order for 2 groups; none for 1 group', () => {
    render(<Harness res={response(2)} />)
    const tabs = within(screen.getByRole('tablist', { name: 'Result groups' })).getAllByRole('tab')
    expect(tabs.map(t => t.getAttribute('data-testid'))).toEqual([
      'graph-group-tab-combined', 'graph-group-tab-long_leg', 'graph-group-tab-short_leg',
    ])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    cleanup()
    render(<Harness res={response(1)} />)
    expect(screen.queryByTestId('graph-group-strip')).toBeNull()
  })

  it('clicking short_leg sets displayedGroup and Summary shows that group\'s return', () => {
    let seen = ''
    render(<Harness res={response(2)} onState={s => { seen = s.displayedGroup }} />)
    expect(returnTile()).toBe('+12.4%')
    fireEvent.click(screen.getByTestId('graph-group-tab-short_leg'))
    expect(seen).toBe('short_leg')
    expect(returnTile()).toBe('-4.1%')
    expect(screen.getByTestId('graph-group-tab-short_leg')).toHaveAttribute('aria-selected', 'true')
  })

  it('arrow keys move between pills; Home and End jump', () => {
    let seen = ''
    render(<Harness res={response(2)} onState={s => { seen = s.displayedGroup }} />)
    const list = screen.getByRole('tablist', { name: 'Result groups' })
    fireEvent.keyDown(list, { key: 'ArrowRight' })
    expect(seen).toBe('long_leg')
    fireEvent.keyDown(list, { key: 'End' })
    expect(seen).toBe('short_leg')
    fireEvent.keyDown(list, { key: 'Home' })
    expect(seen).toBe(COMBINED)
  })

  it('pills show returns with a spoken sign, the open-position dot and the status text', () => {
    render(<Harness res={response(2)} />)
    const longPill = screen.getByTestId('graph-group-tab-long_leg')
    expect(within(longPill).getByLabelText('return plus 18.2 percent').textContent).toBe('+18.2 %')
    expect(within(longPill).getByLabelText('Open position at the end of the window')).toBeTruthy()
    expect(within(screen.getByTestId('graph-group-tab-short_leg')).getByLabelText('return minus 4.1 percent').textContent).toBe('−4.1 %')
    expect(screen.getByTestId('graph-group-status').textContent).toBe('2 groups · 10 000 capital · exposure 61 %')
  })

  it('Combined Summary has Exposure 61.2 % and Gross deployed 48.0 % tiles and no B&H tile', () => {
    render(<Harness res={response(2)} />)
    expect(within(screen.getByTestId('graph-tile-Exposure')).getByText('61.2 %')).toBeTruthy()
    expect(screen.getByTestId('graph-tile-Exposure')).toHaveAttribute('title', 'Share of bars with any leg in a position')
    expect(within(screen.getByTestId('graph-tile-Gross deployed')).getByText('48.0 %')).toBeTruthy()
    expect(screen.queryByText('B&H')).toBeNull()
  })

  it('Combined trades table has a group column and one row per completed trade of every group', () => {
    render(<Harness res={response(2)} tab="trades" />)
    expect(screen.getByTestId('graph-trades-group-col').textContent).toBe('group')
    const rows = screen.getAllByTestId('graph-trade-row')
    // long_leg: 2 completed (the open entry is left out); short_leg: 1.
    expect(rows).toHaveLength(3)
    expect(rows.map(r => r.firstElementChild!.textContent).sort()).toEqual(['long_leg', 'long_leg', 'short_leg'])
  })

  it('a sparse legacy summary (multi-group response, strip not wired) renders without crashing', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <Results
          result={{ summary: {} as never, trades: [], equity_curve: [] }}
          activeTab="summary" onTabChange={() => {}} bucket={null} onBucketChange={() => {}}
          lastRequest={null} showBaseline={false} onShowBaselineChange={() => {}} logScale={false}
          onLogScaleChange={() => {}} viewInterval="1d" backtestInterval="1d"
          graphInfo={{ headerText: 'pair', stale: false }}
        />
      </QueryClientProvider>,
    )
    expect(returnTile()).toBe('0%')
  })

  it('switching tabs never touches lastRequest', () => {
    const req = { ticker: 'AAPL', start: '2024-01-01' } as unknown as StrategyRequest
    const snap = JSON.stringify(req)
    const seen: Array<StrategyRequest | null> = []
    render(<Harness res={response(2)} lastRequest={req} onState={s => { seen.push(s.lastRequest) }} />)
    fireEvent.click(screen.getByTestId('graph-group-tab-short_leg'))
    fireEvent.click(screen.getByTestId('graph-group-tab-combined'))
    expect(JSON.stringify(req)).toBe(snap)
    expect(seen.every(r => r === req)).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe('groupResults helpers', () => {
  it('tab keys and the effective tab', () => {
    const res = response(2)
    expect(groupTabKeys(res)).toEqual(['combined', 'long_leg', 'short_leg'])
    expect(effectiveGroupKey(res, 'main')).toBe('combined')
    expect(effectiveGroupKey(res, 'short_leg')).toBe('short_leg')
    expect(effectiveGroupKey(response(1), 'long_leg')).toBeNull()
    expect(groupTabKeys({})).toEqual([])
  })

  it('displayed result: a group feeds its own summary, trades and equity', () => {
    const res = response(2)
    const shown = displayedGroupResult(res, 'short_leg')!
    expect(shown.result.summary.total_return_pct).toBe(-4.1)
    expect(shown.result.trades).toBe(res.groups![1].trades)
    expect(shown.result.equity_curve).toBe(res.groups![1].equity_curve)
    const comb = displayedGroupResult(res, COMBINED)!
    expect(comb.result.equity_curve).toBe(res.combined!.equity_curve)
    expect(comb.result.summary.win_rate_pct).toBe(0)
  })

  it('merged trades drop a trailing open entry and tag each trade with its group', () => {
    const res = response(2)
    const merged = mergedTrades(res.groups!)
    expect(merged).toHaveLength(6)
    expect(merged.filter(t => t.group === 'long_leg')).toHaveLength(4)
    expect(merged.at(-1)).toMatchObject({ type: 'cover', group: 'short_leg' })
  })

  it('zero trades reads "0 trades"', () => {
    expect(groupReturnText({ num_trades: 0, total_return_pct: 0 }).text).toBe('0 trades')
  })

  it('status text without a combined result', () => {
    expect(stripStatusText(response(2).groups!, null)).toBe('2 groups · 10 000 capital')
  })
})

describe('capital share (S32a)', () => {
  it('two equal weights and 10 000 capital give 50 % · 5 000 of 10 000', () => {
    expect(capitalShareText(1, 2, 10000)).toBe('50 % · 5 000 of 10 000')
    expect(capitalShareText(1, 3, 10000)).toBe('33.3 % · 3 333 of 10 000')
  })

  it('weight tooltip and the graph weight sum', () => {
    expect(weightTooltip(1, 2)).toBe('Capital weight 1 of 2 (50 % of initial capital)')
    expect(weightTooltip(0, 2)).toBe('This group gets no capital and will not trade.')
    expect(totalGroupWeight({
      a: { type: 'output_group', params: { capital_weight: 1 } },
      b: { type: 'output_group', params: {} },
      c: { type: 'rsi', params: {} },
    })).toBe(2)
  })
})
