/**
 * F435 Wave 5 review fixes, frontend (fixer C): the Spawn dialog, the bot
 * card, AddBotBar and the Monte Carlo tab.
 *
 * - FE-04 / UX-06: a weight-0 leg starts unchecked with no capital.
 * - UX-07: the implicit `main` row shows the sidebar direction it sends.
 * - UX-08: plain Enter in a field never creates bots; Cmd/Ctrl+Enter does,
 *   from anywhere in the dialog (the Cancel button included).
 * - LM-5: the spawn body carries the sidebar gates the graph backtest used.
 * - reference_unavailable reads as plain copy with Retry.
 * - FE-08: a cancelled "Spawn…" link leaves no pending request behind.
 * - UX-05: a compact graph bot card shows the rev hint and the graph line.
 * - UX-18: a regime_switch group posts direction long, never the hidden select.
 * - FE-07: a Monte Carlo answer for another group tab is dropped.
 *
 * Money safety: the HTTP client is mocked; nothing here starts a bot.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../../../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
vi.mock('../../trading/MiniSparkline', () => ({ default: () => null }))
vi.mock('../../trading/DailyPnlChart', () => ({ default: () => null }))
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
vi.mock('../../strategy/OptimizerPanel', () => ({ default: () => null }))
vi.mock('../../strategy/WalkForwardPanel', () => ({ default: () => null }))
vi.mock('../../strategy/SensitivityPanel', () => ({ default: () => null }))
vi.mock('../../strategy/MonteCarloChart', () => ({ default: () => <div data-testid="mc-chart" /> }))

import { api } from '../../../api/client'
import type { Graph, GraphBacktestResult, GraphNode } from '../../../api/nodebuilder'
import type { BotSummary } from '../../../shared/types'
import type { GroupResult, Trade } from '../../../shared/types/strategy'
import { graphActionError } from '../../../api/graphSpawn'
import { SpawnBotsDialog, type SpawnBotsDialogProps } from '../SpawnBotsDialog'
import { clearPendingSpawn, resetSpawnUi, spawnErrorView, useSpawnUi } from '../spawnUi'
import { listGraphGroups } from '../graphGroups'
import BotCard from '../../trading/BotCard'
import AddBotBar from '../../trading/AddBotBar'
import Results, { type ResultsTab } from '../../strategy/Results'

const get = vi.mocked(api.get)
const post = vi.mocked(api.post)

function node(id: string, type: string, name: string, params: GraphNode['params'], parent: string | null = null): GraphNode {
  return { id, type, name, parent, params, position: [0, 0], display: false, bypass: false }
}

function graphOf(nodes: GraphNode[]): Graph {
  return {
    _version: 3, stream_schema: 1, readOnly: false, meta: {},
    nodes: Object.fromEntries(nodes.map(n => [n.id, n])),
    wires: [], annotations: { boxes: [], notes: [] },
  }
}

function pairGraph(weight2 = 1): Graph {
  return graphOf([
    node('t1', 'ticker', 'aapl', { symbol: 'AAPL', interval: '1d' }),
    node('t2', 'ticker', 'msft', { symbol: 'MSFT', interval: '1d' }),
    node('g1', 'output_group', 'long_leg', { direction: 'long', ticker: '/aapl', capital_weight: 1 }),
    node('g2', 'output_group', 'short_leg', { direction: 'short', ticker: '/msft', capital_weight: weight2 }),
  ])
}

function implicitGraph(): Graph {
  return graphOf([
    node('t1', 'ticker', 'aapl', { symbol: 'AAPL', interval: '1d' }),
    node('e1', 'entry', 'entry', { signal: null }),
  ])
}

function dialogProps(over: Partial<SpawnBotsDialogProps> = {}): SpawnBotsDialogProps {
  return {
    graphId: 'g_pair', graphName: 'pair', rev: 7,
    groups: listGraphGroups(pairGraph()),
    dirty: false, errorCount: 0, initialCapital: 10000,
    onClose: vi.fn(), onSaveNow: vi.fn(), onReload: vi.fn(), onShowDiagnostics: vi.fn(), onCreated: vi.fn(),
    ...over,
  }
}

function spawnOk() {
  return { status: 201, data: { bots: [{ bot_id: 'b_1', group: 'long_leg', symbol: 'AAPL', direction: 'long', running: false }] } }
}

beforeEach(() => {
  get.mockReset()
  post.mockReset()
  try { localStorage.clear() } catch { /* ignore */ }
  resetSpawnUi()
})

afterEach(() => {
  cleanup()
  resetSpawnUi()
})

// ---------------------------------------------------------------------------
// The Spawn dialog
// ---------------------------------------------------------------------------

describe('Spawn dialog fixes', () => {
  it('FE-04: a weight-0 leg starts unchecked and gets no capital', async () => {
    post.mockResolvedValue(spawnOk())
    render(<SpawnBotsDialog {...dialogProps({ groups: listGraphGroups(pairGraph(0)) })} />)
    const zero = screen.getByLabelText('Include short_leg') as HTMLInputElement
    expect(zero.checked).toBe(false)
    expect((screen.getByLabelText('Capital for short_leg') as HTMLInputElement).value).toBe('0')
    expect((screen.getByLabelText('Capital for long_leg') as HTMLInputElement).value).toBe('10 000')
    fireEvent.click(screen.getByTestId('nb-dialog-primary'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    const body = post.mock.calls[0][1] as { legs: Array<{ group: string }> }
    expect(body.legs.map(l => l.group)).toEqual(['long_leg'])
  })

  it('UX-07: the implicit main row shows the sidebar direction it sends', () => {
    render(<SpawnBotsDialog {...dialogProps({ groups: listGraphGroups(implicitGraph()), implicitDirection: 'short' })} />)
    expect(screen.getByTestId('nb-spawn-row-main').textContent).toContain('SHORT')
  })

  it('UX-08: plain Enter in a capital field never creates bots', () => {
    render(<SpawnBotsDialog {...dialogProps()} />)
    const field = screen.getByLabelText('Capital for long_leg')
    field.focus()
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(post).not.toHaveBeenCalled()
  })

  it('UX-08: Cmd+Enter creates bots from a field and from the Cancel button (which is not clicked)', async () => {
    post.mockResolvedValue(spawnOk())
    const props = dialogProps()
    const { unmount } = render(<SpawnBotsDialog {...props} />)
    const cancel = screen.getByTestId('nb-dialog-cancel')
    cancel.focus()
    fireEvent.keyDown(cancel, { key: 'Enter', metaKey: true })
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(props.onClose).not.toHaveBeenCalled()
    unmount()
    post.mockClear()
    render(<SpawnBotsDialog {...dialogProps()} />)
    fireEvent.keyDown(screen.getByLabelText('Capital for long_leg'), { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
  })

  it('LM-5: the spawn body carries the sidebar gates, once for all legs', async () => {
    post.mockResolvedValue(spawnOk())
    const gates = {
      trading_hours: { enabled: true, start_time: '10:00', end_time: '15:30', skip_ranges: [] },
      skip_after_stop: { enabled: true, count: 2, trigger: 'sl' },
      dynamic_sizing: { enabled: true, consec_sls: 2, reduced_pct: 25, trigger: 'sl' },
    }
    render(<SpawnBotsDialog {...dialogProps({ gates })} />)
    fireEvent.click(screen.getByTestId('nb-dialog-primary'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    const body = post.mock.calls[0][1] as Record<string, unknown> & { legs: Array<Record<string, unknown>> }
    expect(body).toMatchObject(gates)
    for (const leg of body.legs) {
      expect(leg).not.toHaveProperty('trading_hours')
      expect(leg).not.toHaveProperty('skip_after_stop')
      expect(leg).not.toHaveProperty('dynamic_sizing')
    }
  })

  it('LM-5: no gates set sends none', async () => {
    post.mockResolvedValue(spawnOk())
    render(<SpawnBotsDialog {...dialogProps()} />)
    fireEvent.click(screen.getByTestId('nb-dialog-primary'))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(Object.keys(post.mock.calls[0][1] as object).sort()).toEqual(['legs', 'rev'])
  })

  it('reference_unavailable reads as plain copy with Retry and marks the row', async () => {
    post.mockRejectedValue({ response: { status: 400, data: { detail: {
      code: 'reference_unavailable', message: 'reference SPY unavailable on alpaca-iex', group: 'long_leg', symbol: 'spy', data_source: 'alpaca-iex',
    } } } })
    render(<SpawnBotsDialog {...dialogProps()} />)
    fireEvent.click(screen.getByTestId('nb-dialog-primary'))
    const banner = await screen.findByTestId('nb-spawn-error')
    expect(banner.textContent).toContain('Could not load the reference ticker SPY from Alpaca IEX. Pick another data source for long_leg, or try again later.')
    expect(screen.getByTestId('nb-spawn-retry')).toBeTruthy()
    expect(screen.getByTestId('nb-spawn-row-long_leg').getAttribute('data-marked')).toBe('true')
    // With no detail fields it still reads plainly.
    const bare = spawnErrorView(graphActionError({ response: { status: 400, data: { detail: { code: 'reference_unavailable' } } } }))
    expect(bare).toEqual({ text: 'Could not load a reference ticker. Pick another data source, or try again later.', action: 'retry' })
  })
})

describe('FE-08: a cancelled Spawn… link leaves nothing pending', () => {
  it('clearPendingSpawn drops only that graph', () => {
    useSpawnUi.setState({ pendingGraphId: 'g_a' })
    clearPendingSpawn('g_b')
    expect(useSpawnUi.getState().pendingGraphId).toBe('g_a')
    clearPendingSpawn('g_a')
    expect(useSpawnUi.getState().pendingGraphId).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// BotCard compact (UX-05)
// ---------------------------------------------------------------------------

function summary(over: Partial<BotSummary> = {}): BotSummary {
  return {
    bot_id: 'b1', strategy_name: 'pair ▸ long_leg', symbol: 'AAPL', interval: '15m', allocated_capital: 5000,
    status: 'stopped', trades_count: 0, total_pnl: 0, backtest_summary: null, has_position: false,
    direction: 'long', broker: 'alpaca', kind: 'graph', graph_id: 'g_pair', graph_name: 'pair',
    graph_group: 'long_leg', graph_rev: 7, graph_latest_rev: 9,
    ...over,
  }
}

describe('UX-05: the compact bot card shows the graph line', () => {
  it('shows rev 7 → 9 in the row and the graph line with Update when expanded', () => {
    const { container } = render(
      <BotCard
        summary={summary()} compact
        onStart={vi.fn()} onStop={vi.fn()} onBacktest={vi.fn()} onDelete={vi.fn()}
        onManualBuy={vi.fn()} onUpdate={vi.fn()} onResetPnl={vi.fn()}
        adaptiveInterval={ms => ms}
      />,
    )
    expect(screen.getByTestId('botcard-compact-rev').textContent).toBe('rev 7 → 9')
    expect(screen.queryByTestId('botcard-graph-line')).toBeNull()
    // Expand: a click on the compact row.
    fireEvent.click(screen.getByText('AAPL'))
    expect(screen.getByTestId('botcard-graph-line')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Update to rev 9' })).toBeTruthy()
    expect(container).toBeTruthy()
  })

  it('a compact card on the latest rev shows no rev hint', () => {
    render(
      <BotCard
        summary={summary({ graph_latest_rev: 7 })} compact
        onStart={vi.fn()} onStop={vi.fn()} onBacktest={vi.fn()} onDelete={vi.fn()}
        onManualBuy={vi.fn()} onUpdate={vi.fn()} onResetPnl={vi.fn()}
        adaptiveInterval={ms => ms}
      />,
    )
    expect(screen.queryByTestId('botcard-compact-rev')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// AddBotBar (UX-18)
// ---------------------------------------------------------------------------

describe('UX-18: AddBotBar posts long for a regime_switch group', () => {
  it('the hidden direction select never reaches the POST', async () => {
    const graph = graphOf([
      node('t1', 'ticker', 'aapl', { symbol: 'AAPL', interval: '1d' }),
      node('g1', 'output_group', 'switch_leg', { direction: 'regime_switch', ticker: '/aapl', capital_weight: 1 }),
    ])
    get.mockImplementation(async (url: string) => {
      if (url === '/api/graphs') return { data: { graphs: [{ id: 'g_sw', rev: 2, name: 'sw', description: '', updated_at: 'u', node_count: 2, groups: [] }] } }
      if (url === '/api/graphs/g_sw') return { data: { id: 'g_sw', rev: 2, name: 'sw', description: '', created_at: 'c', updated_at: 'u', graph } }
      throw new Error(`unexpected GET ${url}`)
    })
    const onAdd = vi.fn().mockResolvedValue(undefined)
    render(<AddBotBar fund={{ bot_fund: 10000, allocated: 0, available: 10000 }} onAdd={onAdd} />)
    // The user had picked short in rule mode before switching to the graph.
    fireEvent.change(screen.getByTestId('addbot-direction'), { target: { value: 'short' } })
    fireEvent.click(screen.getByLabelText('Graph', { selector: 'input[type="radio"]' }))
    const select = await screen.findByTestId('addbot-graph-select')
    await waitFor(() => expect((select as HTMLSelectElement).disabled).toBe(false))
    fireEvent.change(select, { target: { value: 'g_sw' } })
    await waitFor(() => expect((screen.getByTestId('addbot-symbol') as HTMLInputElement).value).toBe('AAPL'))
    fireEvent.change(screen.getByPlaceholderText('Allocation $'), { target: { value: '2500' } })
    fireEvent.click(screen.getByTestId('addbot-add'))
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1))
    expect(onAdd.mock.calls[0][0]).toMatchObject({ kind: 'graph', graph_group: 'switch_leg', direction: 'long' })
  })
})

// ---------------------------------------------------------------------------
// Monte Carlo (FE-07)
// ---------------------------------------------------------------------------

function trade(type: Trade['type'], date: string, price: number, pnl?: number): Trade {
  return { type, date, price, shares: 10, ...(pnl !== undefined ? { pnl, pnl_pct: pnl / 10 } : {}) }
}

function group(name: string, direction: GroupResult['direction'], symbol: string): GroupResult {
  const exit = direction === 'short' ? 'cover' : 'sell'
  const entry = direction === 'short' ? 'short' : 'buy'
  const trades = [
    trade(entry, '2024-01-02', 100), trade(exit, '2024-01-10', 110, 100),
    trade(entry, '2024-02-02', 105), trade(exit, '2024-02-10', 103, -20),
  ]
  return {
    name, node_id: `n_${name}`, path: `/${name}`, symbol, interval: '1d', direction, weight: 1, capital: 5000,
    summary: {
      initial_capital: 5000, final_value: 5080, total_return_pct: 1.6, buy_hold_return_pct: 3,
      num_trades: 2, win_rate_pct: 50, sharpe_ratio: 1.1, max_drawdown_pct: -4, open_position: null, exit_connected: true,
    },
    trades,
    equity_curve: [{ time: '2024-01-02', value: 5000 }, { time: '2024-01-03', value: 5080 }],
  }
}

function pairResponse(): GraphBacktestResult {
  return {
    summary: {}, trades: [], equity_curve: [{ time: '2024-01-02', value: 10000 }], baseline_curve: [], cook_id: 'ck',
    groups: [group('long_leg', 'long', 'AAPL'), group('short_leg', 'short', 'MSFT')],
    combined: {
      summary: { initial_capital: 10000, final_value: 10160, total_return_pct: 1.6, max_drawdown_pct: -4, sharpe_ratio: 1, num_trades: 4, exposure_pct: 50, gross_deployed_pct: 40 },
      equity_curve: [{ time: '2024-01-02', value: 10000 }],
    },
  } as unknown as GraphBacktestResult
}

function McHarness({ res }: { res: GraphBacktestResult }) {
  const [displayedGroup, setDisplayedGroup] = useState('long_leg')
  const [activeTab, setActiveTab] = useState<ResultsTab>('monte_carlo')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={client}>
      <Results
        result={{ summary: { initial_capital: 10000, final_value: 1, total_return_pct: 0, buy_hold_return_pct: 0, num_trades: 0, win_rate_pct: 0, sharpe_ratio: 0, max_drawdown_pct: 0 }, trades: [], equity_curve: [] }}
        activeTab={activeTab} onTabChange={setActiveTab} bucket={null} onBucketChange={() => {}}
        lastRequest={null} showBaseline={false} onShowBaselineChange={() => {}} logScale={false}
        onLogScaleChange={() => {}} viewInterval="1d" backtestInterval="1d"
        graphInfo={{ headerText: 'pair @ rev 3', stale: false }}
        graphGroups={{ response: res, displayedGroup, onSelect: setDisplayedGroup }}
      />
    </QueryClientProvider>
  )
}

describe('FE-07: Monte Carlo answers stay with their group tab', () => {
  it('an answer that lands after a group switch is dropped', async () => {
    let resolve: (v: unknown) => void = () => {}
    post.mockImplementation(() => new Promise(r => { resolve = r }))
    render(<McHarness res={pairResponse()} />)
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))
    expect(screen.getByText(/Running 1,000 simulations/)).toBeTruthy()
    fireEvent.click(screen.getByTestId('graph-group-tab-short_leg'))
    await act(async () => {
      resolve({ data: { percentiles: [], final_values: [] } })
      await Promise.resolve()
    })
    expect(screen.queryByTestId('mc-chart')).toBeNull()
    expect(screen.getByText('Run Monte Carlo (1,000 simulations)')).toBeTruthy()
  })
})
